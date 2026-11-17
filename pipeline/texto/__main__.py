"""Gera o rascunho de texto a partir das fontes citadas: `uv run python -m pipeline.texto augusto`."""

import argparse
import sys

import httpx2
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker

from app.infrastructure.database.engine import create_db_engine
from app.settings import Settings, get_settings
from pipeline.texto import gemini, openrouter, sources
from pipeline.texto.draft import InvalidDraftError
from pipeline.texto.model import DraftingError, QuotaExhaustedError, TextDrafter, create_http_client
from pipeline.texto.sources import SourceFetcher, SourceTextError
from pipeline.texto.writer import TextRefusedError, generate_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.texto", description=__doc__)
    parser.add_argument("slugs", nargs="+", metavar="SLUG", help="endereço da entidade em rascunho")
    parser.add_argument("--substituir", action="store_true", help="apaga introdução e seções existentes e gera de novo")
    parser.add_argument("--provedor", choices=["gemini", "openrouter"], help="sobrepõe ORTELIUS_DRAFT_PROVIDER")
    args = parser.parse_args(argv)

    settings = get_settings()
    provider = args.provedor or settings.draft_provider
    secret = settings.gemini_api_key if provider == "gemini" else settings.openrouter_api_key
    api_key = secret.get_secret_value() if secret else ""
    if not api_key:
        parser.error(f"ORTELIUS_{provider.upper()}_API_KEY não está no .env")

    engine = create_db_engine(settings.database_url.get_secret_value())
    failed = 0
    with sources.create_http_client() as source_http, create_http_client() as model_http:
        drafter = _drafter(provider, settings, model_http, api_key)
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


def _drafter(provider: str, settings: Settings, http: httpx2.Client, api_key: str) -> TextDrafter:
    def notify(message: str) -> None:
        print(f"...        {message}", flush=True)

    if provider == "openrouter":
        model = settings.draft_model or openrouter.DEFAULT_MODEL
        return openrouter.OpenRouterDrafter(http, api_key=api_key, model=model, notify=notify)
    return gemini.GeminiDrafter(
        http, api_key=api_key, model=settings.draft_model or gemini.DEFAULT_MODEL, notify=notify
    )


if __name__ == "__main__":
    sys.exit(main())
