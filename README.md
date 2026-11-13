# Ortelius

Backend do Atlas: API, domínio, PostGIS e pipeline geográfico. Visão, arquitetura e especificações ficam no
repositório `oAtlas` (`CORE.md`, `ARCH.md`, `docs/projects/`).

## Rodar

```bash
cp .env.example .env
docker compose up -d --wait   # PostGIS local
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
```

## Verificar

Os testes usam o PostGIS do `compose.yaml`. O CI (`.github/workflows/ci.yml`) roda a mesma verificação em todo
PR para a `main`.

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

## Lançar versão

Fluxo e convenção de versões em `oAtlas/ARCH.md` §17. Na `main` atualizada, com o PostGIS rodando:

```bash
scripts/release.sh 0.2.0 --dry-run   # checagens, verificação e prévia das notas, sem criar nada
scripts/release.sh 0.2.0             # cria a tag v0.2.0 e envia ao GitHub
```

O workflow `release` cria a release no GitHub com as notas agrupadas por tipo de commit (`0.x` sai como
pre-release). Para regerar a release de uma tag existente, rode o workflow manualmente informando a tag.
