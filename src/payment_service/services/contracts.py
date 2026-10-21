from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from payment_service.payment_status import PaymentStatus


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not self.amount.is_finite():
            raise ValueError("Amount must be finite")
        if not Decimal("0") < self.amount <= Decimal("9999999999.99"):
            raise ValueError("Amount is outside the supported range")
        if self.amount != self.amount.quantize(Decimal("0.01")):
            raise ValueError("Amount must have at most two decimal places")
        if self.currency != "USD":
            raise ValueError("Only USD is supported")


@dataclass(frozen=True)
class PayCart:
    user_id: UUID
    cart_id: UUID
    payment_method_id: UUID
    idempotency_key: UUID


@dataclass(frozen=True)
class ChargeRequest:
    payment_id: UUID
    total: Money
    provider_token: str = field(repr=False)


@dataclass(frozen=True)
class ChargeSucceeded:
    provider_reference: str


@dataclass(frozen=True)
class ChargeDeclined:
    pass


type ChargeResult = ChargeSucceeded | ChargeDeclined


@dataclass(frozen=True)
class PaymentView:
    id: UUID
    cart_id: UUID
    payment_method_id: UUID
    total: Money
    status: PaymentStatus

    @property
    def failure_code(self) -> str | None:
        return {
            PaymentStatus.PENDING: None,
            PaymentStatus.SUCCEEDED: None,
            PaymentStatus.FAILED: "card_declined",
            PaymentStatus.UNKNOWN: "provider_unavailable",
        }[self.status]


@dataclass(frozen=True)
class PayCartResult:
    payment: PaymentView
    replayed: bool


class TotalUnavailable(Exception):
    pass


class ProviderUnavailable(Exception):
    pass


class PaymentError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TotalProvider(Protocol):
    def get_total(self, cart_id: UUID) -> Money: ...


class PaymentGateway(Protocol):
    def charge(self, request: ChargeRequest) -> ChargeResult: ...
