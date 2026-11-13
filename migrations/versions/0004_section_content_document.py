"""Conteúdo das seções passa a ser o documento do editor de blocos ({"blocks": [...]}), não uma lista.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Só seções vazias são convertidas: blocos no formato antigo não têm tradução automática para o novo, e a
    # migration falha em vez de descartá-los.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM entity_sections WHERE content <> '[]'::jsonb) THEN
                RAISE EXCEPTION 'há seções com conteúdo no formato antigo (lista); converta-as antes de migrar';
            END IF;
        END
        $$;

        ALTER TABLE entity_sections DROP CONSTRAINT ck_entity_sections_content_is_array;
        UPDATE entity_sections SET content = '{"blocks": []}'::jsonb;
        ALTER TABLE entity_sections ALTER COLUMN content SET DEFAULT '{"blocks": []}'::jsonb;
        ALTER TABLE entity_sections ADD CONSTRAINT ck_entity_sections_content_is_document
            CHECK (jsonb_typeof(content) = 'object' AND COALESCE(jsonb_typeof(content -> 'blocks'), '') = 'array');
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM entity_sections WHERE jsonb_array_length(content -> 'blocks') > 0) THEN
                RAISE EXCEPTION 'há seções com blocos; o formato antigo não os representa';
            END IF;
        END
        $$;

        ALTER TABLE entity_sections DROP CONSTRAINT ck_entity_sections_content_is_document;
        UPDATE entity_sections SET content = '[]'::jsonb;
        ALTER TABLE entity_sections ALTER COLUMN content SET DEFAULT '[]'::jsonb;
        ALTER TABLE entity_sections ADD CONSTRAINT ck_entity_sections_content_is_array
            CHECK (jsonb_typeof(content) = 'array');
    """)
