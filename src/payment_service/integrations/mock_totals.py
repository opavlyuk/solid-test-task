from decimal import Decimal
from uuid import UUID

from payment_service.services.contracts import Money, TotalUnavailable

DEMO_CART_ID = UUID("c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1")
DEMO_TOTAL = Money(Decimal("70.00"), "USD")


class DemoTotalProvider:
    def get_total(self, cart_id: UUID) -> Money:
        if cart_id != DEMO_CART_ID:
            raise TotalUnavailable("Cart total unavailable")
        return DEMO_TOTAL
