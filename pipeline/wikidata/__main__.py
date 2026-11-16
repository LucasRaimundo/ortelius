"""Importa itens do Wikidata como rascunhos: `uv run python -m pipeline.wikidata Q1405 Q131386`."""

import argparse
import re
import sys

from sqlalchemy.orm import sessionmaker

from app.domain.entities import EntityType
from app.infrastructure.database.engine import create_db_engine
from app.settings import get_settings
from pipeline.wikidata.client import WikidataClient, create_http_client
from pipeline.wikidata.importer import Outcome, import_items


def _qid(value: str) -> str:
    if not re.fullmatch(r"Q[1-9][0-9]*", value):
        raise argparse.ArgumentTypeError(f"{value!r} não é um código do Wikidata (ex.: Q1405)")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.wikidata", description=__doc__)
    parser.add_argument("qids", nargs="+", type=_qid, metavar="QID")
    parser.add_argument("--tipo", choices=[t.value for t in EntityType], help="força o tipo quando o P31 não basta")
    parser.add_argument("--slug", help="endereço da entidade; só com um item")
    args = parser.parse_args(argv)
    if args.slug and len(args.qids) > 1:
        parser.error("--slug só pode ser usado com um item")

    engine = create_db_engine(get_settings().database_url.get_secret_value())
    with create_http_client() as http:
        results = import_items(
            args.qids,
            fetch=WikidataClient(http).fetch_item,
            session_factory=sessionmaker(engine),
            forced_type=EntityType(args.tipo) if args.tipo else None,
            slug=args.slug,
        )
    engine.dispose()

    for result in results:
        print(f"{result.outcome:<10} {result.message}")
    failed = sum(result.outcome == Outcome.FAILED for result in results)
    print(f"{len(results) - failed} de {len(results)} item(ns) processado(s); revise os rascunhos no painel.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
