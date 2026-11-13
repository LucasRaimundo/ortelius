from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities import EntityType, PublicationStatus, SourceType
from app.infrastructure.database.models import EntityRow, EntitySourceRow, SourceRow


def add_source(session: Session, title: str = "Fonte de teste") -> SourceRow:
    source = SourceRow(title=title, source_type=SourceType.BOOK)
    session.add(source)
    session.flush()
    return source


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
