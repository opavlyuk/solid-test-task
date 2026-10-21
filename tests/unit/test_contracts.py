from decimal import Decimal
from uuid import UUID

import pytest

from payment_service.payment_status import PaymentStatus
from payment_service.services.contracts import ChargeRequest, Money, PaymentView


@pytest.mark.parametrize("amount", ["0.01", "70.00", "70.000", "9999999999.99"])
def test_money_accepts_supported_amounts(amount: str) -> None:
    money = Money(Decimal(amount), "USD")
    assert money.amount == Decimal(amount)
    assert money.currency == "USD"


@pytest.mark.parametrize(
    "amount",
    [
        "NaN",
        "sNaN",
        "Infinity",
        "-Infinity",
        "0",
        "-0.01",
        "70.001",
        "9999999999.991",
        "10000000000",
    ],
)
def test_money_rejects_invalid_amounts(amount: str) -> None:
    with pytest.raises(ValueError):
        Money(Decimal(amount), "USD")


@pytest.mark.parametrize("currency", ["EUR", "usd", "", "USDD"])
def test_money_rejects_other_currencies(currency: str) -> None:
    with pytest.raises(ValueError):
        Money(Decimal("70"), currency)


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (PaymentStatus.PENDING, None),
        (PaymentStatus.SUCCEEDED, None),
        (PaymentStatus.FAILED, "card_declined"),
        (PaymentStatus.UNKNOWN, "provider_unavailable"),
    ],
)
def test_payment_view_derives_failure_code(
    status: PaymentStatus, expected: str | None
) -> None:
    identifier = UUID("11111111-1111-1111-1111-111111111111")
    view = PaymentView(
        identifier, identifier, identifier, Money(Decimal("70"), "USD"), status
    )
    assert view.failure_code == expected


def test_charge_request_repr_hides_provider_token() -> None:
    request = ChargeRequest(
        UUID("11111111-1111-1111-1111-111111111111"),
        Money(Decimal("70"), "USD"),
        "secret-provider-token",
    )
    assert "secret-provider-token" not in repr(request)
