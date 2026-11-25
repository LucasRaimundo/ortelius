"""Imagens com procedência obrigatória (`media`) e capa da entidade apontando para elas.

No aceite da Fase 2b o painel aceitou capas de banco de imagens com direitos reservados, e metade das capas
publicadas não tem origem conhecida. Autor, licença e fonte passam a ser colunas obrigatórias numa tabela de
domínio: uma capa sem crédito não consegue existir, venha do painel, do pipeline ou de SQL.

`cover_media` continua até as capas atuais serem migradas para `media` (scripts/migrar_capas.py); uma migration
posterior a remove.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE media (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            file_id TEXT NOT NULL,
            alt_text VARCHAR(300) NOT NULL,
            caption VARCHAR(300) NOT NULL,
            author VARCHAR(200) NOT NULL,
            license VARCHAR(24) NOT NULL,
            license_version VARCHAR(8),
            source_url TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_media PRIMARY KEY (id),
            CONSTRAINT ck_media_license CHECK (license IN ('public_domain', 'cc0', 'cc_by', 'cc_by_sa')),
            CONSTRAINT ck_media_source_url_format CHECK (source_url ~ '^https?://'),
            CONSTRAINT ck_media_texts_not_blank CHECK (
                btrim(alt_text) <> '' AND btrim(caption) <> '' AND btrim(author) <> '' AND btrim(file_id) <> ''
            )
        );
        CREATE TRIGGER trg_media_updated_at BEFORE UPDATE ON media
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        ALTER TABLE entities
            ADD COLUMN cover_id UUID,
            ADD CONSTRAINT fk_entities_cover_id FOREIGN KEY (cover_id) REFERENCES media (id) ON DELETE SET NULL;
        CREATE INDEX ix_entities_cover_id ON entities (cover_id);

        GRANT SELECT, INSERT, UPDATE, DELETE ON media TO atlas_editor;
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE entities DROP COLUMN cover_id;
        DROP TABLE media;
    """)
