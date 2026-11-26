"""Grava itens do Wikidata como rascunhos, uma transação por item.

Regras (plan.md §3.8): só rascunhos; nunca altera entidade que saiu de rascunho; numa entidade em rascunho só
completa campos vazios, para não desfazer o trabalho do revisor; relações só com entidades já existentes.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from geoalchemy2 import WKTElement
from sqlalchemy import select
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
from pipeline.wikidata.client import WikidataError
from pipeline.wikidata.mapping import ItemFacts, RelationFact, UnmappableItemError, facts_from_response, slugify

WIKIDATA_ALIAS_TYPE = "wikidata"


class Outcome(StrEnum):
    CREATED = "criado"
    COMPLETED = "completado"
    SKIPPED = "ignorado"
    FAILED = "falhou"


@dataclass(frozen=True)
class ImportResult:
    qid: str
    outcome: Outcome
    message: str


class ImportRefusedError(Exception):
    """O item não pode ser gravado sem decisão do revisor; a mensagem diz qual."""


def import_items(
    qids: Iterable[str],
    *,
    fetch: Callable[[str], Mapping[str, Any]],
    session_factory: Callable[[], Session],
    forced_type: EntityType | None = None,
    slug: str | None = None,
) -> list[ImportResult]:
    results: list[ImportResult] = []
    for qid in qids:
        try:
            facts = facts_from_response(fetch(qid), forced_type=forced_type)
            with session_factory() as session, session.begin():
                results.append(import_facts(session, facts, slug=slug))
        except (WikidataError, UnmappableItemError, ImportRefusedError) as error:
            results.append(ImportResult(qid, Outcome.FAILED, str(error)))
        except SQLAlchemyError as error:
            results.append(ImportResult(qid, Outcome.FAILED, f"{qid}: erro no banco, nada gravado ({error!r})"))
    return results


def import_facts(session: Session, facts: ItemFacts, *, slug: str | None = None) -> ImportResult:
    entity = session.scalar(select(EntityRow).where(EntityRow.wikidata_id == facts.qid))
    if entity is not None and entity.status != PublicationStatus.DRAFT:
        message = f"{facts.qid}: {entity.slug} não está em rascunho; o pipeline não altera conteúdo revisado"
        return ImportResult(facts.qid, Outcome.SKIPPED, message)

    if entity is None:
        entity = _create_entity(session, facts, slug or slugify(facts.name))
        outcome = Outcome.CREATED
        optional = ("summary", "location", "start_year", "end_year")
        filled = ["name", "entity_type"] + [field for field in optional if getattr(facts, field) is not None]
    else:
        outcome, filled = Outcome.COMPLETED, _fill_empty_fields(entity, facts)

    aliases_added = _add_missing_aliases(session, entity, facts)
    relations_added, without_target = _add_missing_relations(session, entity, facts.relations)
    _cite_wikidata(session, entity, facts.qid)
    session.add(
        EntityDraftRunRow(
            entity_id=entity.id,
            kind=DraftRunKind.WIKIDATA,
            points_to_check="\n".join(facts.points_to_check) or None,
            details={
                "wikidata_id": facts.qid,
                "revision": facts.revision,
                "fields_filled": filled,
                "aliases_added": aliases_added,
                "relations_added": relations_added,
                "relations_without_target": without_target,
            },
        )
    )
    session.flush()
    summary = f"{len(aliases_added)} nome(s) alternativo(s), {len(relations_added)} relação(ões)"
    return ImportResult(facts.qid, outcome, f"{facts.qid}: {entity.slug} ({summary})")


def _create_entity(session: Session, facts: ItemFacts, slug: str) -> EntityRow:
    if not slug:
        raise ImportRefusedError(f"{facts.qid}: o nome {facts.name!r} não gera endereço; informe com --slug")
    owner = session.scalar(select(EntityRow).where(EntityRow.slug == slug))
    if owner is not None:
        other = f" (Wikidata {owner.wikidata_id})" if owner.wikidata_id else ""
        raise ImportRefusedError(f"{facts.qid}: o endereço {slug!r} já é de outra entidade{other}; use --slug")
    entity = EntityRow(
        slug=slug,
        name=facts.name,
        entity_type=facts.entity_type,
        status=PublicationStatus.DRAFT,
        summary=facts.summary,
        location=_point(facts),
        start_year=facts.start_year,
        end_year=facts.end_year,
        wikidata_id=facts.qid,
    )
    session.add(entity)
    session.flush()
    return entity


def _fill_empty_fields(entity: EntityRow, facts: ItemFacts) -> list[str]:
    filled: list[str] = []
    if entity.summary is None and facts.summary is not None:
        entity.summary = facts.summary
        filled.append("summary")
    if entity.location is None and facts.location is not None:
        entity.location = _point(facts)
        filled.append("location")
    filled += fill_empty_years(entity, facts)
    return filled


def fill_empty_years(entity: EntityRow, facts: ItemFacts) -> list[str]:
    """Preenche início e fim vazios; não grava um ano que contradiga o outro já definido pelo revisor."""
    start = entity.start_year if entity.start_year is not None else facts.start_year
    end = entity.end_year if entity.end_year is not None else facts.end_year
    if start is not None and end is not None and start > end:
        return []
    filled: list[str] = []
    if entity.start_year is None and start is not None:
        entity.start_year = start
        filled.append("start_year")
    if entity.end_year is None and end is not None:
        entity.end_year = end
        filled.append("end_year")
    return filled


def _point(facts: ItemFacts) -> WKTElement | None:
    if facts.location is None:
        return None
    longitude, latitude = facts.location
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


def _add_missing_aliases(session: Session, entity: EntityRow, facts: ItemFacts) -> list[str]:
    existing = session.scalars(select(EntityAliasRow.alias).where(EntityAliasRow.entity_id == entity.id))
    known = {alias.casefold() for alias in existing} | {entity.name.casefold()}
    added: list[str] = []
    for alias in facts.aliases:
        if alias.name.casefold() in known:
            continue
        known.add(alias.name.casefold())
        session.add(
            EntityAliasRow(
                entity_id=entity.id,
                alias=alias.name,
                language_code=alias.language_code,
                alias_type=WIKIDATA_ALIAS_TYPE,
            )
        )
        added.append(alias.name)
    return added


def _add_missing_relations(
    session: Session, entity: EntityRow, relations: Iterable[RelationFact]
) -> tuple[list[str], list[str]]:
    added: list[str] = []
    without_target: list[str] = []
    for relation in relations:
        target_id = session.scalar(select(EntityRow.id).where(EntityRow.wikidata_id == relation.target_qid))
        if target_id is None:
            without_target.append(f"{relation.property_id} {relation.target_qid}")
            continue
        if target_id == entity.id:
            continue
        relation_type = relation.relation_type
        if session.get(RelationTypeRow, relation_type.key) is None:
            session.add(
                RelationTypeRow(
                    key=relation_type.key, label=relation_type.label, inverse_label=relation_type.inverse_label
                )
            )
            session.flush()
        exists = session.scalar(
            select(EntityRelationRow.id).where(
                EntityRelationRow.source_entity_id == entity.id,
                EntityRelationRow.target_entity_id == target_id,
                EntityRelationRow.relation_type == relation_type.key,
            )
        )
        if exists is None:
            session.add(
                EntityRelationRow(
                    source_entity_id=entity.id,
                    target_entity_id=target_id,
                    relation_type=relation_type.key,
                    is_directional=relation_type.inverse_label is not None,
                )
            )
            added.append(f"{relation_type.key} {relation.target_qid}")
    return added, without_target


def _cite_wikidata(session: Session, entity: EntityRow, qid: str) -> None:
    cited = session.scalar(
        select(EntitySourceRow.id).where(
            EntitySourceRow.entity_id == entity.id, EntitySourceRow.source_id == WIKIDATA_SOURCE_ID
        )
    )
    if cited is None:
        session.add(
            EntitySourceRow(
                entity_id=entity.id,
                source_id=WIKIDATA_SOURCE_ID,
                contribution_type="fundamenta",
                locator=f"https://www.wikidata.org/wiki/{qid}",
            )
        )
