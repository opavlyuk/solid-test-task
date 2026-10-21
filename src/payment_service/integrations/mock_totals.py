from decimal import Decimal
from uuid import UUID

from payment_service.services.contracts import Money, TotalUnavailable


class DemoTotalProvider:
    def get_total(self, cart_id: UUID) -> Money:
        if cart_id != UUID("c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1"):
            raise TotalUnavailable("Cart total unavailable")
        return Money(Decimal("70.00"), "USD")
