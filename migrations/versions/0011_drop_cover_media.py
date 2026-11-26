"""Remove `entities.cover_media`, substituída por `cover_id` → `media` (migration 0009).

As capas com crédito reconhecível já foram migradas para `media`. As demais deixam de ter capa; o arquivo antigo
continua no Directus, sem uso.

O painel guarda metadados da coluna (o campo e a relação de arquivo). Sem a coluna, eles ficariam órfãos e o
painel falharia ao abrir a entidade; por isso saem aqui também, quando o Directus já criou suas tabelas. É a única
escrita no schema do Directus: o domínio continua sem referenciar tabelas do CMS.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN cover_media;")
    op.execute("""
        DO $$
        BEGIN
            IF to_regclass('directus.directus_fields') IS NOT NULL THEN
                DELETE FROM directus.directus_fields WHERE collection = 'entities' AND field = 'cover_media';
            END IF;
            IF to_regclass('directus.directus_relations') IS NOT NULL THEN
                DELETE FROM directus.directus_relations
                WHERE many_collection = 'entities' AND many_field = 'cover_media';
            END IF;
        END $$;
    """)


def downgrade() -> None:
    # Volta vazia: o conteúdo antigo só existe no backup. Os metadados do painel não voltam (o config.json da
    # versão anterior os recria com cms.apply_config).
    op.execute("ALTER TABLE entities ADD COLUMN cover_media TEXT;")
