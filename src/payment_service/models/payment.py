from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from payment_service.models.base import Base
from payment_service.payment_status import PaymentStatus


class _PaymentStatusText(TypeDecorator[PaymentStatus]):
    impl = Text
    cache_ok = True

    def process_bind_param(
        self, value: PaymentStatus | None, dialect: Dialect
    ) -> str | None:
        return value.value if value is not None else None

    def process_result_value(
        self, value: str | None, dialect: Dialect
    ) -> PaymentStatus | None:
        return PaymentStatus(value) if value is not None else None


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_payments_user_key"),
        CheckConstraint(
            "amount > 0 AND amount <= 9999999999.99", name="ck_payments_amount"
        ),
        CheckConstraint("currency = 'USD'", name="ck_payments_currency"),
        CheckConstraint(
            "(status IN ('pending', 'failed', 'unknown') "
            "AND provider_reference IS NULL) OR "
            "(status = 'succeeded' AND provider_reference IS NOT NULL)",
            name="ck_payments_result",
        ),
        Index(
            "uq_payments_blocking_cart",
            "cart_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'succeeded', 'unknown')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    cart_id: Mapped[UUID] = mapped_column(ForeignKey("carts.id"))
    payment_method_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_payment_methods.id")
    )
    idempotency_key: Mapped[UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(CHAR(3))
    status: Mapped[PaymentStatus] = mapped_column(_PaymentStatusText())
    provider_reference: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
