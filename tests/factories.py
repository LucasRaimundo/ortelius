from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities import EntityType, ImageLicense, PublicationStatus, SourceType
from app.infrastructure.database.models import (
    EntityRelationRow,
    EntityRow,
    EntitySectionRow,
    EntitySourceRow,
    MediaRow,
    RelationTypeRow,
    SourceRow,
)


def add_source(session: Session, title: str = "Fonte de teste") -> SourceRow:
    source = SourceRow(title=title, source_type=SourceType.BOOK)
    session.add(source)
    session.flush()
    return source


def add_media(session: Session, **overrides: object) -> MediaRow:
    values: dict[str, object] = {
        "file_id": "6f1c2d3e-0000-4000-8000-000000000001",
        "alt_text": "Mosaico com um poeta sentado entre duas musas",
        "caption": "Mosaico de Virgílio entre Clio e Melpômene",
        "author": "David Bjorgen",
        "license": ImageLicense.PUBLIC_DOMAIN,
        "source_url": "https://commons.wikimedia.org/wiki/File:Virgil_Mosaic_Bardo_Museum_Tunis.jpg",
        **overrides,
    }
    media = MediaRow(**values)
    session.add(media)
    session.flush()
    return media


def add_entity(
    session: Session,
    slug: str,
    *,
    status: PublicationStatus = PublicationStatus.PUBLISHED,
    entity_type: EntityType = EntityType.PLACE,
    with_source: bool = True,
) -> EntityRow:
    entity = EntityRow(slug=slug, name=slug.replace("-", " ").title(), entity_type=entity_type, status=status)
    session.add(entity)
    session.flush()
    if with_source:
        cite(session, entity.id, add_source(session).id)
    return entity


def cite(session: Session, entity_id: UUID, source_id: UUID) -> EntitySourceRow:
    citation = EntitySourceRow(entity_id=entity_id, source_id=source_id, contribution_type="fundamenta")
    session.add(citation)
    session.flush()
    return citation


def check_deferred_constraints(session: Session) -> None:
    """Os testes nunca fazem commit; isto força os triggers diferidos a rodarem agora."""
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


def add_section(session: Session, entity_id: UUID, key: str, position: int, content: object) -> EntitySectionRow:
    section = EntitySectionRow(
        entity_id=entity_id, section_key=key, title=key.title(), position=position, content=content
    )
    session.add(section)
    session.flush()
    return section


def relate(
    session: Session,
    source: EntityRow,
    target: EntityRow,
    *,
    label: str,
    inverse_label: str | None = None,
    is_directional: bool = True,
) -> EntityRelationRow:
    key = label.replace(" ", "-")
    if session.get(RelationTypeRow, key) is None:
        session.add(RelationTypeRow(key=key, label=label, inverse_label=inverse_label))
        session.flush()
    relation = EntityRelationRow(
        source_entity_id=source.id, target_entity_id=target.id, relation_type=key, is_directional=is_directional
    )
    session.add(relation)
    session.flush()
    return relation
