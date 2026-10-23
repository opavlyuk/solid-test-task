from payment_service.services.contracts import (
    ChargeDeclined,
    ChargeRequest,
    ChargeResult,
    ChargeSucceeded,
    ProviderUnavailable,
)

DECLINED_TOKEN = "tok_test_declined"
TIMEOUT_TOKEN = "tok_test_timeout"


class MockPaymentGateway:
    def charge(self, request: ChargeRequest) -> ChargeResult:
        if not request.provider_token:
            raise ValueError("Provider token must not be empty")
        if request.provider_token == DECLINED_TOKEN:
            return ChargeDeclined()
        if request.provider_token == TIMEOUT_TOKEN:
            raise ProviderUnavailable("Payment provider unavailable")
        return ChargeSucceeded(f"mock_{request.payment_id}")
