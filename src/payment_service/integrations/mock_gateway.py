from payment_service.services.contracts import (
    ChargeDeclined,
    ChargeRequest,
    ChargeResult,
    ChargeSucceeded,
    ProviderUnavailable,
)


class MockPaymentGateway:
    def charge(self, request: ChargeRequest) -> ChargeResult:
        if not request.provider_token:
            raise ValueError("Provider token must not be empty")
        if request.provider_token == "tok_test_declined":
            return ChargeDeclined()
        if request.provider_token == "tok_test_timeout":
            raise ProviderUnavailable("Payment provider unavailable")
        return ChargeSucceeded(f"mock_{request.payment_id}")
