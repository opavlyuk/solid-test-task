"""Load and validate application settings from the environment and overrides."""

import os
from collections.abc import Mapping

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def load_config(overrides: Mapping[str, object] | None) -> dict[str, object]:
    """Load settings with overrides taking precedence over environment values.

    Demo authentication defaults to disabled. ALLOW_DEMO_AUTH accepts booleans
    or case-insensitive "true" and "false" strings and is returned as a boolean.
    DATABASE_URL must select the postgresql+psycopg driver. Validation does not
    open database connections.

    Raises:
        ValueError: A required setting is missing or a setting is invalid.
    """
    settings: dict[str, object] = {
        "DATABASE_URL": os.environ.get("DATABASE_URL"),
        "ALLOW_DEMO_AUTH": os.environ.get("ALLOW_DEMO_AUTH", "false"),
    }
    if overrides is not None:
        settings.update(overrides)
    url = settings["DATABASE_URL"]
    if not isinstance(url, str) or not url:
        raise ValueError("DATABASE_URL is required")
    try:
        parsed_url = make_url(url)
    except (ArgumentError, ValueError) as error:
        raise ValueError("DATABASE_URL is invalid") from error
    if parsed_url.drivername != "postgresql+psycopg":
        raise ValueError("DATABASE_URL must use postgresql+psycopg")
    enabled = settings["ALLOW_DEMO_AUTH"]
    if isinstance(enabled, str) and enabled.lower() in {"true", "false"}:
        enabled = enabled.lower() == "true"
    if not isinstance(enabled, bool):
        raise ValueError("ALLOW_DEMO_AUTH must be true or false")
    settings["ALLOW_DEMO_AUTH"] = enabled
    return settings
