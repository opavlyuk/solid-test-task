"""Read demo identities from X-User-ID when local impersonation is enabled."""

from uuid import UUID

from flask import current_app, request

from payment_service.services.contracts import PaymentError


def demo_user_id() -> UUID:
    """Read the demo user's UUID from the current request's X-User-ID header.

    Requires a request context. Does not verify that the user exists.

    Raises:
        PaymentError: With code "unauthorized" if demo authentication is
            disabled or the header is missing or malformed.
    """
    if not current_app.config["ALLOW_DEMO_AUTH"]:
        raise PaymentError("unauthorized")
    try:
        return UUID(request.headers.get("X-User-ID", ""))
    except ValueError as error:
        raise PaymentError("unauthorized") from error
