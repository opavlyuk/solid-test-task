"""Database operations for payment reservation."""

from uuid import UUID, uuid4

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from payment_service.models import Cart, CartItem, Payment, User, UserPaymentMethod
from payment_service.payment_status import PaymentStatus
from payment_service.services.contracts import Money, PayCart


class PaymentStore:
    """Query and flush payment rows within caller-owned transactions."""

    def user_exists(self, session: Session, user_id: UUID) -> bool:
        return bool(session.scalar(select(exists().where(User.id == user_id))))

    def lock_cart(self, session: Session, cart_id: UUID, user_id: UUID) -> Cart | None:
        """Lock the user's cart until transaction end; return None if inaccessible."""
        return session.scalar(
            select(Cart)
            .where(Cart.id == cart_id, Cart.user_id == user_id)
            .with_for_update()
        )

    def find_by_key(
        self, session: Session, user_id: UUID, idempotency_key: UUID
    ) -> Payment | None:
        """Find a stored attempt by the user's idempotency key, in any status."""
        return session.scalar(
            select(Payment).where(
                Payment.user_id == user_id, Payment.idempotency_key == idempotency_key
            )
        )

    def has_items(self, session: Session, cart_id: UUID) -> bool:
        return bool(session.scalar(select(exists().where(CartItem.cart_id == cart_id))))

    def get_method(
        self, session: Session, payment_method_id: UUID, user_id: UUID
    ) -> UserPaymentMethod | None:
        """Return the saved method only if it belongs to the user."""
        return session.scalar(
            select(UserPaymentMethod).where(
                UserPaymentMethod.id == payment_method_id,
                UserPaymentMethod.user_id == user_id,
            )
        )

    def find_blocking(self, session: Session, cart_id: UUID) -> Payment | None:
        """Find a pending, unknown, or successful attempt that blocks another charge."""
        return session.scalar(
            select(Payment).where(
                Payment.cart_id == cart_id,
                Payment.status.in_(
                    [
                        PaymentStatus.PENDING,
                        PaymentStatus.UNKNOWN,
                        PaymentStatus.SUCCEEDED,
                    ]
                ),
            )
        )

    def insert_pending(
        self, session: Session, command: PayCart, total: Money
    ) -> Payment:
        """Flush a new pending attempt with database defaults, without committing."""
        payment = Payment(
            id=uuid4(),
            user_id=command.user_id,
            cart_id=command.cart_id,
            payment_method_id=command.payment_method_id,
            idempotency_key=command.idempotency_key,
            amount=total.amount,
            currency=total.currency,
            status=PaymentStatus.PENDING,
            provider_reference=None,
        )
        session.add(payment)
        session.flush()
        return payment
