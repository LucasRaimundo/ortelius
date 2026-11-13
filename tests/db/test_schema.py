import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.entities import PublicationStatus
from app.infrastructure.database.models import Base, is_domain_object
from tests.factories import add_entity, add_section, add_source, check_deferred_constraints, cite


def test_migrations_match_models(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"include_name": is_domain_object})
        assert compare_metadata(context, Base.metadata) == []


def test_rejects_publishing_entity_without_source(db_session: Session) -> None:
    add_entity(db_session, "roma", with_source=False)

    with pytest.raises(IntegrityError, match="Para publicar, cadastre ao menos uma fonte"):
        check_deferred_constraints(db_session)


def test_allows_publishing_entity_with_source_added_in_same_transaction(db_session: Session) -> None:
    entity = add_entity(db_session, "roma", with_source=False)
    cite(db_session, entity.id, add_source(db_session).id)

    check_deferred_constraints(db_session)


def test_rejects_removing_last_source_of_published_entity(db_session: Session) -> None:
    entity = add_entity(db_session, "roma", with_source=False)
    citation = cite(db_session, entity.id, add_source(db_session).id)
    check_deferred_constraints(db_session)

    with pytest.raises(IntegrityError, match="Para publicar, cadastre ao menos uma fonte"):
        db_session.delete(citation)
        db_session.flush()


def test_allows_draft_without_source(db_session: Session) -> None:
    add_entity(db_session, "roma", status=PublicationStatus.DRAFT, with_source=False)

    check_deferred_constraints(db_session)


@pytest.mark.parametrize("slug", ["Roma", "imperio romano", "imperio--romano", "-roma", "império-romano"])
def test_rejects_malformed_slug(db_session: Session, slug: str) -> None:
    with pytest.raises(IntegrityError, match="ck_entities_slug_format"):
        add_entity(db_session, slug, with_source=False)


@pytest.mark.parametrize("content", [[], {"time": 1}, {"blocks": {}}])
def test_rejects_section_content_that_is_not_a_block_document(db_session: Session, content: object) -> None:
    entity = add_entity(db_session, "roma")

    with pytest.raises(IntegrityError, match="ck_entity_sections_content_is_document"):
        add_section(db_session, entity.id, "visao_geral", 1, content)


def test_accepts_block_editor_document(db_session: Session) -> None:
    entity = add_entity(db_session, "roma")
    block: dict[str, object] = {"id": "a1", "type": "paragraph", "data": {}}
    document: dict[str, object] = {"time": 1794596350781, "version": "2.31.2", "blocks": [block]}

    assert add_section(db_session, entity.id, "visao_geral", 1, document).content == document
