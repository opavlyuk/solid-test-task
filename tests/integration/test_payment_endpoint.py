from decimal import Decimal
from http import HTTPStatus
from uuid import UUID, uuid4

import pytest
from apiflask import APIFlask
from flask.testing import FlaskClient
from sqlalchemy import Engine, text
from tests.fakes import RecordingGateway, StubTotals

from payment_service import create_app
from payment_service.services.contracts import Money

pytestmark = pytest.mark.integration


@pytest.fixture
def headers(shop: dict[str, UUID]) -> dict[str, str]:
    return {"X-User-ID": str(shop["user_id"]), "Idempotency-Key": str(uuid4())}


def test_success_and_replay(
    client: FlaskClient, shop: dict[str, UUID], headers: dict[str, str]
) -> None:
    url = f"/carts/{shop['cart_id']}/payments"
    response = client.post(
        url, headers=headers, json={"payment_method_id": str(shop["method_id"])}
    )
    assert response.status_code == HTTPStatus.CREATED
    assert response.json == {
        "id": response.get_json()["id"],
        "cart_id": str(shop["cart_id"]),
        "payment_method_id": str(shop["method_id"]),
        "amount": "70.00",
        "currency": "USD",
        "status": "succeeded",
        "failure_code": None,
    }
    replay = client.post(
        url, headers=headers, json={"payment_method_id": str(shop["method_id"])}
    )
    assert replay.status_code == HTTPStatus.OK
    assert replay.json == response.json
    assert "Location" not in response.headers


@pytest.mark.parametrize(
    "field,value",
    [("payment_method_id", "bad"), ("amount", "10"), ("provider_token", "secret")],
)
def test_invalid_body(
    client: FlaskClient,
    shop: dict[str, UUID],
    headers: dict[str, str],
    field: str,
    value: str,
) -> None:
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"]), field: value},
    )
    assert response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT
    assert response.get_json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    "mutation,status,code",
    [
        ("missing_key", HTTPStatus.UNPROCESSABLE_CONTENT, "validation_error"),
        ("bad_key", HTTPStatus.UNPROCESSABLE_CONTENT, "validation_error"),
        ("bad_cart", HTTPStatus.UNPROCESSABLE_CONTENT, "validation_error"),
        ("missing_method", HTTPStatus.UNPROCESSABLE_CONTENT, "validation_error"),
        ("missing_identity", HTTPStatus.UNAUTHORIZED, "unauthorized"),
        ("bad_identity", HTTPStatus.UNAUTHORIZED, "unauthorized"),
        ("unknown_identity", HTTPStatus.UNAUTHORIZED, "unauthorized"),
        ("missing_cart", HTTPStatus.NOT_FOUND, "not_found"),
        ("missing_method_resource", HTTPStatus.NOT_FOUND, "not_found"),
        ("malformed", HTTPStatus.BAD_REQUEST, "invalid_json"),
        ("text", HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "unsupported_media_type"),
    ],
)
def test_request_errors(
    client: FlaskClient,
    shop: dict[str, UUID],
    headers: dict[str, str],
    mutation: str,
    status: int,
    code: str,
) -> None:
    cart = str(shop["cart_id"])
    body = {"payment_method_id": str(shop["method_id"])}
    if mutation == "missing_key":
        headers.pop("Idempotency-Key")
    if mutation == "bad_key":
        headers["Idempotency-Key"] = "bad"
    if mutation == "bad_cart":
        cart = "bad"
    if mutation == "missing_cart":
        cart = str(uuid4())
    if mutation == "missing_method":
        body = {}
    if mutation == "missing_method_resource":
        body["payment_method_id"] = str(uuid4())
    if mutation == "missing_identity":
        headers.pop("X-User-ID")
    if mutation == "bad_identity":
        headers["X-User-ID"] = "bad"
    if mutation == "unknown_identity":
        headers["X-User-ID"] = str(uuid4())
    url = f"/carts/{cart}/payments"
    if mutation in {"malformed", "text"}:
        response = client.post(
            url,
            headers=headers,
            data="{",
            content_type="application/json"
            if mutation == "malformed"
            else "text/plain",
        )
    else:
        response = client.post(url, headers=headers, json=body)
    assert response.status_code == status
    assert response.get_json()["error"]["code"] == code
    assert set(response.get_json()) == {"error"}


@pytest.mark.parametrize(
    "stored,status,failure",
    [
        ("pending", HTTPStatus.ACCEPTED, None),
        ("unknown", HTTPStatus.ACCEPTED, "provider_unavailable"),
        ("failed", HTTPStatus.PAYMENT_REQUIRED, "card_declined"),
    ],
)
def test_stored_status_replay(
    client: FlaskClient,
    engine: Engine,
    shop: dict[str, UUID],
    headers: dict[str, str],
    stored: str,
    status: int,
    failure: str | None,
) -> None:
    payment_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO payments (id, user_id, cart_id, payment_method_id, "
                "idempotency_key, amount, currency, status) "
                "VALUES (:id, :user_id, :cart_id, :method_id, :key, 70, 'USD', :status)"
            ),
            {
                **shop,
                "id": payment_id,
                "key": UUID(headers["Idempotency-Key"]),
                "status": stored,
            },
        )
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"])},
    )
    assert response.status_code == status
    assert response.get_json()["failure_code"] == failure
    assert response.get_json()["id"] == str(payment_id)
    assert "Location" not in response.headers


def test_openapi(endpoint_app: APIFlask) -> None:
    spec = endpoint_app.spec
    assert isinstance(spec, dict)
    operation = spec["paths"]["/carts/{cart_id}/payments"]["post"]
    assert operation["requestBody"]["required"] is True
    assert set(spec["components"]["schemas"]["PaymentOutput"]["required"]) == {
        "id",
        "cart_id",
        "payment_method_id",
        "amount",
        "currency",
        "status",
        "failure_code",
    }
    assert set(operation["responses"]) >= {
        str(HTTPStatus.OK),
        str(HTTPStatus.CREATED),
        str(HTTPStatus.ACCEPTED),
        str(HTTPStatus.PAYMENT_REQUIRED),
        str(HTTPStatus.BAD_REQUEST),
        str(HTTPStatus.UNAUTHORIZED),
        str(HTTPStatus.NOT_FOUND),
        str(HTTPStatus.CONFLICT),
        str(HTTPStatus.UNSUPPORTED_MEDIA_TYPE),
        str(HTTPStatus.UNPROCESSABLE_CONTENT),
        str(HTTPStatus.INTERNAL_SERVER_ERROR),
        str(HTTPStatus.SERVICE_UNAVAILABLE),
    }
    for code in (
        str(HTTPStatus.OK),
        str(HTTPStatus.CREATED),
        str(HTTPStatus.ACCEPTED),
        str(HTTPStatus.PAYMENT_REQUIRED),
    ):
        assert operation["responses"][code]["content"]["application/json"]["schema"][
            "$ref"
        ].endswith("/PaymentOutput")
    assert {
        (p["name"], p["in"], p["schema"]["format"]) for p in operation["parameters"]
    } >= {
        ("cart_id", "path", "uuid"),
        ("Idempotency-Key", "header", "uuid"),
        ("X-User-ID", "header", "uuid"),
    }
    identity_headers = [
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "X-User-ID"
    ]
    assert len(identity_headers) == 1
    assert identity_headers[0]["required"] is True


@pytest.mark.parametrize("database", [False, True])
def test_safe_error_logging(
    endpoint_app: APIFlask,
    client: FlaskClient,
    shop: dict[str, UUID],
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    database: bool,
) -> None:
    from sqlalchemy.exc import OperationalError

    def fail(command: object) -> None:
        if database:
            raise OperationalError(
                "sql secret_token",
                {"token": "secret_token"},
                RuntimeError("secret_token"),
            )
        raise RuntimeError("secret_token")

    monkeypatch.setattr(endpoint_app.extensions["payment_service"], "pay", fail)
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"])},
    )
    assert response.status_code == (
        HTTPStatus.SERVICE_UNAVAILABLE if database else HTTPStatus.INTERNAL_SERVER_ERROR
    )
    assert response.get_json()["error"]["code"] == (
        "service_unavailable" if database else "internal_error"
    )
    assert "secret_token" not in response.get_data(as_text=True) + caplog.text
    assert str(shop["cart_id"]) in caplog.text


@pytest.mark.parametrize(
    "token,status,failure",
    [
        ("tok_test_declined", HTTPStatus.PAYMENT_REQUIRED, "card_declined"),
        ("tok_test_timeout", HTTPStatus.ACCEPTED, "provider_unavailable"),
    ],
)
def test_provider_outcome(
    database_url: str,
    endpoint_app: APIFlask,
    engine: Engine,
    shop: dict[str, UUID],
    headers: dict[str, str],
    token: str,
    status: int,
    failure: str,
) -> None:
    from tests.fakes import StubTotals

    with engine.begin() as connection:
        connection.execute(
            text("UPDATE user_payment_methods SET provider_token=:token"),
            {"token": token},
        )
    app = create_app(
        {"DATABASE_URL": database_url, "ALLOW_DEMO_AUTH": True},
        totals=StubTotals({shop["cart_id"]: Money(Decimal("70"), "USD")}),
    )
    try:
        client = app.test_client()
        response = client.post(
            f"/carts/{shop['cart_id']}/payments",
            headers=headers,
            json={"payment_method_id": str(shop["method_id"])},
        )
        assert response.status_code == status
        assert response.get_json()["failure_code"] == failure
        replay = client.post(
            f"/carts/{shop['cart_id']}/payments",
            headers=headers,
            json={"payment_method_id": str(shop["method_id"])},
        )
        assert replay.status_code == status
        assert replay.get_json() == response.get_json()
        assert token not in response.get_data(as_text=True)
    finally:
        app.extensions["engine"].dispose()


@pytest.mark.parametrize("resource", ["cart", "method"])
def test_foreign_resources(
    client: FlaskClient,
    engine: Engine,
    shop: dict[str, UUID],
    headers: dict[str, str],
    resource: str,
) -> None:
    other_id = uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name) "
                "VALUES (:id, 'other@test', 'Other')"
            ),
            {"id": other_id},
        )
        table = "carts" if resource == "cart" else "user_payment_methods"
        connection.execute(text(f"UPDATE {table} SET user_id=:id"), {"id": other_id})
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"])},
    )
    assert response.status_code == HTTPStatus.NOT_FOUND
    assert response.get_json()["error"]["code"] == "not_found"


def test_total_unavailable(
    database_url: str,
    endpoint_app: APIFlask,
    shop: dict[str, UUID],
    headers: dict[str, str],
    engine: Engine,
) -> None:
    app = create_app(
        {"DATABASE_URL": database_url, "ALLOW_DEMO_AUTH": True}, totals=StubTotals({})
    )
    try:
        response = app.test_client().post(
            f"/carts/{shop['cart_id']}/payments",
            headers=headers,
            json={"payment_method_id": str(shop["method_id"])},
        )
        assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
        assert response.get_json()["error"]["code"] == "total_unavailable"
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM payments")) == 0
    finally:
        app.extensions["engine"].dispose()


def test_falsey_collaborators(
    database_url: str,
    endpoint_app: APIFlask,
    shop: dict[str, UUID],
    headers: dict[str, str],
) -> None:
    from payment_service.services.contracts import ChargeDeclined

    class FalseyTotals(StubTotals):
        def __bool__(self) -> bool:
            return False

    class FalseyGateway(RecordingGateway):
        def __bool__(self) -> bool:
            return False

    app = create_app(
        {"DATABASE_URL": database_url, "ALLOW_DEMO_AUTH": "true"},
        totals=FalseyTotals({shop["cart_id"]: Money(Decimal("12.34"), "USD")}),
        gateway=FalseyGateway(ChargeDeclined()),
    )
    try:
        response = app.test_client().post(
            f"/carts/{shop['cart_id']}/payments",
            headers=headers,
            json={"payment_method_id": str(shop["method_id"])},
        )
        assert response.status_code == HTTPStatus.PAYMENT_REQUIRED
        assert response.get_json()["amount"] == "12.34"
    finally:
        app.extensions["engine"].dispose()


@pytest.mark.parametrize(
    "error_type", ["InvalidRequestError", "CompileError", "ProgrammingError"]
)
def test_database_programming_errors_are_internal(
    endpoint_app: APIFlask,
    client: FlaskClient,
    shop: dict[str, UUID],
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
) -> None:
    from sqlalchemy import exc

    error_class = getattr(exc, error_type)
    error = (
        error_class("secret", {}, RuntimeError("secret"))
        if error_type == "ProgrammingError"
        else error_class("secret")
    )

    def fail(command: object) -> None:
        raise error

    monkeypatch.setattr(endpoint_app.extensions["payment_service"], "pay", fail)
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"])},
    )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR
    assert response.get_json()["error"]["code"] == "internal_error"
    assert "secret" not in response.get_data(as_text=True)


@pytest.mark.parametrize(
    "state,code",
    [
        ("empty", "empty_cart"),
        ("abandoned", "cart_not_active"),
        ("checked_out", "cart_not_active"),
        ("blocking", "payment_exists"),
        ("reused_key", "idempotency_conflict"),
    ],
)
def test_conflicts(
    client: FlaskClient,
    engine: Engine,
    shop: dict[str, UUID],
    headers: dict[str, str],
    state: str,
    code: str,
) -> None:
    with engine.begin() as connection:
        if state == "empty":
            connection.execute(text("DELETE FROM cart_items"))
        elif state in {"abandoned", "checked_out"}:
            connection.execute(
                text("UPDATE carts SET status=:status"), {"status": state}
            )
    if state in {"blocking", "reused_key"}:
        response = client.post(
            f"/carts/{shop['cart_id']}/payments",
            headers=headers,
            json={"payment_method_id": str(shop["method_id"])},
        )
        assert response.status_code == HTTPStatus.CREATED
        if state == "blocking":
            # Restore eligibility to reach the separate blocking-payment check.
            with engine.begin() as connection:
                connection.execute(text("UPDATE carts SET status='active'"))
            headers["Idempotency-Key"] = str(uuid4())
        else:
            shop = {**shop, "method_id": uuid4()}
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"])},
    )
    assert response.status_code == HTTPStatus.CONFLICT
    body = response.get_json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]


@pytest.mark.parametrize("error_type", ["DBAPIError", "IntegrityError", "DataError"])
def test_database_transaction_errors_are_unavailable(
    endpoint_app: APIFlask,
    client: FlaskClient,
    shop: dict[str, UUID],
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    error_type: str,
) -> None:
    from sqlalchemy import exc

    error = getattr(exc, error_type)(
        "secret sql", {"token": "secret"}, RuntimeError("secret")
    )

    def fail(command: object) -> None:
        raise error

    monkeypatch.setattr(endpoint_app.extensions["payment_service"], "pay", fail)
    response = client.post(
        f"/carts/{shop['cart_id']}/payments",
        headers=headers,
        json={"payment_method_id": str(shop["method_id"])},
    )
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
    assert response.get_json()["error"]["code"] == "service_unavailable"
    assert "secret" not in response.get_data(as_text=True)
