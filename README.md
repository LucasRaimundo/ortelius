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

## Painel editorial (Directus)

O Directus guarda as próprias tabelas no schema `directus` e acessa as tabelas de domínio pelo papel
`atlas_editor`, que só lê e escreve dados: ele não consegue criar, alterar nem apagar estrutura
(migration `0002`). Para subir localmente, com o PostGIS rodando e as migrations aplicadas:

```bash
cp cms/.env.example cms/.env          # troque SECRET por um valor aleatório
cms/create-dev-role.sh                # cria o usuário de login `directus`, membro de atlas_editor
docker compose --profile cms up -d    # painel em http://localhost:8055
```

Configuração do painel (rótulos, campos, relações) vive em `cms/config.json` e é aplicada pela API do
Directus, alterando só o que difere:

```bash
uv run python -m cms.apply_config --check   # mostra o que mudaria
uv run python -m cms.apply_config           # aplica
```

Toda coluna de domínio precisa estar configurada em `cms/config.json` (ou na lista `hidden_fields`); um
teste falha caso contrário. Fora do ambiente local, o applier usa `DIRECTUS_URL`, `DIRECTUS_TOKEN` e
`DIRECTUS_DB_URL`.

Em produção, o usuário `directus` é provisionado pela infraestrutura, com a senha no gerenciador de
segredos. O usuário que roda as migrations precisa da permissão `CREATEROLE`, porque a `0002` cria o
papel `atlas_editor`.

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
