# Ortelius

Backend do Atlas: API, domínio, PostGIS e pipeline geográfico. Visão, arquitetura e especificações ficam no
repositório `oAtlas` (`CORE.md`, `ARCH.md`, `docs/projects/`).

## Rodar

```bash
cp .env.example .env
uv sync
uv run uvicorn app.main:create_app --factory --reload
```

## Verificar

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```
