"""Expose the cart payment use case over HTTP."""

from http import HTTPStatus
from typing import Any
from uuid import UUID

from apiflask import APIFlask
from flask import g, request

from payment_service.api.auth import demo_user_id
from payment_service.api.schemas import (
    CartInput,
    ErrorOutput,
    KeyInput,
    PaymentInput,
    PaymentOutput,
)
from payment_service.payment_status import PaymentStatus
from payment_service.services.contracts import PayCart
from payment_service.services.payments import PaymentService


def register_payments(app: APIFlask, service: PaymentService) -> None:
    @app.spec_processor
    def document_payment_request(spec: dict[str, Any] | str) -> dict[str, Any] | str:
        if isinstance(spec, str):
            return spec
        operation = spec["paths"]["/carts/{cart_id}/payments"]["post"]
        operation["requestBody"]["required"] = True
        # Identity is validated by the auth adapter to retain unauthorized responses.
        operation["parameters"].append(
            {
                "name": "X-User-ID",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "format": "uuid"},
                "description": "Demo identity, accepted when demo auth is enabled.",
            }
        )
        return spec

    @app.before_request
    def authenticate_payment() -> None:
        if request.endpoint == "pay_cart" and request.method == "POST":
            g.payment_user_id = demo_user_id()
            # Parse JSON before schema validation to preserve malformed-JSON errors.
            request.get_json()

    @app.post("/carts/<cart_id>/payments")
    @app.input(CartInput, location="path")
    @app.input(KeyInput, location="headers")
    @app.input(PaymentInput, location="json", unknown="raise")
    @app.output(PaymentOutput, status_code=HTTPStatus.CREATED)
    @app.doc(
        responses={
            **{
                code: {
                    "description": "Stored payment attempt",
                    "content": {"application/json": {"schema": PaymentOutput}},
                }
                for code in (
                    HTTPStatus.OK,
                    HTTPStatus.ACCEPTED,
                    HTTPStatus.PAYMENT_REQUIRED,
                )
            },
            **{
                code: {
                    "description": "Request failed",
                    "content": {"application/json": {"schema": ErrorOutput}},
                }
                for code in (
                    HTTPStatus.BAD_REQUEST,
                    HTTPStatus.UNAUTHORIZED,
                    HTTPStatus.NOT_FOUND,
                    HTTPStatus.CONFLICT,
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    HTTPStatus.UNPROCESSABLE_CONTENT,
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    HTTPStatus.SERVICE_UNAVAILABLE,
                )
            },
        }
    )
    def pay_cart(
        cart_id: str,
        path_data: dict[str, UUID],
        headers_data: dict[str, UUID],
        json_data: dict[str, UUID],
    ) -> tuple[dict[str, Any], int]:
        result = service.pay(
            PayCart(
                g.payment_user_id,
                path_data["cart_id"],
                json_data["payment_method_id"],
                headers_data["idempotency_key"],
            )
        )
        payment = result.payment
        status = {
            PaymentStatus.SUCCEEDED: HTTPStatus.OK
            if result.replayed
            else HTTPStatus.CREATED,
            PaymentStatus.FAILED: HTTPStatus.PAYMENT_REQUIRED,
            PaymentStatus.PENDING: HTTPStatus.ACCEPTED,
            PaymentStatus.UNKNOWN: HTTPStatus.ACCEPTED,
        }[payment.status]
        return {
            "id": payment.id,
            "cart_id": payment.cart_id,
            "payment_method_id": payment.payment_method_id,
            "amount": format(payment.total.amount, ".2f"),
            "currency": payment.total.currency,
            "status": payment.status.value,
            "failure_code": payment.failure_code,
        }, status
