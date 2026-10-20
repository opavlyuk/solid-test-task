import pytest

from payment_service.models import Payment
from payment_service.payment_status import PaymentStatus


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        pytest.param(PaymentStatus.PENDING, None, id="pending"),
        pytest.param(PaymentStatus.SUCCEEDED, None, id="succeeded"),
        pytest.param(PaymentStatus.FAILED, "card_declined", id="failed"),
        pytest.param(PaymentStatus.UNKNOWN, "provider_unavailable", id="unknown"),
    ],
)
def test_failure_code_tracks_current_status(
    status: PaymentStatus, expected: str | None
) -> None:
    payment = Payment(status=PaymentStatus.FAILED)
    assert payment.failure_code == "card_declined"
    payment.status = status
    assert payment.failure_code == expected
