from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker


def create_engine_and_sessions(
    database_url: str,
) -> tuple[Engine, sessionmaker[Session]]:
    engine = create_engine(database_url, hide_parameters=True)
    return engine, sessionmaker(engine, expire_on_commit=False)
