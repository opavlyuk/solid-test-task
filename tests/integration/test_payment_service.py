"""Verify payment reservation, replay, and locking with PostgreSQL."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
from threading import Barrier, Event
from time import monotonic
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from psycopg import Error as PsycopgError
from sqlalchemy import Engine, event, func, select, text
from sqlalchemy.engine import ExceptionContext
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from tests.fakes import RecordingGateway, StubTotals

from payment_service.models import Cart, Payment
from payment_service.payment_status import PaymentStatus
from payment_service.services.contracts import (
    ChargeSucceeded,
    Money,
    PayCart,
    PayCartResult,
    PaymentError,
    TotalProvider,
    TotalUnavailable,
)
from payment_service.services.payment_store import PaymentStore
from payment_service.services.payments import PaymentService

pytestmark = pytest.mark.integration


@pytest.fixture
def violated_constraints(
    session_factory: sessionmaker[Session],
) -> Iterator[list[str | None]]:
    """Capture violated database constraints during a test."""
    constraints: list[str | None] = []

    def record_constraint_error(context: ExceptionContext) -> None:
        error = context.original_exception
        if isinstance(error, PsycopgError):
            constraints.append(error.diag.constraint_name)

    engine = session_factory.kw["bind"]
    event.listen(engine, "handle_error", record_constraint_error)
    try:
        yield constraints
    finally:
        event.remove(engine, "handle_error", record_constraint_error)


@pytest.fixture
def pay_command(engine: Engine, shop: dict[str, UUID]) -> PayCart:
    """Prepare a payment command for a nonempty cart."""
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO products (id, name, price) VALUES (:id, 'P', 10)"),
            {"id": (product_id := uuid4())},
        )
        connection.execute(
            text(
                "INSERT INTO cart_items (cart_id, product_id, quantity, unit_price) "
                "VALUES (:cart_id, :product_id, 1, 10)"
            ),
            {"cart_id": shop["cart_id"], "product_id": product_id},
        )
    return PayCart(shop["user_id"], shop["cart_id"], shop["method_id"], uuid4())


def make_service(
    session_factory: sessionmaker[Session],
    command: PayCart,
    total_provider: TotalProvider | None = None,
) -> tuple[PaymentService, RecordingGateway]:
    """Return a payment service and a gateway that records charge requests."""
    gateway = RecordingGateway(ChargeSucceeded("unused"))
    total_provider = (
        total_provider
        if total_provider is not None
        else StubTotals({command.cart_id: Money(Decimal("10.00"), "USD")})
    )
    service = PaymentService(
        session_factory=session_factory,
        total_provider=total_provider,
        gateway=gateway,
    )
    return service, gateway


def assert_no_payment(
    session_factory: sessionmaker[Session], gateway: RecordingGateway
) -> None:
    """Verify that a request caused no payment or charge."""
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 0
    assert gateway.calls == []


@pytest.mark.parametrize(
    ("field", "expected_code"),
    [
        pytest.param("user_id", "unauthorized", id="missing-user"),
        pytest.param("cart_id", "not_found", id="missing-cart"),
        pytest.param("payment_method_id", "not_found", id="missing-method"),
    ],
)
def test_missing_resources_leave_no_attempt(
    session_factory: sessionmaker[Session],
    pay_command: PayCart,
    field: str,
    expected_code: str,
) -> None:
    command = replace(pay_command, **{field: uuid4()})
    service, gateway = make_service(session_factory, command)
    with pytest.raises(PaymentError) as error:
        service.pay(command)
    assert error.value.code == expected_code
    assert_no_payment(session_factory, gateway)


def test_cart_owned_by_another_user_is_rejected(
    session_factory: sessionmaker[Session], engine: Engine, pay_command: PayCart
) -> None:
    other_user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, name) VALUES (:id, 'b@test', 'B')"),
            {"id": other_user_id},
        )
    command = replace(pay_command, user_id=other_user_id)
    service, gateway = make_service(session_factory, command)
    with pytest.raises(PaymentError) as error:
        service.pay(command)
    assert error.value.code == "not_found"
    assert_no_payment(session_factory, gateway)


def test_empty_cart_leaves_no_attempt(
    session_factory: sessionmaker[Session], engine: Engine, pay_command: PayCart
) -> None:
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM cart_items"))
    service, gateway = make_service(session_factory, pay_command)
    with pytest.raises(PaymentError) as error:
        service.pay(pay_command)
    assert error.value.code == "empty_cart"
    assert_no_payment(session_factory, gateway)


@pytest.mark.parametrize("status", ["abandoned", "checked_out"])
def test_inactive_cart_leaves_no_attempt(
    session_factory: sessionmaker[Session],
    engine: Engine,
    pay_command: PayCart,
    status: str,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE carts SET status = :status"), {"status": status}
        )
    service, gateway = make_service(session_factory, pay_command)
    with pytest.raises(PaymentError) as error:
        service.pay(pay_command)
    assert error.value.code == "cart_not_active"
    assert_no_payment(session_factory, gateway)


def test_missing_total_leaves_no_attempt(
    session_factory: sessionmaker[Session], pay_command: PayCart
) -> None:
    service, gateway = make_service(session_factory, pay_command, StubTotals({}))
    with pytest.raises(TotalUnavailable):
        service.pay(pay_command)
    assert_no_payment(session_factory, gateway)


@pytest.mark.parametrize("error_type", [TypeError, AttributeError, ValueError])
def test_total_provider_programming_error_remains_visible(
    session_factory: sessionmaker[Session],
    pay_command: PayCart,
    error_type: type[Exception],
) -> None:
    failure = error_type("Adapter implementation bug")

    class BrokenTotals:
        def get_total(self, cart_id: UUID) -> Money:
            raise failure

    service, gateway = make_service(session_factory, pay_command, BrokenTotals())
    with pytest.raises(error_type) as error:
        service.pay(pay_command)
    assert error.value is failure
    assert_no_payment(session_factory, gateway)


def test_reservation_is_durable_pending_snapshot(
    session_factory: sessionmaker[Session], pay_command: PayCart
) -> None:
    # Deliberately differs from the cart item's USD 10 price.
    service, gateway = make_service(
        session_factory,
        pay_command,
        StubTotals({pay_command.cart_id: Money(Decimal("37.25"), "USD")}),
    )
    result = service.pay(pay_command)
    assert result.replayed is False
    assert result.payment.status == PaymentStatus.PENDING
    assert result.payment.cart_id == pay_command.cart_id
    assert result.payment.payment_method_id == pay_command.payment_method_id
    assert result.payment.total == Money(Decimal("37.25"), "USD")
    with session_factory() as session:
        payment = session.get(Payment, result.payment.id)
        assert payment is not None
        assert payment.idempotency_key == pay_command.idempotency_key
        assert payment.user_id == pay_command.user_id
        assert payment.cart_id == pay_command.cart_id
        assert payment.payment_method_id == pay_command.payment_method_id
        assert payment.amount == Decimal("37.25")
        assert payment.currency == "USD"
        assert payment.created_at is not None
        assert payment.provider_reference is None
    assert gateway.calls == []


@pytest.mark.parametrize("status", list(PaymentStatus))
def test_replay_uses_historical_snapshot(
    session_factory: sessionmaker[Session],
    engine: Engine,
    pay_command: PayCart,
    status: PaymentStatus,
) -> None:
    service, gateway = make_service(session_factory, pay_command)
    initial_result = service.pay(pay_command)
    with session_factory.begin() as session:
        payment = session.get(Payment, initial_result.payment.id)
        assert payment is not None
        payment.status = status
        payment.provider_reference = (
            "ref" if status == PaymentStatus.SUCCEEDED else None
        )
    other_user_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name) "
                "VALUES (:id, 'other@test', 'Other')"
            ),
            {"id": other_user_id},
        )
        connection.execute(text("UPDATE carts SET status = 'checked_out'"))
        connection.execute(text("DELETE FROM cart_items"))
        connection.execute(
            text(
                "UPDATE user_payment_methods SET user_id = :id, "
                "provider_token = 'changed'"
            ),
            {"id": other_user_id},
        )
    service, gateway = make_service(session_factory, pay_command, StubTotals({}))
    replay = service.pay(pay_command)
    assert replay.replayed is True
    assert replay.payment.id == initial_result.payment.id
    assert replay.payment.cart_id == pay_command.cart_id
    assert replay.payment.payment_method_id == pay_command.payment_method_id
    assert replay.payment.status == status
    assert replay.payment.total == Money(Decimal("10.00"), "USD")
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert gateway.calls == []


@pytest.mark.parametrize("field", ["cart_id", "payment_method_id"])
def test_key_reuse_for_different_request_conflicts(
    session_factory: sessionmaker[Session],
    engine: Engine,
    pay_command: PayCart,
    field: str,
) -> None:
    service, _ = make_service(session_factory, pay_command)
    service.pay(pay_command)
    replacement_id = uuid4()
    if field == "cart_id":
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO carts (id, user_id) VALUES (:id, :user_id)"),
                {"id": replacement_id, "user_id": pay_command.user_id},
            )
    with pytest.raises(PaymentError) as error:
        service.pay(replace(pay_command, **{field: replacement_id}))
    assert error.value.code == "idempotency_conflict"


@pytest.mark.parametrize(
    "status", [PaymentStatus.PENDING, PaymentStatus.UNKNOWN, PaymentStatus.SUCCEEDED]
)
def test_blocking_attempt_prevents_new_key(
    session_factory: sessionmaker[Session], pay_command: PayCart, status: PaymentStatus
) -> None:
    service, _ = make_service(session_factory, pay_command)
    result = service.pay(pay_command)
    with session_factory.begin() as session:
        payment = session.get(Payment, result.payment.id)
        assert payment is not None
        payment.status = status
        payment.provider_reference = (
            "ref" if status == PaymentStatus.SUCCEEDED else None
        )
    with pytest.raises(PaymentError) as error:
        service.pay(replace(pay_command, idempotency_key=uuid4()))
    assert error.value.code == "payment_exists"


def seed_cart(
    engine: Engine, user_id: UUID, *, create_user: bool = False
) -> tuple[UUID, UUID]:
    """Create a nonempty cart and payment method for a user.

    Returns:
        The cart ID and payment method ID, in that order.
    """
    cart_id, method_id, product_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        if create_user:
            connection.execute(
                text(
                    "INSERT INTO users (id, email, name) VALUES (:id, :email, 'Other')"
                ),
                {"id": user_id, "email": f"{user_id}@test"},
            )
        connection.execute(
            text("INSERT INTO carts (id, user_id) VALUES (:id, :user_id)"),
            {"id": cart_id, "user_id": user_id},
        )
        connection.execute(
            text(
                "INSERT INTO user_payment_methods (id, user_id, provider_token) "
                "VALUES (:id, :user_id, 'tok_other')"
            ),
            {"id": method_id, "user_id": user_id},
        )
        connection.execute(
            text("INSERT INTO products (id, name, price) VALUES (:id, 'Other', 20)"),
            {"id": product_id},
        )
        connection.execute(
            text(
                "INSERT INTO cart_items (cart_id, product_id, quantity, unit_price) "
                "VALUES (:cart, :product, 1, 20)"
            ),
            {"cart": cart_id, "product": product_id},
        )
    return cart_id, method_id


def test_two_users_can_use_same_key(
    session_factory: sessionmaker[Session], engine: Engine, pay_command: PayCart
) -> None:
    other_user_id = uuid4()
    cart_id, method_id = seed_cart(engine, other_user_id, create_user=True)
    other_command = PayCart(
        other_user_id, cart_id, method_id, pay_command.idempotency_key
    )
    first_service, _ = make_service(session_factory, pay_command)
    second_service, _ = make_service(session_factory, other_command)
    assert (
        first_service.pay(pay_command).payment.id
        != second_service.pay(other_command).payment.id
    )
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 2


def test_method_owned_by_another_user_is_rejected(
    session_factory: sessionmaker[Session], engine: Engine, pay_command: PayCart
) -> None:
    _, method_id = seed_cart(engine, uuid4(), create_user=True)
    service, gateway = make_service(session_factory, pay_command)
    with pytest.raises(PaymentError) as error:
        service.pay(replace(pay_command, payment_method_id=method_id))
    assert error.value.code == "not_found"
    assert_no_payment(session_factory, gateway)


@pytest.mark.parametrize(
    "adapter_output",
    [
        pytest.param(None, id="missing-fields"),
        pytest.param(
            SimpleNamespace(amount=Decimal("0"), currency="USD"), id="invalid-amount"
        ),
        pytest.param(
            SimpleNamespace(amount=Decimal("10"), currency="EUR"), id="invalid-currency"
        ),
        pytest.param(
            SimpleNamespace(amount="10.00", currency="USD"), id="wrong-amount-type"
        ),
    ],
)
def test_invalid_adapter_output_leaves_no_attempt(
    session_factory: sessionmaker[Session], pay_command: PayCart, adapter_output: object
) -> None:
    service, gateway = make_service(
        session_factory,
        pay_command,
        StubTotals({pay_command.cart_id: cast(Money, adapter_output)}),
    )
    with pytest.raises(TotalUnavailable):
        service.pay(pay_command)
    assert_no_payment(session_factory, gateway)


def test_competing_carts_same_user_key_recover_from_unique_violation(
    session_factory: sessionmaker[Session],
    engine: Engine,
    pay_command: PayCart,
    violated_constraints: list[str | None],
) -> None:
    cart_id, method_id = seed_cart(engine, pay_command.user_id)
    other_command = replace(pay_command, cart_id=cart_id, payment_method_id=method_id)
    barrier = Barrier(2)

    class RacingTotals:
        def get_total(self, cart_id: UUID) -> Money:
            barrier.wait(timeout=5)
            return Money(Decimal("10.00"), "USD")

    service, gateway = make_service(session_factory, pay_command, RacingTotals())

    def reserve(command: PayCart) -> PayCartResult | str:
        try:
            return service.pay(command)
        except PaymentError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(reserve, [pay_command, other_command]))
    assert violated_constraints == ["uq_payments_user_key"]
    assert sum(result == "idempotency_conflict" for result in results) == 1
    winners = [result for result in results if isinstance(result, PayCartResult)]
    assert len(winners) == 1
    winner = winners[0]
    assert winner.replayed is False
    assert winner.payment.status == PaymentStatus.PENDING
    assert (winner.payment.cart_id, winner.payment.payment_method_id) in {
        (pay_command.cart_id, pay_command.payment_method_id),
        (other_command.cart_id, other_command.payment_method_id),
    }
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
        payment = session.get(Payment, winner.payment.id)
        assert payment is not None
        assert payment.cart_id == winner.payment.cart_id
        assert payment.payment_method_id == winner.payment.payment_method_id
    assert gateway.calls == []


def test_same_cart_competition_returns_one_attempt(
    session_factory: sessionmaker[Session],
    engine: Engine,
    pay_command: PayCart,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cart_locked = Event()
    second_lock_requested = Event()
    release_reservation = Event()
    backend_pids: list[int] = []
    real_lock_cart = PaymentStore.lock_cart

    def record_lock_request(
        self: PaymentStore, session: Session, cart_id: UUID, user_id: UUID
    ) -> Cart | None:
        backend_pids.append(
            session.execute(text("SELECT pg_backend_pid()")).scalar_one()
        )
        if len(backend_pids) == 2:
            second_lock_requested.set()
        return real_lock_cart(self, session, cart_id, user_id)

    class PausingTotals:
        def get_total(self, cart_id: UUID) -> Money:
            cart_locked.set()
            assert release_reservation.wait(timeout=15), (
                "First reservation was not released"
            )
            return Money(Decimal("10.00"), "USD")

    monkeypatch.setattr(PaymentStore, "lock_cart", record_lock_request)
    service, gateway = make_service(session_factory, pay_command, PausingTotals())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_request = executor.submit(service.pay, pay_command)
        try:
            assert cart_locked.wait(timeout=5), "First request did not reach totals"
            second_request = executor.submit(service.pay, pay_command)
            assert second_lock_requested.wait(timeout=5), (
                "Second request did not reach the lock"
            )
            deadline = monotonic() + 3
            with engine.connect() as connection:
                while not connection.execute(
                    text("SELECT :first = ANY(pg_blocking_pids(:second))"),
                    {"first": backend_pids[0], "second": backend_pids[1]},
                ).scalar_one():
                    assert monotonic() < deadline, "Second request never waited on cart"
                    release_reservation.wait(timeout=0.01)
        finally:
            release_reservation.set()
        results = [first_request.result(timeout=5), second_request.result(timeout=5)]
    assert results[0].payment.id == results[1].payment.id
    assert sorted(result.replayed for result in results) == [False, True]
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert gateway.calls == []


def test_unexpected_integrity_error_remains_visible(
    session_factory: sessionmaker[Session],
    pay_command: PayCart,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, gateway = make_service(session_factory, pay_command)
    result = service.pay(pay_command)
    with session_factory.begin() as session:
        payment = session.get(Payment, result.payment.id)
        assert payment is not None
        payment.status = PaymentStatus.FAILED
    monkeypatch.setattr(
        "payment_service.services.payment_store.uuid4", lambda: result.payment.id
    )
    with pytest.raises(IntegrityError) as error:
        service.pay(replace(pay_command, idempotency_key=uuid4()))
    assert isinstance(error.value.orig, PsycopgError)
    assert error.value.orig.diag.constraint_name == "payments_pkey"
    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(Payment)) == 1
    assert gateway.calls == []


def test_blocking_unique_violation_is_rechecked_in_fresh_session(
    session_factory: sessionmaker[Session],
    pay_command: PayCart,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, gateway = make_service(session_factory, pay_command)
    service.pay(pay_command)
    real_find_blocking = PaymentStore.find_blocking
    checked_sessions: list[Session] = []

    def miss_first_check(
        self: PaymentStore, session: Session, cart_id: UUID
    ) -> Payment | None:
        checked_sessions.append(session)
        if len(checked_sessions) == 1:
            return None
        return real_find_blocking(self, session, cart_id)

    monkeypatch.setattr(PaymentStore, "find_blocking", miss_first_check)
    with pytest.raises(PaymentError) as error:
        service.pay(replace(pay_command, idempotency_key=uuid4()))
    assert error.value.code == "payment_exists"
    integrity_error = error.value.__cause__
    assert isinstance(integrity_error, IntegrityError)
    assert isinstance(integrity_error.orig, PsycopgError)
    assert integrity_error.orig.diag.constraint_name == "uq_payments_blocking_cart"
    assert len(checked_sessions) == 2
    assert checked_sessions[0] is not checked_sessions[1]
    assert gateway.calls == []


def test_store_flushes_new_payment_with_database_defaults(
    session_factory: sessionmaker[Session], pay_command: PayCart
) -> None:
    store = PaymentStore()
    with session_factory.begin() as session:
        payment = store.insert_pending(
            session, pay_command, Money(Decimal("10.00"), "USD")
        )
        assert payment.id.version == 4
        assert payment.created_at is not None
        assert payment.updated_at is not None
