"""Reserve durable payment attempts and replay historical snapshots."""

from uuid import UUID

from psycopg.errors import UniqueViolation
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from payment_service.models import Payment
from payment_service.services.contracts import (
    ChargeRequest,
    Money,
    PayCart,
    PayCartResult,
    PaymentError,
    PaymentGateway,
    PaymentView,
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
    """Own reservation transactions and enforce payment eligibility and replay rules."""

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
        """Reserve a pending attempt or replay a matching stored attempt.

        New attempts commit before returning; this implementation does not charge.
        Raise PaymentError for rejected requests or TotalUnavailable for missing
        or invalid totals. Database and adapter programming errors propagate.
        """
        reservation = self._reserve(command)
        if isinstance(reservation, PayCartResult):
            return reservation
        with self._session_factory() as session:
            payment = session.get(Payment, reservation.payment_id)
            if payment is None:
                raise RuntimeError("Reserved payment disappeared")
            return PayCartResult(_payment_view(payment), replayed=False)

    def _reserve(self, command: PayCart) -> PayCartResult | ChargeRequest:
        """Return a replay or charge details after committing a new reservation."""
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
        """Validate adapter output without masking errors raised by the adapter."""
        supplied_total = self._total_provider.get_total(cart_id)
        try:
            return Money(supplied_total.amount, supplied_total.currency)
        except (ValueError, AttributeError, TypeError) as error:
            raise TotalUnavailable("Invalid cart total") from error

    def _recover_reservation(
        self, command: PayCart, error: IntegrityError
    ) -> PayCartResult | None:
        """Resolve a known uniqueness conflict; return None to re-raise the error."""
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
