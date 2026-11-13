import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.domain import content
from app.domain.entities import EntityType, SourceType
from app.infrastructure.database.engine import get_session
from app.infrastructure.database.models import SLUG_PATTERN, EntitySectionRow
from app.infrastructure.editorjs import to_blocks
from app.repositories.entities import EntityRecord, RelationRecord, find_published_entity
from app.schemas.entities import (
    Alias,
    ContentBlock,
    EntityDetail,
    HeadingBlock,
    ListBlock,
    ListItem,
    ParagraphBlock,
    PointLocation,
    QuoteBlock,
    RelatedEntity,
    Relation,
    Section,
    Source,
    TextRun,
)
from app.schemas.errors import ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/entities", tags=["entities"])


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
        blocks = to_blocks(row.content)
    except content.InvalidContentError as error:
        logger.warning(
            "seção descartada por conteúdo inválido",
            extra={"entity_slug": entity_slug, "section_key": row.section_key, "reason": str(error)},
        )
        return None
    return Section(
        section_key=row.section_key,
        title=row.title,
        position=row.position,
        content=[_to_content_block(block) for block in blocks],
    )


def _to_content_block(block: content.Block) -> ContentBlock:
    match block:
        case content.Heading(level=level, text=text):
            return HeadingBlock(type="heading", level=level, text=_to_runs(text))
        case content.Paragraph(text=text):
            return ParagraphBlock(type="paragraph", text=_to_runs(text))
        case content.BulletList(style=style, items=items):
            return ListBlock(type="list", style=style, items=[_to_list_item(item) for item in items])
        case content.Quote(text=text, caption=caption):
            return QuoteBlock(type="quote", text=_to_runs(text), caption=caption)


def _to_list_item(item: content.ListItem) -> ListItem:
    return ListItem(text=_to_runs(item.text), items=[_to_list_item(child) for child in item.items])


def _to_runs(text: content.RichText) -> list[TextRun]:
    return [TextRun(text=run.text, bold=run.bold, italic=run.italic, href=run.href) for run in text]


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
