"""Load the supplied sample data into a migrated local development database."""

import os
from decimal import Decimal
from uuid import UUID

from sqlalchemy import MetaData, create_engine
from sqlalchemy.dialects.postgresql import insert

# Stable IDs make repeated runs safe, including the two sample cart items.
ALICE = UUID("11111111-1111-1111-1111-111111111111")
BOB = UUID("22222222-2222-2222-2222-222222222222")
KETTLE = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
BLANKET = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
MUG = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
CART = UUID("c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1")

# Parent tables precede their dependents.
SEED_ROWS: dict[str, list[dict[str, object]]] = {
    "users": [
        {"id": ALICE, "email": "alice@example.com", "name": "Alice"},
        {"id": BOB, "email": "bob@example.com", "name": "Bob"},
    ],
    "products": [
        {
            "id": KETTLE,
            "name": "Blue Kettle",
            "price": Decimal("45.00"),
            "currency": "USD",
            "stock_quantity": 10,
        },
        {
            "id": BLANKET,
            "name": "Wool Blanket",
            "price": Decimal("89.99"),
            "currency": "USD",
            "stock_quantity": 5,
        },
        {
            "id": MUG,
            "name": "Ceramic Mug",
            "price": Decimal("12.50"),
            "currency": "USD",
            "stock_quantity": 100,
        },
    ],
    "carts": [{"id": CART, "user_id": ALICE, "status": "active"}],
    "cart_items": [
        {
            "id": UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
            "cart_id": CART,
            "product_id": KETTLE,
            "quantity": 1,
            "unit_price": Decimal("45.00"),
        },
        {
            "id": UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"),
            "cart_id": CART,
            "product_id": MUG,
            "quantity": 2,
            "unit_price": Decimal("12.50"),
        },
    ],
    "user_payment_methods": [
        {
            "id": UUID("11111111-2222-3333-4444-555555555555"),
            "user_id": ALICE,
            "provider_token": "tok_test_alice_visa",
            "last_four": "4242",
            "is_default": True,
        }
    ],
}


def seed_database(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            metadata = MetaData()
            metadata.reflect(bind=connection, only=list(SEED_ROWS))
            for table_name, rows in SEED_ROWS.items():
                statement = insert(metadata.tables[table_name]).values(rows)
                connection.execute(
                    statement.on_conflict_do_nothing(index_elements=["id"])
                )
    finally:
        engine.dispose()


def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("Set DATABASE_URL before running the development seed.")
    seed_database(database_url)
    print("Development seed complete; existing rows were preserved.")


if __name__ == "__main__":
    main()
