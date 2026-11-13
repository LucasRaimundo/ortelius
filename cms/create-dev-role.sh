#!/usr/bin/env bash
# Cria (ou atualiza a senha de) o usuário de login `directus` no banco local, como membro de atlas_editor.
# Em produção esse usuário é provisionado pela infraestrutura.
# Uso: cms/create-dev-role.sh   (com o PostGIS do compose rodando e as migrations aplicadas)
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

[[ -f cms/.env ]] || { echo "erro: cms/.env não existe; copie de cms/.env.example" >&2; exit 1; }
password="$(grep -E '^DB_PASSWORD=' cms/.env | cut -d= -f2-)"
[[ -n "$password" ]] || { echo "erro: DB_PASSWORD vazio em cms/.env" >&2; exit 1; }

docker compose exec -T db psql -U ortelius -d ortelius -v ON_ERROR_STOP=1 -v password="$password" <<'SQL'
SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_editor') AS missing_group \gset
\if :missing_group
    \echo 'erro: papel atlas_editor não existe; rode `uv run alembic upgrade head` antes'
    \quit 1
\endif

SELECT NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'directus') AS missing_user \gset
\if :missing_user
    CREATE ROLE directus LOGIN PASSWORD :'password';
\else
    ALTER ROLE directus PASSWORD :'password';
\endif
GRANT atlas_editor TO directus;
SQL

echo "usuário directus pronto (membro de atlas_editor)"
