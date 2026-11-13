from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.models import Base, is_domain_object
from app.settings import get_settings

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, include_name=is_domain_object)
    with context.begin_transaction():
        context.run_migrations()


# Os testes injetam a conexão do banco de teste em config.attributes["connection"].
injected_connection: Connection | None = config.attributes.get("connection")
if injected_connection is not None:
    run_migrations(injected_connection)
else:
    engine = create_db_engine(get_settings().database_url.get_secret_value())
    with engine.connect() as connection:
        run_migrations(connection)
    engine.dispose()
