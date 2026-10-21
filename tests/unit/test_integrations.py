from decimal import Decimal
from uuid import UUID

import pytest

from payment_service.integrations.mock_gateway import MockPaymentGateway
from payment_service.integrations.mock_totals import DemoTotalProvider
from payment_service.services.contracts import (
    ChargeDeclined,
    ChargeRequest,
    ChargeSucceeded,
    Money,
    ProviderUnavailable,
    TotalUnavailable,
)

PAYMENT_ID = UUID("11111111-1111-1111-1111-111111111111")


def request_for(token: str) -> ChargeRequest:
    return ChargeRequest(PAYMENT_ID, Money(Decimal("70"), "USD"), token)


@pytest.mark.parametrize("token", ["tok_test_alice_visa", "another_nonempty_token"])
def test_gateway_success_reference_is_deterministic(token: str) -> None:
    gateway = MockPaymentGateway()
    expected = ChargeSucceeded("mock_11111111-1111-1111-1111-111111111111")
    assert gateway.charge(request_for(token)) == expected
    assert gateway.charge(request_for(token)) == expected


def test_gateway_returns_confirmed_decline() -> None:
    assert (
        MockPaymentGateway().charge(request_for("tok_test_declined"))
        == ChargeDeclined()
    )


def test_gateway_reports_timeout_as_unavailable() -> None:
    with pytest.raises(ProviderUnavailable):
        MockPaymentGateway().charge(request_for("tok_test_timeout"))


def test_gateway_rejects_empty_token() -> None:
    with pytest.raises(ValueError):
        MockPaymentGateway().charge(request_for(""))


def test_demo_total_is_explicit_for_seeded_cart() -> None:
    provider = DemoTotalProvider()
    cart_id = UUID("c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1")
    assert provider.get_total(cart_id) == Money(Decimal("70.00"), "USD")
    assert provider.get_total(cart_id) == Money(Decimal("70.00"), "USD")


def test_demo_total_is_unavailable_for_other_carts() -> None:
    with pytest.raises(TotalUnavailable):
        DemoTotalProvider().get_total(PAYMENT_ID)
