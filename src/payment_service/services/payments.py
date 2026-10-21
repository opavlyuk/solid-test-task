"""Payment reservation, charging, finalization, and replay."""

from datetime import UTC, datetime
from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from payment_service.models import Payment
from payment_service.payment_status import PaymentStatus
from payment_service.services.contracts import (
    ChargeDeclined,
    ChargeRequest,
    ChargeResult,
    ChargeSucceeded,
    Money,
    PayCart,
    PayCartResult,
    PaymentError,
    PaymentGateway,
    PaymentView,
    ProviderUnavailable,
    TotalProvider,
    TotalUnavailable,
)
from payment_service.services.payment_store import PaymentStore


def _payment_view(payment: Payment) -> PaymentView:
    return PaymentView(
        payment.id,
        payment.cart_id,
        payment.payment_method_id,
        Money(payment.amount, payment.currency),
        payment.status,
    )


def _replay(payment: Payment, command: PayCart) -> PayCartResult:
    if (payment.cart_id, payment.payment_method_id) != (
        command.cart_id,
        command.payment_method_id,
    ):
        raise PaymentError("idempotency_conflict")
    return PayCartResult(_payment_view(payment), replayed=True)


class PaymentService:
    """Payment coordinator enforcing eligibility and idempotency."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        total_provider: TotalProvider,
        gateway: PaymentGateway,
    ) -> None:
        self._session_factory = session_factory
        self._total_provider = total_provider
        self._gateway = gateway
        self._store = PaymentStore()

    def pay(self, command: PayCart) -> PayCartResult:
        """Charge a reserved payment and persist its outcome, or replay it.

        Unexpected provider errors or malformed results leave the attempt pending.

        Raises:
            PaymentError: The request is ineligible or conflicts with a payment.
            TotalUnavailable: The cart total is missing or invalid.
        """
        reservation = self._reserve(command)
        if isinstance(reservation, PayCartResult):
            return reservation
        try:
            outcome = self._gateway.charge(reservation)
        except ProviderUnavailable as error:
            return self._finalize(command, reservation.payment_id, error)
        return self._finalize(command, reservation.payment_id, outcome)

    def _finalize(
        self,
        command: PayCart,
        payment_id: UUID,
        outcome: ChargeResult | ProviderUnavailable,
    ) -> PayCartResult:
        """Persist an outcome and any checkout together before returning it."""
        with self._session_factory.begin() as session:
            cart = self._store.lock_cart(session, command.cart_id, command.user_id)
            if cart is None:
                raise RuntimeError("Reserved cart disappeared")
            payment = self._store.lock_payment(session, payment_id)
            if payment.status is not PaymentStatus.PENDING:
                raise RuntimeError("Reserved payment is no longer pending")
            payment.updated_at = datetime.now(UTC)
            if isinstance(outcome, ChargeSucceeded):
                payment.status = PaymentStatus.SUCCEEDED
                payment.provider_reference = outcome.provider_reference
                cart.status = "checked_out"
                cart.updated_at = payment.updated_at
            elif isinstance(outcome, ChargeDeclined):
                payment.status = PaymentStatus.FAILED
            elif isinstance(outcome, ProviderUnavailable):
                payment.status = PaymentStatus.UNKNOWN
            else:
                raise TypeError("Unexpected payment gateway result")
            view = _payment_view(payment)
        return PayCartResult(view, replayed=False)

    def _reserve(self, command: PayCart) -> PayCartResult | ChargeRequest:
        """Return an existing payment result or reserve a new attempt."""
        try:
            with self._session_factory.begin() as session:
                if not self._store.user_exists(session, command.user_id):
                    raise PaymentError("unauthorized")
                cart = self._store.lock_cart(session, command.cart_id, command.user_id)
                if cart is None:
                    raise PaymentError("not_found")
                existing = self._store.find_by_key(
                    session, command.user_id, command.idempotency_key
                )
                if existing is not None:
                    return _replay(existing, command)
                if cart.status != "active":
                    raise PaymentError("cart_not_active")
                if not self._store.has_items(session, cart.id):
                    raise PaymentError("empty_cart")
                payment_method = self._store.get_method(
                    session, command.payment_method_id, command.user_id
                )
                if payment_method is None:
                    raise PaymentError("not_found")
                if self._store.find_blocking(session, cart.id) is not None:
                    raise PaymentError("payment_exists")
                total = self._get_total(cart.id)
                payment = self._store.insert_pending(session, command, total)
                charge_request = ChargeRequest(
                    payment.id, total, payment_method.provider_token
                )
            return charge_request
        except IntegrityError as error:
            # The failed transaction must close before reading the winning attempt.
            replay = self._recover_reservation(command, error)
            if replay is None:
                raise
            return replay

    def _get_total(self, cart_id: UUID) -> Money:
        """Get a valid cart total.

        Raises:
            TotalUnavailable: The cart total is missing or invalid.
        """
        supplied_total = self._total_provider.get_total(cart_id)
        try:
            return Money(supplied_total.amount, supplied_total.currency)
        except (ValueError, AttributeError, TypeError) as error:
            raise TotalUnavailable("Invalid cart total") from error

    def _recover_reservation(
        self, command: PayCart, error: IntegrityError
    ) -> PayCartResult | None:
        """Recover a payment result after a reservation conflict.

        Returns:
            A matching result, or None if the conflict cannot be resolved.

        Raises:
            PaymentError: The request conflicts with an existing attempt.
        """
        database_error = error.orig
        if not isinstance(database_error, UniqueViolation):
            return None
        constraint_name = database_error.diag.constraint_name
        if constraint_name not in {
            "uq_payments_user_key",
            "uq_payments_blocking_cart",
        }:
            return None
        with self._session_factory() as session:
            existing = self._store.find_by_key(
                session, command.user_id, command.idempotency_key
            )
            if existing is not None:
                return _replay(existing, command)
            if (
                constraint_name == "uq_payments_blocking_cart"
                and self._store.find_blocking(session, command.cart_id) is not None
            ):
                raise PaymentError("payment_exists") from error
        return None
