"""Gera e grava o rascunho de texto de uma entidade (plan.md §3.8).

Rede e modelo ficam fora da transação, que pode levar minutos; a gravação é uma transação curta que confere de
novo o estado da entidade antes de escrever.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.domain.entities import DraftRunKind, PublicationStatus
from app.infrastructure.database.models import (
    EntityDraftRunRow,
    EntityRow,
    EntitySectionRow,
    EntitySourceRow,
    SourceRow,
)
from pipeline.texto.draft import TextDraft, parse_draft
from pipeline.texto.gemini import DraftRequest, ModelReply, TextDrafter
from pipeline.texto.sources import Citation, GatheredSources, gather_sources


class TextRefusedError(Exception):
    """A entidade não pode receber texto gerado sem decisão do revisor; a mensagem diz qual."""


@dataclass(frozen=True)
class _Target:
    name: str
    entity_type: str
    citations: list[Citation]


def generate_text(
    slug: str,
    *,
    session_factory: Callable[[], Session],
    drafter: TextDrafter,
    fetch_text: Callable[[str], str],
    replace: bool = False,
) -> str:
    with session_factory() as session:
        target = _target(session, slug, replace=replace)
    gathered = gather_sources(target.citations, fetch_text)
    reply = drafter.draft(DraftRequest(target.name, target.entity_type, gathered.texts))
    draft = parse_draft(reply.payload, {source.label for source in gathered.texts})
    with session_factory() as session, session.begin():
        entity = _entity(session, slug, replace=replace)
        _write(session, entity, draft, reply, gathered, model=drafter.model)
    return f"{len(draft.sections)} seção(ões), {len(draft.points_to_check)} ponto(s) a conferir"


def _target(session: Session, slug: str, *, replace: bool) -> _Target:
    entity = _entity(session, slug, replace=replace)
    rows = session.execute(
        select(SourceRow, EntitySourceRow.excerpt)
        .join(EntitySourceRow, EntitySourceRow.source_id == SourceRow.id)
        .where(EntitySourceRow.entity_id == entity.id)
        .order_by(EntitySourceRow.created_at)
    )
    citations = [
        Citation(source.id, source.title, source.text_url, source.text_license, excerpt) for source, excerpt in rows
    ]
    return _Target(entity.name, entity.entity_type, citations)


def _entity(session: Session, slug: str, *, replace: bool) -> EntityRow:
    entity = session.scalar(select(EntityRow).where(EntityRow.slug == slug))
    if entity is None:
        raise TextRefusedError("entidade não encontrada")
    if entity.status != PublicationStatus.DRAFT:
        raise TextRefusedError("não está em rascunho; o pipeline não altera conteúdo revisado")
    has_sections = session.scalar(select(EntitySectionRow.id).where(EntitySectionRow.entity_id == entity.id).limit(1))
    if not replace and (has_sections is not None or (entity.description or "").strip()):
        raise TextRefusedError("já tem introdução ou seções; use --substituir para gerar de novo")
    return entity


def _write(
    session: Session, entity: EntityRow, draft: TextDraft, reply: ModelReply, gathered: GatheredSources, *, model: str
) -> None:
    previous_summary = entity.summary
    session.execute(delete(EntitySectionRow).where(EntitySectionRow.entity_id == entity.id))
    entity.summary = draft.summary
    entity.description = draft.description
    for position, section in enumerate(draft.sections, start=1):
        session.add(
            EntitySectionRow(
                entity_id=entity.id,
                section_key=section.key,
                title=section.title,
                position=position,
                content=section.document,
            )
        )
    source_ids = {source.label: str(source.source_id) for source in gathered.texts}
    details: dict[str, Any] = {
        "previous_summary": previous_summary,
        "usage": reply.usage,
        "sources": [
            {
                "label": source.label,
                "source_id": str(source.source_id),
                "title": source.title,
                "origin": source.origin,
                "url": source.url,
                "sha256": source.sha256,
                "chars": len(source.text),
            }
            for source in gathered.texts
        ],
        "skipped_sources": list(gathered.skipped),
        "sections": {section.key: [source_ids[label] for label in section.source_labels] for section in draft.sections},
    }
    session.add(
        EntityDraftRunRow(
            entity_id=entity.id,
            kind=DraftRunKind.TEXT,
            model=reply.model_version or model,
            points_to_check="\n".join(draft.points_to_check) or None,
            details=details,
        )
    )
    session.flush()
