import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.entities import WIKIDATA_SOURCE_ID, DraftRunKind, PublicationStatus, SourceType, TextLicense
from app.infrastructure.database.models import Base, EntityDraftRunRow, SourceRow, is_domain_object
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
        add_section(db_session, entity.id, "visao-geral", 1, content)


def test_accepts_block_editor_document(db_session: Session) -> None:
    entity = add_entity(db_session, "roma")
    block: dict[str, object] = {"id": "a1", "type": "paragraph", "data": {}}
    document: dict[str, object] = {"time": 1794596350781, "version": "2.31.2", "blocks": [block]}

    assert add_section(db_session, entity.id, "visao-geral", 1, document).content == document


def test_citation_without_contribution_type_defaults_to_supporting_the_content(db_session: Session) -> None:
    """O painel cria a citação só com entidade e fonte quando o revisor escolhe uma fonte existente."""
    entity = add_entity(db_session, "roma", with_source=False)
    source = add_source(db_session)

    db_session.execute(
        text("INSERT INTO entity_sources (entity_id, source_id) VALUES (:entity, :source)"),
        {"entity": entity.id, "source": source.id},
    )

    assert db_session.scalar(text("SELECT contribution_type FROM entity_sources")) == "fundamenta"
    check_deferred_constraints(db_session)


@pytest.mark.parametrize("wikidata_id", ["q220", "220", "Q0", "Q22a", " Q220"])
def test_rejects_malformed_wikidata_id(db_session: Session, wikidata_id: str) -> None:
    entity = add_entity(db_session, "roma")

    with pytest.raises(IntegrityError, match="ck_entities_wikidata_id_format"):
        entity.wikidata_id = wikidata_id
        db_session.flush()


def test_rejects_two_entities_with_the_same_wikidata_id(db_session: Session) -> None:
    add_entity(db_session, "roma").wikidata_id = "Q220"
    db_session.flush()

    with pytest.raises(IntegrityError, match="uq_entities_wikidata_id"):
        add_entity(db_session, "roma-antiga").wikidata_id = "Q220"
        db_session.flush()


@pytest.mark.parametrize(
    ("text_url", "text_license", "constraint"),
    [
        ("https://www.gutenberg.org/ebooks/1", None, "ck_sources_text_url_requires_license"),
        ("ftp://exemplo.org/texto.txt", TextLicense.PUBLIC_DOMAIN, "ck_sources_text_url_format"),
        ("https://www.gutenberg.org/ebooks/1", "todos-os-direitos", "ck_sources_text_license"),
    ],
)
def test_rejects_source_text_without_a_valid_open_license(
    db_session: Session, text_url: str, text_license: str | None, constraint: str
) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        db_session.add(SourceRow(title="T", source_type=SourceType.BOOK, text_url=text_url, text_license=text_license))
        db_session.flush()


@pytest.mark.parametrize(("kind", "model"), [(DraftRunKind.TEXT, None), (DraftRunKind.WIKIDATA, "gemini-flash")])
def test_only_text_runs_record_the_model(db_session: Session, kind: DraftRunKind, model: str | None) -> None:
    entity = add_entity(db_session, "roma")

    with pytest.raises(IntegrityError, match="ck_entity_draft_runs_model_only_for_text"):
        db_session.add(EntityDraftRunRow(entity_id=entity.id, kind=kind, model=model))
        db_session.flush()


def test_wikidata_source_exists_with_the_id_the_pipeline_cites(db_session: Session) -> None:
    source = db_session.get(SourceRow, WIKIDATA_SOURCE_ID)

    assert source is not None
    assert (source.title, source.source_type, source.license) == ("Wikidata", SourceType.DATASET, "CC0 1.0")
