from http import HTTPStatus
from typing import get_args
from uuid import uuid4

import pytest
from apiflask import APIFlask

from payment_service import create_app
from payment_service.api.errors import register_errors
from payment_service.services.contracts import PaymentError, PaymentErrorCode


@pytest.mark.parametrize("code", get_args(PaymentErrorCode.__value__))
def test_every_business_error_has_an_http_response(code: PaymentErrorCode) -> None:
    app = APIFlask(__name__)
    register_errors(app)

    @app.get("/failure")
    def fail() -> None:
        raise PaymentError(code)

    response = app.test_client().get("/failure")
    expected_status = {
        "unauthorized": HTTPStatus.UNAUTHORIZED,
        "not_found": HTTPStatus.NOT_FOUND,
    }.get(code, HTTPStatus.CONFLICT)
    assert response.status_code == expected_status
    body = response.get_json()
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str)
    assert body["error"]["message"]


@pytest.mark.parametrize("value", ["perhaps", 2, None, "", [], {}])
def test_rejects_invalid_demo_auth(value: object) -> None:
    with pytest.raises(ValueError, match="ALLOW_DEMO_AUTH"):
        create_app({"DATABASE_URL": "postgresql+psycopg://", "ALLOW_DEMO_AUTH": value})


def test_auth_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_DEMO_AUTH", raising=False)
    app = create_app({"DATABASE_URL": "postgresql+psycopg://"})
    response = app.test_client().post(f"/carts/{uuid4()}/payments", json={})
    assert response.status_code == HTTPStatus.UNAUTHORIZED
    assert response.get_json()["error"]["code"] == "unauthorized"
    assert app.extensions["engine"].hide_parameters


def test_database_url_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        create_app()


def test_unsupported_method_preserves_allowed_methods() -> None:
    app = create_app({"DATABASE_URL": "postgresql+psycopg://"})
    response = app.test_client().get(f"/carts/{uuid4()}/payments")

    assert response.status_code == HTTPStatus.METHOD_NOT_ALLOWED
    assert response.allow == {"POST", "OPTIONS"}
    assert response.get_json()["error"]["code"] == "method_not_allowed"


@pytest.mark.parametrize(
    ("demo_auth", "identity"),
    [(False, None), (True, None), (True, "invalid"), (True, str(uuid4()))],
)
def test_options_does_not_require_identity_or_json(
    demo_auth: bool, identity: str | None
) -> None:
    app = create_app(
        {"DATABASE_URL": "postgresql+psycopg://", "ALLOW_DEMO_AUTH": demo_auth}
    )
    response = app.test_client().options(
        f"/carts/{uuid4()}/payments",
        headers={} if identity is None else {"X-User-ID": identity},
    )

    assert response.status_code == HTTPStatus.OK
    assert response.allow == {"POST", "OPTIONS"}
    assert response.data == b""


@pytest.mark.parametrize(
    "url",
    ["invalid", "postgresql+psycopg://localhost:not-a-port/db", "sqlite:///db"],
)
def test_invalid_database_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="DATABASE_URL"):
        create_app({"DATABASE_URL": url})


def test_unexpected_url_parser_errors_are_not_hidden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from payment_service import config

    def broken_parser(url: str) -> None:
        raise RuntimeError("Parser bug")

    monkeypatch.setattr(config, "make_url", broken_parser)
    with pytest.raises(RuntimeError, match="Parser bug"):
        create_app({"DATABASE_URL": "postgresql+psycopg://"})
