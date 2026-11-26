from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities import DraftRunKind, EntityType, PublicationStatus
from app.infrastructure.database.models import EntityDraftRunRow
from pipeline.wikidata.datas import apply, plan, report
from tests.factories import add_entity
from tests.pipeline.test_importer import fetch


def test_fills_only_empty_dates_of_a_published_entity_and_records_the_origin(db_session: Session) -> None:
    augusto = add_entity(db_session, "augusto", entity_type=EntityType.PERSON)
    augusto.wikidata_id = "Q1405"
    augusto.summary = "Texto do revisor."
    augusto.start_year = -63
    db_session.flush()

    changed = apply(db_session, plan(db_session, fetch))

    assert changed == 1
    assert (augusto.status, augusto.summary) == (PublicationStatus.PUBLISHED, "Texto do revisor.")
    assert (augusto.start_year, augusto.end_year) == (-63, 14)
    origin = db_session.scalar(select(EntityDraftRunRow).where(EntityDraftRunRow.entity_id == augusto.id))
    assert origin is not None
    assert origin.kind == DraftRunKind.WIKIDATA
    assert (origin.details["fields_filled"], origin.details["scope"]) == (["end_year"], "datas")


def test_planning_writes_nothing_and_reports_in_historical_years(db_session: Session) -> None:
    alesia = add_entity(db_session, "batalha-de-alesia", entity_type=EntityType.EVENT)
    alesia.wikidata_id = "Q131386"
    eneida = add_entity(db_session, "eneida", entity_type=EntityType.WORK)
    eneida.wikidata_id = "Q60220"
    db_session.flush()

    text = report(plan(db_session, fetch))

    assert (alesia.start_year, alesia.end_year) == (None, None)
    assert text.splitlines() == [
        "preenche batalha-de-alesia [published]: 52 a.C. → 52 a.C. (novo: start_year, end_year)",
        "mantém  eneida [published]: — → —",
        "1 entidade(s) a completar de 2 com Wikidata.",
    ]


def test_limits_to_chosen_slugs_and_reports_items_that_fail(db_session: Session) -> None:
    for slug, qid in (("augusto", "Q1405"), ("sumido", "Q999999999")):
        add_entity(db_session, slug, entity_type=EntityType.PERSON).wikidata_id = qid
    db_session.flush()

    proposals = plan(db_session, fetch, ["sumido"])

    assert [(p.entity.slug, p.problem) for p in proposals] == [("sumido", "Q999999999: não existe no Wikidata")]
    assert apply(db_session, proposals) == 0
