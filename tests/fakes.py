from payment_service.services.contracts import (
    ChargeRequest,
    ChargeResult,
    ProviderUnavailable,
)


class RecordingGateway:
    def __init__(self, result: ChargeResult | ProviderUnavailable) -> None:
        self.calls: list[ChargeRequest] = []
        self.result = result

    def charge(self, request: ChargeRequest) -> ChargeResult:
        self.calls.append(request)
        if isinstance(self.result, ProviderUnavailable):
            raise self.result
        return self.result
