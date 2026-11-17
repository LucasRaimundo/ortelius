"""Gera o rascunho de texto a partir das fontes citadas: `uv run python -m pipeline.texto augusto`."""

import argparse
import sys

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.infrastructure.database.engine import create_db_engine
from app.settings import get_settings
from pipeline.texto import gemini, sources
from pipeline.texto.draft import InvalidDraftError
from pipeline.texto.gemini import DraftingError, GeminiDrafter, QuotaExhaustedError
from pipeline.texto.sources import SourceFetcher, SourceTextError
from pipeline.texto.writer import TextRefusedError, generate_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.texto", description=__doc__)
    parser.add_argument("slugs", nargs="+", metavar="SLUG", help="endereço da entidade em rascunho")
    parser.add_argument("--substituir", action="store_true", help="apaga introdução e seções existentes e gera de novo")
    args = parser.parse_args(argv)

    settings = get_settings()
    api_key = settings.gemini_api_key.get_secret_value() if settings.gemini_api_key else ""
    if not api_key:
        parser.error("ORTELIUS_GEMINI_API_KEY não está no .env")

    engine = create_db_engine(settings.database_url.get_secret_value())
    failed = 0
    with sources.create_http_client() as source_http, gemini.create_http_client() as model_http:
        drafter = GeminiDrafter(
            model_http,
            api_key=api_key,
            model=settings.draft_model,
            notify=lambda message: print(f"...        {message}", flush=True),
        )
        for index, slug in enumerate(args.slugs):
            try:
                message = generate_text(
                    slug,
                    session_factory=sessionmaker(engine),
                    drafter=drafter,
                    fetch_text=SourceFetcher(source_http).fetch_text,
                    replace=args.substituir,
                    progress=lambda step, slug=slug: print(f"...        {slug}: {step}", flush=True),
                )
                print(f"gerado     {slug}: {message}")
            except QuotaExhaustedError as error:
                remaining = args.slugs[index + 1 :]
                failed += 1 + len(remaining)
                print(f"falhou     {slug}: {error}")
                if remaining:
                    print(f"parado     sem tentar, para não gastar pedidos: {', '.join(remaining)}")
                break
            except (TextRefusedError, SourceTextError, DraftingError, InvalidDraftError) as error:
                failed += 1
                print(f"falhou     {slug}: {error}")
            except SQLAlchemyError as error:
                failed += 1
                print(f"falhou     {slug}: erro no banco, nada gravado ({error!r})")
    engine.dispose()
    done = len(args.slugs) - failed
    print(
        f"{done} de {len(args.slugs)} entidade(s) com texto gerado; confira cada afirmação contra as fontes no painel."
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
