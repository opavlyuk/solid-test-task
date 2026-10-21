"""Exercise durable payment safety with real concurrent HTTP requests."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from queue import Queue
from threading import Event, Lock, Thread
from uuid import UUID, uuid4

import psycopg
import pytest
from apiflask import APIFlask
from sqlalchemy import Engine, text
from sqlalchemy.pool import PoolProxiedConnection
from tests.fakes import RecordingGateway, StubTotals
from werkzeug.test import TestResponse

from payment_service import create_app
from payment_service.services.contracts import (
    ChargeDeclined,
    ChargeRequest,
    ChargeResult,
    ChargeSucceeded,
    Money,
    ProviderUnavailable,
)

pytestmark = pytest.mark.integration
WAIT = 10


class PausedGateway(RecordingGateway):
    def __init__(self) -> None:
        super().__init__(ChargeSucceeded("provider_success"))
        self.entered = Event()
        self.release = Event()
        self.both_entered = Event()

    def charge(self, request: ChargeRequest) -> ChargeResult:
        result = super().charge(request)
        with self._lock:
            if len(self.calls) == 2:
                self.both_entered.set()
        self.entered.set()
        assert self.release.wait(WAIT), "Gateway was not released"
        return result


class RacingTotals(StubTotals):
    """Hold both cart reservations before either can insert its payment."""

    def __init__(self, totals: dict[UUID, Money]) -> None:
        super().__init__(totals)
        self.lock = Lock()
        self.count = 0
        self.both_entered = Event()
        self.release = Event()

    def get_total(self, cart_id: UUID) -> Money:
        with self.lock:
            self.count += 1
            if self.count == 2:
                self.both_entered.set()
        assert self.release.wait(WAIT), "Totals were not released"
        return super().get_total(cart_id)


class RequestWorker:
    """Keep worker exceptions visible and make cleanup bounded."""

    def __init__(self, request: Callable[[], TestResponse]) -> None:
        self.results: Queue[TestResponse | BaseException] = Queue()

        def run() -> None:
            try:
                self.results.put(request())
            except BaseException as error:
                self.results.put(error)

        self.thread = Thread(target=run, daemon=True)
        self.thread.start()

    def response(self) -> TestResponse:
        result = self.results.get(timeout=WAIT)
        if isinstance(result, BaseException):
            raise result
        return result


def join_workers(workers: list[RequestWorker]) -> None:
    """Attempt every bounded join before reporting unfinished workers."""
    for worker in workers:
        worker.thread.join(timeout=WAIT)
    assert not any(worker.thread.is_alive() for worker in workers), (
        "HTTP workers did not finish"
    )


@pytest.fixture
def app_factory(
    database_url: str, populated_cart: dict[str, UUID]
) -> Iterator[Callable[..., APIFlask]]:
    apps: list[APIFlask] = []

    def build(gateway: RecordingGateway, totals: StubTotals | None = None) -> APIFlask:
        app = create_app(
            {"DATABASE_URL": database_url, "ALLOW_DEMO_AUTH": True},
            gateway=gateway,
            totals=totals
            if totals is not None
            else StubTotals(
                {populated_cart["cart_id"]: Money(Decimal("70.00"), "USD")}
            ),
        )
        apps.append(app)
        return app

    yield build
    for app in apps:
        app.extensions["engine"].dispose()


def post(app: APIFlask, shop: dict[str, UUID], key: UUID) -> TestResponse:
    # Each request owns a client. The service opens fresh sessions per transaction.
    with app.test_client() as client:
        return client.post(
            f"/carts/{shop['cart_id']}/payments",
            headers={"X-User-ID": str(shop["user_id"]), "Idempotency-Key": str(key)},
            json={"payment_method_id": str(shop["method_id"])},
        )


def rows(engine: Engine) -> tuple[list[dict], dict[UUID, str]]:
    """Read durable state using the independent fixture engine."""
    with engine.connect() as connection:
        payments = [
            dict(row)
            for row in connection.execute(
                text("SELECT id, cart_id, amount, currency, status FROM payments")
            ).mappings()
        ]
        carts = {
            cart_id: status
            for cart_id, status in connection.execute(
                text("SELECT id, status FROM carts")
            )
        }
    return payments, carts


def second_cart(engine: Engine, shop: dict[str, UUID]) -> dict[str, UUID]:
    other = {**shop, "cart_id": uuid4()}
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO carts (id, user_id) VALUES (:cart_id, :user_id)"), other
        )
        connection.execute(
            text(
                "INSERT INTO cart_items (cart_id, product_id, quantity, unit_price) "
                "SELECT :cart_id, product_id, quantity, unit_price FROM cart_items "
                "WHERE cart_id=:original"
            ),
            {**other, "original": shop["cart_id"]},
        )
    return other


@pytest.mark.parametrize("same_key", [True, False])
def test_competing_request_while_gateway_paused(
    app_factory: Callable[..., APIFlask],
    engine: Engine,
    shop: dict[str, UUID],
    same_key: bool,
) -> None:
    gateway = PausedGateway()
    app = app_factory(gateway)
    key = uuid4()
    workers = [RequestWorker(lambda: post(app, shop, key))]
    try:
        assert gateway.entered.wait(WAIT)
        # NOWAIT fails immediately if transaction A still owns the cart lock.
        with engine.begin() as connection:
            connection.execute(
                text("SELECT id FROM carts WHERE id=:id FOR UPDATE NOWAIT"),
                {"id": shop["cart_id"]},
            )
        contender_key = key if same_key else uuid4()
        workers.append(RequestWorker(lambda: post(app, shop, contender_key)))
        response = workers[1].response()
        assert response.status_code == (202 if same_key else 409)
        payments, carts = rows(engine)
        assert len(payments) == 1
        assert payments[0]["status"] == "pending"
        assert carts[shop["cart_id"]] == "active"
        if same_key:
            assert response.get_json()["id"] == str(payments[0]["id"])
            assert response.get_json()["status"] == "pending"
        else:
            assert response.get_json()["error"]["code"] == "payment_exists"
        assert len(gateway.calls) == 1
        gateway.release.set()
        success = workers[0].response()
        assert success.status_code == 201
        replay = post(app, shop, key)
        assert replay.status_code == 200
        assert replay.get_json() == success.get_json()
        payments, carts = rows(engine)
        assert len(payments) == 1
        assert payments[0]["status"] == "succeeded"
        assert carts[shop["cart_id"]] == "checked_out"
        assert len(gateway.calls) == 1
    finally:
        gateway.release.set()
        join_workers(workers)


def test_same_user_key_races_across_carts(
    app_factory: Callable[..., APIFlask], engine: Engine, shop: dict[str, UUID]
) -> None:
    other = second_cart(engine, shop)
    totals = RacingTotals(
        {
            shop["cart_id"]: Money(Decimal("70.00"), "USD"),
            other["cart_id"]: Money(Decimal("23.45"), "USD"),
        }
    )
    gateway = RecordingGateway(ChargeSucceeded("success"))
    app = app_factory(gateway, totals)
    key = uuid4()
    workers = [
        RequestWorker(lambda: post(app, shop, key)),
        RequestWorker(lambda: post(app, other, key)),
    ]
    try:
        assert totals.both_entered.wait(WAIT)
        totals.release.set()
        responses = [worker.response() for worker in workers]
        assert sorted(response.status_code for response in responses) == [201, 409]
        winner = next(response for response in responses if response.status_code == 201)
        loser = next(response for response in responses if response.status_code == 409)
        assert loser.get_json()["error"]["code"] == "idempotency_conflict"
        payments, carts = rows(engine)
        assert len(payments) == len(gateway.calls) == 1
        assert str(payments[0]["id"]) == winner.get_json()["id"]
        assert payments[0]["status"] == "succeeded"
        assert str(payments[0]["cart_id"]) == winner.get_json()["cart_id"]
        assert gateway.calls[0].payment_id == payments[0]["id"]
        assert gateway.calls[0].total.amount == payments[0]["amount"]
        winning_cart = payments[0]["cart_id"]
        losing_cart = next(cart_id for cart_id in carts if cart_id != winning_cart)
        assert carts[winning_cart] == "checked_out"
        assert carts[losing_cart] == "active"
        assert payments[0]["amount"] == totals.totals_by_cart[winning_cart].amount
        assert winner.get_json()["amount"] == str(payments[0]["amount"])
    finally:
        totals.release.set()
        join_workers(workers)


def test_unrelated_payments_overlap_in_gateway(
    app_factory: Callable[..., APIFlask], engine: Engine, shop: dict[str, UUID]
) -> None:
    other = second_cart(engine, shop)
    gateway = PausedGateway()
    app = app_factory(
        gateway,
        StubTotals(
            {
                shop["cart_id"]: Money(Decimal("70.00"), "USD"),
                other["cart_id"]: Money(Decimal("23.45"), "USD"),
            }
        ),
    )
    workers = [
        RequestWorker(lambda: post(app, shop, uuid4())),
        RequestWorker(lambda: post(app, other, uuid4())),
    ]
    try:
        assert gateway.both_entered.wait(WAIT)
        payments, carts = rows(engine)
        assert len(payments) == len(gateway.calls) == 2
        assert {payment["status"] for payment in payments} == {"pending"}
        assert set(carts.values()) == {"active"}
        gateway.release.set()
        responses = [worker.response() for worker in workers]
        assert [response.status_code for response in responses] == [201, 201]
        assert {response.get_json()["amount"] for response in responses} == {
            "70.00",
            "23.45",
        }
        payments, carts = rows(engine)
        assert len(payments) == 2
        assert {payment["status"] for payment in payments} == {"succeeded"}
        assert set(carts.values()) == {"checked_out"}
    finally:
        gateway.release.set()
        join_workers(workers)


@pytest.mark.parametrize(
    "target_commit",
    [pytest.param(1, id="reservation"), pytest.param(2, id="finalization")],
)
def test_failure_before_commit(
    app_factory: Callable[..., APIFlask],
    engine: Engine,
    shop: dict[str, UUID],
    monkeypatch: pytest.MonkeyPatch,
    target_commit: int,
) -> None:
    gateway = RecordingGateway(ChargeSucceeded("success"))
    app = app_factory(gateway)
    app_engine = app.extensions["engine"]
    real_commit = app_engine.dialect.do_commit
    count = 0

    def fail_before_commit(connection: PoolProxiedConnection) -> None:
        nonlocal count
        count += 1
        if count == target_commit:
            raise psycopg.OperationalError("Simulated pre-commit failure")
        real_commit(connection)

    key = uuid4()
    with monkeypatch.context() as patch:
        patch.setattr(app_engine.dialect, "do_commit", fail_before_commit)
        response = post(app, shop, key)
    assert count == target_commit
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "service_unavailable"
    payments, carts = rows(engine)
    assert carts[shop["cart_id"]] == "active"
    assert len(gateway.calls) == target_commit - 1
    if target_commit == 1:
        assert payments == []
    else:
        assert len(payments) == 1
        assert payments[0]["status"] == "pending"
        replay = post(app, shop, key)
        assert replay.status_code == 202
        assert replay.get_json()["id"] == str(payments[0]["id"])
        assert len(gateway.calls) == 1
        assert rows(engine) == (payments, carts)


def assert_durable_replay(
    app: APIFlask,
    engine: Engine,
    shop: dict[str, UUID],
    key: UUID,
    gateway: RecordingGateway,
    response: TestResponse,
    *,
    payment_status: str,
    cart_status: str,
    replay_status: int,
    gateway_calls: int,
) -> None:
    """Verify an acknowledged failure and a safe replay of committed state."""
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "service_unavailable"
    payments, carts = rows(engine)
    assert len(payments) == 1
    payment = payments[0]
    assert payment["status"] == payment_status
    assert carts[shop["cart_id"]] == cart_status
    assert len(gateway.calls) == gateway_calls
    replay = post(app, shop, key)
    assert replay.status_code == replay_status
    assert replay.get_json()["id"] == str(payment["id"])
    assert replay.get_json()["amount"] == "70.00"
    assert replay.get_json()["currency"] == "USD"
    assert replay.get_json()["status"] == payment["status"]
    assert len(gateway.calls) == gateway_calls
    assert rows(engine) == (payments, carts)


def test_reservation_commit_ack_lost(
    app_factory: Callable[..., APIFlask],
    engine: Engine,
    shop: dict[str, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_commit = 1
    gateway = RecordingGateway(ChargeSucceeded("success"))
    app = app_factory(gateway)
    app_engine = app.extensions["engine"]
    real_commit = app_engine.dialect.do_commit
    commit_count = 0
    fault_raised = False

    def commit_then_lose_ack(connection: PoolProxiedConnection) -> None:
        nonlocal commit_count, fault_raised
        real_commit(connection)
        commit_count += 1
        if commit_count == target_commit:
            fault_raised = True
            raise psycopg.OperationalError("Simulated lost commit acknowledgment")

    key = uuid4()
    with monkeypatch.context() as patch:
        patch.setattr(app_engine.dialect, "do_commit", commit_then_lose_ack)
        response = post(app, shop, key)
    assert fault_raised
    assert_durable_replay(
        app,
        engine,
        shop,
        key,
        gateway,
        response,
        payment_status="pending",
        cart_status="active",
        replay_status=202,
        gateway_calls=0,
    )


def test_finalization_commit_ack_lost(
    app_factory: Callable[..., APIFlask],
    engine: Engine,
    shop: dict[str, UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_commit = 2
    gateway = RecordingGateway(ChargeSucceeded("success"))
    app = app_factory(gateway)
    app_engine = app.extensions["engine"]
    real_commit = app_engine.dialect.do_commit
    commit_count = 0
    fault_raised = False

    def commit_then_lose_ack(connection: PoolProxiedConnection) -> None:
        nonlocal commit_count, fault_raised
        real_commit(connection)
        commit_count += 1
        if commit_count == target_commit:
            fault_raised = True
            raise psycopg.OperationalError("Simulated lost commit acknowledgment")

    key = uuid4()
    with monkeypatch.context() as patch:
        patch.setattr(app_engine.dialect, "do_commit", commit_then_lose_ack)
        response = post(app, shop, key)
    assert fault_raised
    assert_durable_replay(
        app,
        engine,
        shop,
        key,
        gateway,
        response,
        payment_status="succeeded",
        cart_status="checked_out",
        replay_status=200,
        gateway_calls=1,
    )


def test_lost_success_delivery_replays_stored_result(
    app_factory: Callable[..., APIFlask], engine: Engine, shop: dict[str, UUID]
) -> None:
    gateway = RecordingGateway(ChargeSucceeded("success"))
    app = app_factory(gateway)
    key = uuid4()
    post(app, shop, key)  # The caller never receives this response.
    payments, carts = rows(engine)
    assert len(payments) == 1
    assert payments[0]["status"] == "succeeded"
    assert carts[shop["cart_id"]] == "checked_out"
    response = post(app, shop, key)
    assert response.status_code == 200
    assert response.get_json()["id"] == str(payments[0]["id"])
    assert response.get_json()["amount"] == "70.00"
    assert response.get_json()["currency"] == "USD"
    assert response.get_json()["status"] == "succeeded"
    assert len(gateway.calls) == 1
    assert rows(engine) == (payments, carts)


@pytest.mark.parametrize(
    "outcome,status,stored,failure",
    [
        pytest.param(ChargeDeclined(), 402, "failed", "card_declined", id="declined"),
        pytest.param(
            ProviderUnavailable(),
            202,
            "unknown",
            "provider_unavailable",
            id="provider-unavailable",
        ),
    ],
)
def test_provider_failure_is_replayed_without_charging(
    app_factory: Callable[..., APIFlask],
    engine: Engine,
    shop: dict[str, UUID],
    outcome: ChargeResult | ProviderUnavailable,
    status: int,
    stored: str,
    failure: str,
) -> None:
    gateway = RecordingGateway(outcome)
    app = app_factory(gateway)
    key = uuid4()
    first = post(app, shop, key)
    assert first.status_code == status
    assert first.get_json()["status"] == stored
    assert first.get_json()["failure_code"] == failure
    replay = post(app, shop, key)
    assert replay.status_code == status
    assert replay.get_json() == first.get_json()
    payments, carts = rows(engine)
    assert len(payments) == len(gateway.calls) == 1
    assert payments[0]["status"] == stored
    assert str(payments[0]["id"]) == first.get_json()["id"]
    assert carts[shop["cart_id"]] == "active"
    if stored == "unknown":
        bypass = post(app, shop, uuid4())
        assert bypass.status_code == 409
        assert bypass.get_json()["error"]["code"] == "payment_exists"
        assert len(gateway.calls) == 1
        assert rows(engine) == (payments, carts)
