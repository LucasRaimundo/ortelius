from collections.abc import Iterator

from fastapi import Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

CONNECT_TIMEOUT_SECONDS = 3
STATEMENT_TIMEOUT_MS = 5_000


def create_db_engine(database_url: str) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "options": f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
        },
    )


def get_session(request: Request) -> Iterator[Session]:
    session_factory: sessionmaker[Session] = request.app.state.session_factory
    with session_factory() as session:
        yield session
