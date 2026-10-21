"""Create the payment API with configurable totals and payment providers."""

from collections.abc import Mapping

from apiflask import APIFlask

from payment_service.api.errors import register_errors
from payment_service.api.payments import register_payments
from payment_service.api.schemas import ErrorOutput
from payment_service.config import load_config
from payment_service.db import create_engine_and_sessions
from payment_service.integrations.mock_gateway import MockPaymentGateway
from payment_service.integrations.mock_totals import DemoTotalProvider
from payment_service.services.contracts import PaymentGateway, TotalProvider
from payment_service.services.payments import PaymentService


def create_app(
    config: Mapping[str, object] | None = None,
    *,
    totals: TotalProvider | None = None,
    gateway: PaymentGateway | None = None,
) -> APIFlask:
    """Create a configured payment API.

    Args:
        config: Settings that override environment values.
        totals: Cart total provider. None selects the demo implementation.
        gateway: Payment provider. None selects the demo implementation.

    Raises:
        ValueError: Database or demo authentication settings are invalid.
    """
    app = APIFlask(__name__, title="Cart payments", version="1.0")
    app.config.update(load_config(config))
    app.config.update(
        HTTP_ERROR_SCHEMA=ErrorOutput, VALIDATION_ERROR_SCHEMA=ErrorOutput
    )
    engine, sessions = create_engine_and_sessions(str(app.config["DATABASE_URL"]))
    service = PaymentService(
        sessions,
        DemoTotalProvider() if totals is None else totals,
        MockPaymentGateway() if gateway is None else gateway,
    )
    app.extensions["engine"] = engine
    app.extensions["payment_service"] = service
    register_errors(app)
    register_payments(app, service)
    return app
