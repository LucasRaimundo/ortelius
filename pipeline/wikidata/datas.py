"""Completa as datas de início e fim vazias com o Wikidata, inclusive em entidades já publicadas.

O importador nunca mexe em entidade fora de rascunho. Este comando é a exceção estreita: só preenche
`start_year` e `end_year` que estão vazios, sem tocar em nenhum outro campo. Como numa entidade publicada a data
vai direto para o site, a revisão humana é a tabela: por padrão o comando só mostra o que faria.

Uso: uv run python -m pipeline.wikidata.datas [slug ...] [--aplicar]
"""

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.chronology import from_astronomical_year
from app.domain.entities import DraftRunKind, EntityType
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.models import EntityDraftRunRow, EntityRow
from app.settings import get_settings
from pipeline.wikidata.client import WikidataClient, WikidataError, create_http_client
from pipeline.wikidata.importer import empty_years_to_fill
from pipeline.wikidata.mapping import ItemFacts, UnmappableItemError, facts_from_response


@dataclass(frozen=True)
class Proposal:
    entity: EntityRow
    facts: ItemFacts | None
    years: Mapping[str, int]
    problem: str | None = None


def plan(session: Session, fetch: Callable[[str], Mapping[str, Any]], slugs: Sequence[str] = ()) -> list[Proposal]:
    query = select(EntityRow).where(EntityRow.wikidata_id.is_not(None)).order_by(EntityRow.slug)
    if slugs:
        query = query.where(EntityRow.slug.in_(slugs))
    proposals: list[Proposal] = []
    for entity in session.scalars(query):
        assert entity.wikidata_id is not None
        try:
            facts = facts_from_response(fetch(entity.wikidata_id), forced_type=EntityType(entity.entity_type))
        except (WikidataError, UnmappableItemError) as error:
            proposals.append(Proposal(entity, None, {}, str(error)))
            continue
        proposals.append(Proposal(entity, facts, empty_years_to_fill(entity, facts)))
    return proposals


def apply(session: Session, proposals: Sequence[Proposal]) -> int:
    """Grava as datas propostas e registra a origem em cada entidade alterada; devolve quantas mudaram."""
    changed = 0
    for proposal in proposals:
        if proposal.facts is None or not proposal.years:
            continue
        for field, year in proposal.years.items():
            setattr(proposal.entity, field, year)
        session.add(
            EntityDraftRunRow(
                entity_id=proposal.entity.id,
                kind=DraftRunKind.WIKIDATA,
                points_to_check="\n".join(proposal.facts.points_to_check) or None,
                details={
                    "wikidata_id": proposal.facts.qid,
                    "revision": proposal.facts.revision,
                    "fields_filled": list(proposal.years),
                    "scope": "datas",
                },
            )
        )
        changed += 1
    session.flush()
    return changed


def _year(year: int | None) -> str:
    if year is None:
        return "—"
    number, era = from_astronomical_year(year)
    return f"{number} {era}"


def report(proposals: Sequence[Proposal]) -> str:
    lines: list[str] = []
    for proposal in proposals:
        entity = proposal.entity
        if proposal.problem:
            lines.append(f"falha   {entity.slug}: {proposal.problem}")
            continue
        assert proposal.facts is not None
        start = proposal.years.get("start_year", entity.start_year)
        end = proposal.years.get("end_year", entity.end_year)
        action = "preenche" if proposal.years else "mantém "
        filled = f" (novo: {', '.join(proposal.years)})" if proposal.years else ""
        lines.append(f"{action} {entity.slug} [{entity.status}]: {_year(start)} → {_year(end)}{filled}")
        lines.extend(f"         ponto a conferir: {point}" for point in proposal.facts.points_to_check)
    changed = sum(bool(proposal.years) for proposal in proposals)
    lines.append(f"{changed} entidade(s) a completar de {len(proposals)} com Wikidata.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.wikidata.datas", description=__doc__)
    parser.add_argument("slugs", nargs="*", metavar="SLUG", help="só estas entidades; sem nenhuma, todas")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isto, só mostra o que faria")
    args = parser.parse_args(argv)

    engine = create_db_engine(get_settings().database_url.get_secret_value())
    try:
        with create_http_client() as http, Session(engine) as session:
            proposals = plan(session, WikidataClient(http).fetch_item, args.slugs)
            print(report(proposals))
            if not args.aplicar:
                print("Nada foi gravado. Rode com --aplicar para gravar.")
            else:
                changed = apply(session, proposals)
                session.commit()
                print(f"Datas gravadas em {changed} entidade(s).")
    finally:
        engine.dispose()
    return 1 if any(proposal.problem for proposal in proposals) else 0


if __name__ == "__main__":
    sys.exit(main())
