import os

from alembic import context
from sqlalchemy import create_engine, pool

from payment_service.models import Base

target_metadata = Base.metadata

database_url = os.environ.get("DATABASE_URL", "")
if not database_url:
    raise RuntimeError("Set DATABASE_URL to a postgresql+psycopg:// connection URL.")


def run_migrations_offline() -> None:
    """Generate SQL without connecting to the database."""
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply migrations using a database connection."""
    engine = create_engine(database_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
