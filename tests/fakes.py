from threading import Lock
from uuid import UUID

from payment_service.services.contracts import (
    ChargeRequest,
    ChargeResult,
    Money,
    ProviderUnavailable,
    TotalUnavailable,
)


class StubTotals:
    """Cart totals for tests."""

    def __init__(self, totals_by_cart: dict[UUID, Money]) -> None:
        self.totals_by_cart = totals_by_cart

    def get_total(self, cart_id: UUID) -> Money:
        if cart_id not in self.totals_by_cart:
            raise TotalUnavailable()
        return self.totals_by_cart[cart_id]


class RecordingGateway:
    def __init__(self, result: ChargeResult | ProviderUnavailable) -> None:
        self.calls: list[ChargeRequest] = []
        self.result = result
        self._lock = Lock()

    def charge(self, request: ChargeRequest) -> ChargeResult:
        with self._lock:
            self.calls.append(request)
        if isinstance(self.result, ProviderUnavailable):
            raise self.result
        return self.result
