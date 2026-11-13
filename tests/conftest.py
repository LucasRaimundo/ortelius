from collections.abc import Iterator
from typing import cast

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Connection, Engine, make_url, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.infrastructure.database.engine import create_db_engine, get_session
from app.main import create_app
from app.settings import Settings, get_settings

TEST_DATABASE_NAME = "ortelius_test"


def _run_alembic(connection: Connection, *, downgrade: bool = False) -> None:
    config = Config("alembic.ini")
    config.attributes["connection"] = connection
    config.attributes["configure_logger"] = False
    if downgrade:
        command.downgrade(config, "base")
    else:
        command.upgrade(config, "head")


def _recreate_test_database() -> str:
    admin_url = make_url(get_settings().database_url.get_secret_value())
    admin_engine = create_db_engine(admin_url.render_as_string(hide_password=False))
    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text(f"DROP DATABASE IF EXISTS {TEST_DATABASE_NAME} WITH (FORCE)"))
            connection.execute(text(f"CREATE DATABASE {TEST_DATABASE_NAME}"))
    except OperationalError as error:
        pytest.exit(f"PostGIS indisponível. Rode `docker compose up -d --wait`.\n{error}", returncode=1)
    finally:
        admin_engine.dispose()
    return admin_url.set(database=TEST_DATABASE_NAME).render_as_string(hide_password=False)


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    engine = create_db_engine(_recreate_test_database())
    with engine.begin() as connection:
        _run_alembic(connection)
        _run_alembic(connection, downgrade=True)
        _run_alembic(connection)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    with db_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


def _test_settings(database_url: str) -> Settings:
    return Settings(cors_allowed_origins=["http://localhost:3000"], database_url=SecretStr(database_url))


class ApiClient:
    """Starlette >= 1.10 tipa o TestClient pelo pacote httpx2; com httpx instalado, a resposta em runtime é
    um httpx.Response, mas o pyright a vê como desconhecida. Este wrapper concentra o cast num só lugar."""

    def __init__(self, test_client: TestClient) -> None:
        self._test_client = test_client

    def get(self, path: str) -> httpx.Response:
        return cast(httpx.Response, self._test_client.get(path))  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def client(db_session: Session) -> Iterator[ApiClient]:
    app = create_app(_test_settings(db_session.get_bind().engine.url.render_as_string(hide_password=False)))
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield ApiClient(test_client)


@pytest.fixture
def client_without_database() -> Iterator[ApiClient]:
    app = create_app(_test_settings("postgresql+psycopg://ortelius:ortelius@127.0.0.1:1/ortelius"))
    with TestClient(app) as test_client:
        yield ApiClient(test_client)
