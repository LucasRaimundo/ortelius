"""Isola o painel editorial: schema `directus` e papel `atlas_editor` sem permissão de alterar estrutura.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Tabelas que o painel editorial pode ler e escrever. Concessão explícita, tabela a tabela: uma tabela de
# domínio nova só fica editável por decisão consciente, e `alembic_version` nunca fica exposta.
EDITOR_TABLES = (
    "entities",
    "entity_aliases",
    "entity_sections",
    "relation_types",
    "entity_relations",
    "sources",
    "entity_sources",
)


def upgrade() -> None:
    tables = ", ".join(EDITOR_TABLES)
    op.execute(f"""
        -- Papéis pertencem ao servidor, não ao banco: outro banco do mesmo servidor pode já tê-lo criado.
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'atlas_editor') THEN
                CREATE ROLE atlas_editor NOLOGIN;
            END IF;
        END
        $$;

        -- Padrão desde o PostgreSQL 15, mas bancos gerenciados podem vir configurados de outro jeito.
        REVOKE CREATE ON SCHEMA public FROM PUBLIC;

        CREATE SCHEMA directus;
        GRANT USAGE, CREATE ON SCHEMA directus TO atlas_editor;

        GRANT USAGE ON SCHEMA public TO atlas_editor;
        GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO atlas_editor;
    """)


def downgrade() -> None:
    tables = ", ".join(EDITOR_TABLES)
    # Sem CASCADE: se o Directus já criou tabelas no schema, o downgrade falha em vez de apagar o CMS.
    # O papel não é removido porque pode ter permissões em outros bancos do mesmo servidor. O CREATE em
    # public não é devolvido a PUBLIC: seria afrouxar a segurança em relação ao padrão do PostgreSQL.
    op.execute(f"""
        REVOKE ALL ON {tables} FROM atlas_editor;
        REVOKE USAGE ON SCHEMA public FROM atlas_editor;
        DROP SCHEMA directus;
    """)
