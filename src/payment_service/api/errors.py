"""Return consistent API errors without exposing sensitive exception details."""

from http import HTTPStatus
from uuid import UUID

from apiflask import APIFlask, HTTPError
from apiflask.types import ResponseHeaderType
from flask import request
from sqlalchemy.exc import (
    DataError,
    DBAPIError,
    DisconnectionError,
    IntegrityError,
    ProgrammingError,
    TimeoutError,
)

from payment_service.services.contracts import PaymentError, TotalUnavailable

MESSAGES = {
    "unauthorized": "A valid identity is required.",
    "not_found": "Resource not found.",
    "method_not_allowed": "This HTTP method is not allowed.",
    "cart_not_active": "The cart is not active.",
    "empty_cart": "The cart is empty.",
    "payment_exists": "The cart already has a blocking payment.",
    "idempotency_conflict": "This key was already used for another payment request.",
    "validation_error": "Request validation failed.",
    "invalid_json": "Malformed JSON.",
    "unsupported_media_type": "A JSON body is required.",
    "total_unavailable": "The cart total is unavailable.",
    "service_unavailable": "The payment service is unavailable.",
    "internal_error": "An internal error occurred.",
}


def error_body(code: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": MESSAGES[code]}}


def register_errors(app: APIFlask) -> None:
    @app.error_processor
    def http_error(
        error: HTTPError,
    ) -> tuple[dict[str, dict[str, str]], int, ResponseHeaderType]:
        code = {
            HTTPStatus.BAD_REQUEST: "invalid_json",
            HTTPStatus.UNSUPPORTED_MEDIA_TYPE: "unsupported_media_type",
            HTTPStatus.UNPROCESSABLE_CONTENT: "validation_error",
            HTTPStatus.NOT_FOUND: "not_found",
            HTTPStatus.UNAUTHORIZED: "unauthorized",
            HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
        }.get(error.status_code, "internal_error")
        return error_body(code), error.status_code, error.headers

    @app.errorhandler(PaymentError)
    def domain_error(error: PaymentError) -> tuple[dict[str, dict[str, str]], int]:
        status = {
            "unauthorized": HTTPStatus.UNAUTHORIZED,
            "not_found": HTTPStatus.NOT_FOUND,
        }.get(error.code, HTTPStatus.CONFLICT)
        return error_body(error.code), status

    @app.errorhandler(TotalUnavailable)
    def total_error(error: TotalUnavailable) -> tuple[dict[str, dict[str, str]], int]:
        return error_body("total_unavailable"), HTTPStatus.SERVICE_UNAVAILABLE

    @app.errorhandler(Exception)
    def unexpected_error(error: Exception) -> tuple[dict[str, dict[str, str]], int]:
        # Only validated identifiers and exception type names may enter logs.
        cart_id = None
        if request.view_args:
            try:
                cart_id = str(UUID(str(request.view_args.get("cart_id", ""))))
            except ValueError:
                pass
        app.logger.error(
            "Payment request failed: type=%s cart_id=%s", type(error).__name__, cart_id
        )
        if isinstance(error, (ProgrammingError, IntegrityError, DataError)):
            return error_body("internal_error"), HTTPStatus.INTERNAL_SERVER_ERROR
        if isinstance(error, (DBAPIError, TimeoutError, DisconnectionError)):
            return error_body("service_unavailable"), HTTPStatus.SERVICE_UNAVAILABLE
        return error_body("internal_error"), HTTPStatus.INTERNAL_SERVER_ERROR
