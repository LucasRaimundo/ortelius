from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.domain.entities import WIKIDATA_SOURCE_ID, DraftRunKind, EntityType, PublicationStatus
from app.infrastructure.database.models import (
    EntityAliasRow,
    EntityDraftRunRow,
    EntityRelationRow,
    EntityRow,
    EntitySourceRow,
    RelationTypeRow,
)
from pipeline.wikidata import importer
from pipeline.wikidata.__main__ import main
from pipeline.wikidata.client import WikidataError
from pipeline.wikidata.importer import Outcome, import_items
from tests.factories import add_entity
from tests.pipeline.test_mapping import load

FIXTURES = ("Q1405", "Q60220", "Q131386")


def fetch(qid: str) -> Mapping[str, Any]:
    if qid not in FIXTURES:
        raise WikidataError(f"{qid}: não existe no Wikidata")
    return load(qid)


@pytest.fixture
def session_factory(db_session: Session) -> Callable[[], Session]:
    """Cada item roda num savepoint da conexão do teste, como roda numa transação própria em produção."""
    connection = db_session.connection()
    return lambda: Session(bind=connection, join_transaction_mode="create_savepoint")


@pytest.fixture
def run(session_factory: Callable[[], Session]) -> Callable[..., list[importer.ImportResult]]:
    def run(*qids: str, **options: Any) -> list[importer.ImportResult]:
        return import_items(qids, fetch=fetch, session_factory=session_factory, **options)

    return run


def existing(session: Session, slug: str, wikidata_id: str) -> EntityRow:
    entity = add_entity(session, slug)
    entity.wikidata_id = wikidata_id
    session.flush()
    return entity


def by_qid(session: Session, qid: str) -> EntityRow:
    entity = session.scalar(select(EntityRow).where(EntityRow.wikidata_id == qid))
    assert entity is not None
    return entity


def relations_of(session: Session, entity: EntityRow) -> list[tuple[str, str]]:
    rows = session.execute(
        select(EntityRelationRow.relation_type, EntityRow.slug)
        .join(EntityRow, EntityRow.id == EntityRelationRow.target_entity_id)
        .where(EntityRelationRow.source_entity_id == entity.id)
        .order_by(EntityRelationRow.relation_type)
    )
    return [(key, slug) for key, slug in rows]


def count(session: Session, model: type[Any], entity: EntityRow) -> int:
    column = model.source_entity_id if model is EntityRelationRow else model.entity_id
    return session.scalar(select(func.count()).select_from(model).where(column == entity.id)) or 0


def test_creates_a_cited_draft_linked_only_to_entities_already_in_the_atlas(db_session: Session, run: Any) -> None:
    existing(db_session, "roma", "Q220")

    [result] = run("Q1405")

    augusto = by_qid(db_session, "Q1405")
    assert result.outcome == Outcome.CREATED
    assert (augusto.slug, augusto.name, augusto.entity_type, augusto.status) == (
        "augusto",
        "Augusto",
        EntityType.PERSON,
        PublicationStatus.DRAFT,
    )
    assert relations_of(db_session, augusto) == [("nasceu-em", "roma")]
    citation = db_session.scalar(select(EntitySourceRow).where(EntitySourceRow.entity_id == augusto.id))
    assert citation is not None
    assert (citation.source_id, citation.locator) == (WIKIDATA_SOURCE_ID, "https://www.wikidata.org/wiki/Q1405")
    assert {alias.alias_type for alias in db_session.scalars(select(EntityAliasRow))} == {"wikidata"}
    origin = db_session.scalar(select(EntityDraftRunRow).where(EntityDraftRunRow.entity_id == augusto.id))
    assert origin is not None
    assert (origin.kind, origin.model, origin.details["revision"]) == (DraftRunKind.WIKIDATA, None, 2568559129)
    assert origin.details["relations_without_target"] == ["P20 Q72288", "P26 Q237618", "P26 Q233444", "P26 Q469701"]


def test_stores_location_and_creates_missing_relation_type_with_its_labels(db_session: Session, run: Any) -> None:
    existing(db_session, "guerras-galicas", "Q202161")

    run("Q131386")

    alesia = by_qid(db_session, "Q131386")
    point = db_session.execute(
        select(func.ST_X(EntityRow.location), func.ST_Y(EntityRow.location)).where(EntityRow.id == alesia.id)
    ).one()
    assert tuple(point) == pytest.approx((4.50027778, 47.53722222))
    # O revisor vê no painel as coordenadas do ponto importado (trigger da migration 0008).
    coordinates = db_session.execute(
        select(EntityRow.latitude, EntityRow.longitude).where(EntityRow.id == alesia.id)
    ).one()
    assert tuple(coordinates) == (Decimal("47.537222"), Decimal("4.500278"))
    assert relations_of(db_session, alesia) == [("parte-de", "guerras-galicas")]
    relation_type = db_session.get(RelationTypeRow, "parte-de")
    assert relation_type is not None
    assert (relation_type.label, relation_type.inverse_label) == ("faz parte de", "inclui")


def test_running_again_on_a_draft_keeps_reviewer_edits_and_does_not_duplicate(db_session: Session, run: Any) -> None:
    existing(db_session, "guerras-galicas", "Q202161")
    run("Q131386")
    alesia = by_qid(db_session, "Q131386")
    alesia.summary = "Texto do revisor."
    alesia.location = None
    db_session.flush()

    [result] = run("Q131386")

    db_session.refresh(alesia)
    assert result.outcome == Outcome.COMPLETED
    assert alesia.summary == "Texto do revisor."
    assert alesia.location is not None
    assert [count(db_session, model, alesia) for model in (EntityAliasRow, EntityRelationRow, EntitySourceRow)] == [
        8,
        1,
        1,
    ]
    assert count(db_session, EntityDraftRunRow, alesia) == 2


def test_new_draft_gets_dates_and_a_draft_only_gets_its_empty_dates(db_session: Session, run: Any) -> None:
    run("Q1405")
    augusto = by_qid(db_session, "Q1405")
    assert (augusto.start_year, augusto.end_year) == (-62, 14)
    augusto.start_year = -63  # o revisor corrigiu o nascimento e apagou a morte
    augusto.end_year = None
    db_session.flush()

    run("Q1405")

    db_session.refresh(augusto)
    assert (augusto.start_year, augusto.end_year) == (-63, 14)
    # Os dois registros nascem na mesma transação do teste, com o mesmo horário: a ordem não é confiável.
    runs = db_session.scalars(select(EntityDraftRunRow).where(EntityDraftRunRow.entity_id == augusto.id))
    assert ["end_year"] in [run.details["fields_filled"] for run in runs]


def test_never_fills_a_year_that_contradicts_the_reviewer(db_session: Session, run: Any) -> None:
    run("Q1405")
    augusto = by_qid(db_session, "Q1405")
    augusto.start_year = 20
    augusto.end_year = None
    db_session.flush()

    run("Q1405")

    db_session.refresh(augusto)
    assert (augusto.start_year, augusto.end_year) == (20, None)


def test_never_touches_an_entity_that_left_draft(db_session: Session, run: Any) -> None:
    eneida = existing(db_session, "eneida", "Q60220")

    [result] = run("Q60220")

    assert result.outcome == Outcome.SKIPPED
    assert "não está em rascunho" in result.message
    assert [count(db_session, model, eneida) for model in (EntityAliasRow, EntityDraftRunRow)] == [0, 0]


def test_refuses_slug_used_by_another_entity_and_accepts_a_chosen_one(db_session: Session, run: Any) -> None:
    add_entity(db_session, "eneida")

    [refused] = run("Q60220")
    [created] = run("Q60220", slug="eneida-de-virgilio")

    assert refused.outcome == Outcome.FAILED
    assert "o endereço 'eneida' já é de outra entidade; use --slug" in refused.message
    assert (created.outcome, by_qid(db_session, "Q60220").slug) == (Outcome.CREATED, "eneida-de-virgilio")


def test_a_failing_item_writes_nothing_and_does_not_stop_the_others(
    db_session: Session, run: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_on_alesia(session: Session, entity: EntityRow, qid: str) -> None:
        if qid == "Q131386":
            raise SQLAlchemyError("conexão perdida")

    monkeypatch.setattr(importer, "_cite_wikidata", fail_on_alesia)

    results = run("Q131386", "Q999", "Q60220")

    assert [result.outcome for result in results] == [Outcome.FAILED, Outcome.FAILED, Outcome.CREATED]
    assert "Q999: não existe no Wikidata" in results[1].message
    assert db_session.scalar(select(EntityRow).where(EntityRow.wikidata_id == "Q131386")) is None
    assert db_session.scalar(select(func.count()).select_from(EntityAliasRow)) == 7


@pytest.mark.parametrize("argv", [["q1405"], ["Q0"], ["Q1405", "Q60220", "--slug", "x"], ["Q1", "--tipo", "pais"]])
def test_command_line_rejects_invalid_arguments_before_touching_anything(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(argv)
    assert exit_info.value.code == 2
