from dataclasses import dataclass
from typing import Literal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.domain.entities import PublicationStatus
from app.infrastructure.database.models import (
    EntityAliasRow,
    EntityRelationRow,
    EntityRow,
    EntitySectionRow,
    EntitySourceRow,
    RelationTypeRow,
    SourceRow,
)


@dataclass(frozen=True)
class RelationRecord:
    direction: Literal["outgoing", "incoming", "mutual"]
    relation: EntityRelationRow
    relation_type: RelationTypeRow
    other: EntityRow


@dataclass(frozen=True)
class EntityRecord:
    entity: EntityRow
    location: tuple[float, float] | None
    aliases: list[EntityAliasRow]
    sections: list[EntitySectionRow]
    relations: list[RelationRecord]
    sources: list[tuple[EntitySourceRow, SourceRow]]


def find_published_entity(session: Session, slug: str) -> EntityRecord | None:
    row = session.execute(
        select(EntityRow, func.ST_X(EntityRow.location), func.ST_Y(EntityRow.location)).where(
            EntityRow.slug == slug, EntityRow.status == PublicationStatus.PUBLISHED
        )
    ).one_or_none()
    if row is None:
        return None
    entity, longitude, latitude = row
    location = (longitude, latitude) if longitude is not None and latitude is not None else None

    aliases = session.scalars(
        select(EntityAliasRow).where(EntityAliasRow.entity_id == entity.id).order_by(EntityAliasRow.alias)
    ).all()
    sections = session.scalars(
        select(EntitySectionRow).where(EntitySectionRow.entity_id == entity.id).order_by(EntitySectionRow.position)
    ).all()
    sources = session.execute(
        select(EntitySourceRow, SourceRow)
        .join(SourceRow, SourceRow.id == EntitySourceRow.source_id)
        .where(EntitySourceRow.entity_id == entity.id)
        .order_by(SourceRow.title)
    ).all()

    return EntityRecord(
        entity=entity,
        location=location,
        aliases=list(aliases),
        sections=list(sections),
        relations=_published_relations(session, entity),
        sources=list(sources),
    )


def _published_relations(session: Session, entity: EntityRow) -> list[RelationRecord]:
    other = aliased(EntityRow)
    is_source = EntityRelationRow.source_entity_id == entity.id
    rows = session.execute(
        select(EntityRelationRow, RelationTypeRow, other)
        .join(RelationTypeRow, RelationTypeRow.key == EntityRelationRow.relation_type)
        .join(
            other,
            or_(
                and_(is_source, other.id == EntityRelationRow.target_entity_id),
                and_(EntityRelationRow.target_entity_id == entity.id, other.id == EntityRelationRow.source_entity_id),
            ),
        )
        .where(other.status == PublicationStatus.PUBLISHED)
        .order_by(EntityRelationRow.position.asc().nulls_last(), other.name)
    ).all()
    return [
        RelationRecord(
            direction=_direction(relation, entity),
            relation=relation,
            relation_type=relation_type,
            other=related,
        )
        for relation, relation_type, related in rows
    ]


def _direction(relation: EntityRelationRow, entity: EntityRow) -> Literal["outgoing", "incoming", "mutual"]:
    if not relation.is_directional:
        return "mutual"
    return "outgoing" if relation.source_entity_id == entity.id else "incoming"
