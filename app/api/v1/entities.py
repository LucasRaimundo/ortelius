import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.orm import Session

from app.domain.entities import EntityType, SourceType
from app.infrastructure.database.engine import get_session
from app.infrastructure.database.models import SLUG_PATTERN, EntitySectionRow
from app.repositories.entities import EntityRecord, RelationRecord, find_published_entity
from app.schemas.entities import (
    Alias,
    ContentBlock,
    EntityDetail,
    PointLocation,
    RelatedEntity,
    Relation,
    Section,
    Source,
)
from app.schemas.errors import ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/entities", tags=["entities"])

_content_blocks = TypeAdapter(list[ContentBlock])


@router.get(
    "/{slug}",
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def get_entity(
    slug: Annotated[str, Path(max_length=180, pattern=SLUG_PATTERN)],
    session: Annotated[Session, Depends(get_session)],
) -> EntityDetail:
    record = find_published_entity(session, slug)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="entidade não encontrada")
    return _to_detail(record)


def _to_detail(record: EntityRecord) -> EntityDetail:
    entity = record.entity
    return EntityDetail(
        id=str(entity.id),
        slug=entity.slug,
        name=entity.name,
        entity_type=EntityType(entity.entity_type),
        summary=entity.summary,
        description=entity.description,
        cover_media=entity.cover_media,
        location=PointLocation(coordinates=record.location) if record.location else None,
        aliases=[
            Alias(
                alias=alias.alias,
                language_code=alias.language_code,
                alias_type=alias.alias_type,
                start_year=alias.start_year,
                end_year=alias.end_year,
            )
            for alias in record.aliases
        ],
        sections=[section for row in record.sections if (section := _valid_section(entity.slug, row))],
        relations=[_to_relation(relation) for relation in record.relations],
        sources=[
            Source(
                id=str(source.id),
                title=source.title,
                source_type=SourceType(source.source_type),
                author_or_institution=source.author_or_institution,
                publication_year=source.publication_year,
                publisher_or_journal=source.publisher_or_journal,
                identifier=source.identifier,
                url=source.url,
                contribution_type=citation.contribution_type,
                locator=citation.locator,
            )
            for citation, source in record.sources
        ],
    )


def _valid_section(entity_slug: str, row: EntitySectionRow) -> Section | None:
    """Uma seção com blocos inválidos é omitida para que o restante da página continue legível."""
    try:
        content = _content_blocks.validate_python(row.content)
    except ValidationError as error:
        logger.warning(
            "seção descartada por conteúdo inválido",
            extra={"entity_slug": entity_slug, "section_key": row.section_key, "errors": error.errors()},
        )
        return None
    return Section(section_key=row.section_key, title=row.title, position=row.position, content=content)


def _to_relation(record: RelationRecord) -> Relation:
    relation, relation_type, other = record.relation, record.relation_type, record.other
    label = relation_type.inverse_label if record.direction == "incoming" else relation_type.label
    return Relation(
        direction=record.direction,
        relation_type=relation_type.key,
        label=label,
        description=relation.description,
        start_year=relation.start_year,
        end_year=relation.end_year,
        entity=RelatedEntity(
            slug=other.slug,
            name=other.name,
            entity_type=EntityType(other.entity_type),
            summary=other.summary,
        ),
    )
