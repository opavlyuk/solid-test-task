"""Verify payment constraints against PostgreSQL."""

from decimal import Decimal
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from payment_service.models import Payment
from payment_service.payment_status import PaymentStatus

pytestmark = pytest.mark.integration


def insert_payment(
    connection: Connection, shop: dict[str, UUID], **overrides: object
) -> UUID:
    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": shop["user_id"],
        "cart_id": shop["cart_id"],
        "payment_method_id": shop["method_id"],
        "idempotency_key": uuid4(),
        "amount": "70.00",
        "currency": "USD",
        "status": "pending",
        "provider_reference": None,
    }
    values.update(overrides)
    return connection.execute(
        text(
            "INSERT INTO payments ("
            "id, user_id, cart_id, payment_method_id, idempotency_key, "
            "amount, currency, status, provider_reference"
            ") VALUES ("
            ":id, :user_id, :cart_id, :payment_method_id, :idempotency_key, "
            ":amount, :currency, :status, :provider_reference"
            ") RETURNING id"
        ),
        values,
    ).scalar_one()


def assert_violation(
    error: IntegrityError, sqlstate: str, constraint_name: str
) -> None:
    assert isinstance(error.orig, psycopg.Error)
    assert error.orig.sqlstate == sqlstate
    assert error.orig.diag.constraint_name == constraint_name


@pytest.mark.parametrize(
    "amount", [pytest.param("0", id="zero"), pytest.param("NaN", id="nan")]
)
def test_rejects_zero_and_nan_amounts(
    engine: Engine, shop: dict[str, UUID], amount: str
) -> None:
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        insert_payment(connection, shop, amount=amount)
    assert_violation(caught.value, "23514", "ck_payments_amount")


@pytest.mark.parametrize(
    "amount",
    [pytest.param("0.01", id="minimum"), pytest.param("9999999999.99", id="maximum")],
)
def test_accepts_amount_boundaries(
    engine: Engine, shop: dict[str, UUID], amount: str
) -> None:
    with engine.begin() as connection:
        payment_id = insert_payment(connection, shop, amount=amount)
        stored = connection.execute(
            text("SELECT amount FROM payments WHERE id = :id"), {"id": payment_id}
        ).scalar_one()
    assert str(stored) == amount


@pytest.mark.parametrize(
    ("status", "reference"),
    [
        pytest.param("pending", "ref", id="pending-with-reference"),
        pytest.param("succeeded", None, id="success-without-reference"),
        pytest.param("failed", "ref", id="decline-with-reference"),
        pytest.param("unknown", "ref", id="unknown-with-reference"),
    ],
)
def test_rejects_inconsistent_results(
    engine: Engine,
    shop: dict[str, UUID],
    status: str,
    reference: str | None,
) -> None:
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        insert_payment(
            connection,
            shop,
            status=status,
            provider_reference=reference,
        )
    assert_violation(caught.value, "23514", "ck_payments_result")


def test_rejects_unsupported_currency(engine: Engine, shop: dict[str, UUID]) -> None:
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        insert_payment(connection, shop, currency="EUR")
    assert_violation(caught.value, "23514", "ck_payments_currency")


def test_rejects_unrecognized_status(engine: Engine, shop: dict[str, UUID]) -> None:
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        insert_payment(connection, shop, status="refunded")
    assert_violation(caught.value, "23514", "ck_payments_result")


@pytest.mark.parametrize(
    ("table", "fixture_key", "fk_column"),
    [
        pytest.param("users", "user_id", "user_id", id="user"),
        pytest.param("carts", "cart_id", "cart_id", id="cart"),
        pytest.param(
            "user_payment_methods", "method_id", "payment_method_id", id="method"
        ),
    ],
)
def test_payment_references_prevent_deletion(
    engine: Engine,
    shop: dict[str, UUID],
    table: str,
    fixture_key: str,
    fk_column: str,
) -> None:
    with engine.begin() as connection:
        insert_payment(connection, shop)
        # Remove other shop dependencies so the payment FK is the only blocker.
        if table == "users":
            other_user = uuid4()
            connection.execute(
                text("INSERT INTO users (id, email, name) VALUES (:id, 'b@test', 'B')"),
                {"id": other_user},
            )
            for owned in ("carts", "user_payment_methods"):
                connection.execute(
                    text(f"UPDATE {owned} SET user_id = :id"), {"id": other_user}
                )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        connection.execute(
            text(f"DELETE FROM {table} WHERE id = :id"), {"id": shop[fixture_key]}
        )
    assert_violation(caught.value, "23503", f"payments_{fk_column}_fkey")


def test_user_key_cannot_be_reused_for_another_cart(
    engine: Engine, shop: dict[str, UUID]
) -> None:
    key = uuid4()
    other_cart_id = uuid4()
    with engine.begin() as connection:
        insert_payment(connection, shop, idempotency_key=key)
        connection.execute(
            text("INSERT INTO carts (id, user_id) VALUES (:id, :user_id)"),
            {"id": other_cart_id, "user_id": shop["user_id"]},
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        insert_payment(connection, shop, idempotency_key=key, cart_id=other_cart_id)
    assert_violation(caught.value, "23505", "uq_payments_user_key")


def test_different_users_can_reuse_key(engine: Engine, shop: dict[str, UUID]) -> None:
    key = uuid4()
    with engine.begin() as connection:
        insert_payment(connection, shop, idempotency_key=key)
        other = {name: uuid4() for name in ("user_id", "cart_id", "method_id")}
        connection.execute(
            text(
                "INSERT INTO users (id, email, name) VALUES (:user_id, 'b@test', 'B')"
            ),
            other,
        )
        connection.execute(
            text("INSERT INTO carts (id, user_id) VALUES (:cart_id, :user_id)"), other
        )
        connection.execute(
            text(
                "INSERT INTO user_payment_methods (id, user_id, provider_token) "
                "VALUES (:method_id, :user_id, 'tok_other')"
            ),
            other,
        )
        insert_payment(connection, other, idempotency_key=key)
        assert connection.scalar(text("SELECT count(*) FROM payments")) == 2


@pytest.mark.parametrize(
    ("status", "reference"),
    [
        pytest.param("pending", None, id="pending"),
        pytest.param("succeeded", "mock_reference", id="succeeded"),
        pytest.param("unknown", None, id="unknown"),
    ],
)
def test_blocking_attempt_prevents_new_payment(
    engine: Engine,
    shop: dict[str, UUID],
    status: str,
    reference: str | None,
) -> None:
    with engine.begin() as connection:
        insert_payment(
            connection,
            shop,
            status=status,
            provider_reference=reference,
        )
    with pytest.raises(IntegrityError) as caught, engine.begin() as connection:
        insert_payment(connection, shop)
    assert_violation(caught.value, "23505", "uq_payments_blocking_cart")


def test_failed_attempts_allow_another_payment(
    engine: Engine, shop: dict[str, UUID]
) -> None:
    with engine.begin() as connection:
        for _ in range(2):
            insert_payment(connection, shop, status="failed")
        insert_payment(connection, shop)
        assert connection.scalar(text("SELECT count(*) FROM payments")) == 3


def test_orm_round_trip_preserves_values_after_commit(
    session_factory: sessionmaker[Session], shop: dict[str, UUID]
) -> None:
    with session_factory.begin() as session:
        payment = Payment(
            user_id=shop["user_id"],
            cart_id=shop["cart_id"],
            payment_method_id=shop["method_id"],
            idempotency_key=uuid4(),
            amount=Decimal("70.00"),
            currency="USD",
            status=PaymentStatus.PENDING,
        )
        session.add(payment)
    # A detached result stays readable because the factory disables expiration.
    assert payment.amount == Decimal("70.00")
    with session_factory() as session:
        stored = session.get(Payment, payment.id)
        assert stored is not None
        assert stored.status is PaymentStatus.PENDING
        assert stored.amount == Decimal("70.00")


def test_migration_round_trip_preserves_shop_data(
    engine: Engine, shop: dict[str, UUID], alembic_config: Config
) -> None:
    tables = ("users", "products", "carts", "cart_items", "user_payment_methods")
    with engine.begin() as connection:
        product_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO products (id, name, price) VALUES (:id, 'Product', 10.00)"
            ),
            {"id": product_id},
        )
        connection.execute(
            text(
                "INSERT INTO cart_items (cart_id, product_id, quantity, unit_price) "
                "VALUES (:cart_id, :product_id, 1, 10.00)"
            ),
            {"cart_id": shop["cart_id"], "product_id": product_id},
        )
    with engine.connect() as connection:
        before = {
            table: connection.execute(text(f"SELECT * FROM {table}")).all()
            for table in tables
        }
    try:
        command.downgrade(alembic_config, "0001")
        assert "payments" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            for table in tables:
                assert (
                    connection.execute(text(f"SELECT * FROM {table}")).all()
                    == before[table]
                )
    finally:
        command.upgrade(alembic_config, "head")
    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
        )
    assert "payments" in inspect(engine).get_table_names()
    command.check(alembic_config)
