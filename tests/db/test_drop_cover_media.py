from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.orm import Session


def _migrate(session: Session, revision: str, *, down: bool = False) -> None:
    config = Config("alembic.ini")
    config.attributes["connection"] = session.connection()
    config.attributes["configure_logger"] = False
    (command.downgrade if down else command.upgrade)(config, revision)


def test_dropping_cover_media_also_removes_its_panel_metadata(db_session: Session) -> None:
    # No banco de testes o Directus nunca rodou: só as colunas que a migration lê. Tudo some com o rollback.
    db_session.execute(
        text("""
            CREATE TABLE directus.directus_fields (id SERIAL PRIMARY KEY, collection TEXT, field TEXT);
            CREATE TABLE directus.directus_relations (id SERIAL PRIMARY KEY, many_collection TEXT, many_field TEXT);
            INSERT INTO directus.directus_fields (collection, field)
                VALUES ('entities', 'cover_media'), ('entities', 'cover_id'), ('media', 'cover_media');
            INSERT INTO directus.directus_relations (many_collection, many_field)
                VALUES ('entities', 'cover_media'), ('media', 'file_id');
        """)
    )
    _migrate(db_session, "0010", down=True)

    _migrate(db_session, "0011")

    fields = db_session.execute(text("SELECT collection, field FROM directus.directus_fields ORDER BY id")).all()
    relations = db_session.execute(
        text("SELECT many_collection, many_field FROM directus.directus_relations ORDER BY id")
    ).all()
    assert [tuple(row) for row in fields] == [("entities", "cover_id"), ("media", "cover_media")]
    assert [tuple(row) for row in relations] == [("media", "file_id")]
    column = db_session.scalar(
        text("SELECT 1 FROM information_schema.columns WHERE table_name = 'entities' AND column_name = 'cover_media'")
    )
    assert column is None
