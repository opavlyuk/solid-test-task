import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from payment_service.db import create_engine_and_sessions


@pytest.fixture(scope="session")
def database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.fail("Set TEST_DATABASE_URL to a disposable PostgreSQL *_test database.")
    url = make_url(value)
    if url.get_backend_name() != "postgresql" or not (url.database or "").endswith(
        "_test"
    ):
        pytest.fail("Refusing database reset: TEST_DATABASE_URL must name a *_test DB.")
    return value


@pytest.fixture(scope="session")
def alembic_config(database_url: str) -> Iterator[Config]:
    with pytest.MonkeyPatch.context() as environment:
        environment.setenv("DATABASE_URL", database_url)
        yield Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))


@pytest.fixture(scope="session")
def migrated_engine(database_url: str, alembic_config: Config) -> Iterator[Engine]:
    command.upgrade(alembic_config, "head")
    engine = create_engine(database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def engine(migrated_engine: Engine) -> Iterator[Engine]:
    def truncate_application_tables() -> None:
        tables = set(inspect(migrated_engine).get_table_names()) & {
            "users",
            "products",
            "carts",
            "cart_items",
            "user_payment_methods",
            "payments",
        }
        with migrated_engine.begin() as connection:
            connection.execute(text(f"TRUNCATE {', '.join(sorted(tables))} CASCADE"))

    truncate_application_tables()
    try:
        yield migrated_engine
    finally:
        truncate_application_tables()


@pytest.fixture
def session_factory(
    database_url: str, engine: Engine
) -> Iterator[sessionmaker[Session]]:
    """Depend on engine for schema setup and table cleanup."""
    orm_engine, factory = create_engine_and_sessions(database_url)
    try:
        yield factory
    finally:
        orm_engine.dispose()


@pytest.fixture
def shop(engine: Engine) -> dict[str, UUID]:
    ids = {name: uuid4() for name in ("user_id", "cart_id", "method_id")}
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, email, name) VALUES (:user_id, 'a@test', 'A')"
            ),
            ids,
        )
        connection.execute(
            text("INSERT INTO carts (id, user_id) VALUES (:cart_id, :user_id)"), ids
        )
        connection.execute(
            text(
                "INSERT INTO user_payment_methods (id, user_id, provider_token) "
                "VALUES (:method_id, :user_id, 'tok_test')"
            ),
            ids,
        )
    return ids
