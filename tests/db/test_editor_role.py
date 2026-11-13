import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.infrastructure.database.models import Base, EntityRow
from tests.factories import add_entity

# Tabelas de domínio que o painel editorial não pode editar. Vazio hoje: toda tabela de domínio nova
# precisa ou ser concedida a atlas_editor numa migration, ou entrar aqui por decisão explícita.
NOT_EDITABLE_TABLES: frozenset[str] = frozenset()


def act_as_editor(session: Session) -> None:
    session.execute(text("SET LOCAL ROLE atlas_editor"))


def test_every_domain_table_has_an_explicit_editor_access_decision(db_session: Session) -> None:
    missing = [
        table
        for table in Base.metadata.tables
        if table not in NOT_EDITABLE_TABLES
        and not db_session.scalar(
            text("SELECT has_table_privilege('atlas_editor', :table, 'SELECT, INSERT, UPDATE, DELETE')"),
            {"table": f"public.{table}"},
        )
    ]
    assert missing == []


def test_editor_can_write_content(db_session: Session) -> None:
    act_as_editor(db_session)
    entity = add_entity(db_session, "roma")

    entity.name = "Roma Antiga"
    db_session.flush()

    assert db_session.get(EntityRow, entity.id) is not None


def test_editor_can_create_tables_in_directus_schema(db_session: Session) -> None:
    act_as_editor(db_session)

    db_session.execute(text("CREATE TABLE directus.directus_spike_check (id int)"))


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.painel_criou (id int)",
        "ALTER TABLE entities ADD COLUMN painel_criou text",
        "ALTER TABLE entities ADD CONSTRAINT painel_fk FOREIGN KEY (cover_media) REFERENCES sources (title)",
        "DROP TABLE entity_aliases",
        "TRUNCATE entities",
    ],
)
def test_editor_cannot_change_domain_structure(db_session: Session, statement: str) -> None:
    act_as_editor(db_session)

    with pytest.raises(ProgrammingError, match=r"permission denied|must be owner"):
        db_session.execute(text(statement))


def test_editor_cannot_read_migration_state(db_session: Session) -> None:
    act_as_editor(db_session)

    with pytest.raises(ProgrammingError, match="permission denied"):
        db_session.execute(text("SELECT * FROM alembic_version"))
