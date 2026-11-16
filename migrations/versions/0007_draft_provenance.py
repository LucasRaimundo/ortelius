"""Identificador do Wikidata, texto das fontes para o modelo e origem dos rascunhos gerados.

A fonte Wikidata nasce com id fixo para o pipeline citá-la sem procurar por título. O downgrade falha se ela já
estiver citada (RESTRICT): apagar a fonte apagaria a procedência das entidades importadas.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

WIKIDATA_SOURCE_ID = "6e2ae8ef-3adb-4c09-86f4-1974f17323ea"


def upgrade() -> None:
    op.execute(f"""
        ALTER TABLE entities
            ADD COLUMN wikidata_id VARCHAR(16),
            ADD CONSTRAINT uq_entities_wikidata_id UNIQUE (wikidata_id),
            ADD CONSTRAINT ck_entities_wikidata_id_format CHECK (wikidata_id ~ '^Q[1-9][0-9]*$');

        ALTER TABLE sources
            ADD COLUMN text_url TEXT,
            ADD COLUMN text_license VARCHAR(24),
            ADD CONSTRAINT ck_sources_text_url_format CHECK (text_url ~ '^https?://'),
            ADD CONSTRAINT ck_sources_text_license CHECK (text_license IN ('public_domain', 'cc0', 'cc_by')),
            ADD CONSTRAINT ck_sources_text_url_requires_license CHECK (text_url IS NULL OR text_license IS NOT NULL);

        ALTER TABLE entity_sources ADD COLUMN excerpt TEXT;

        CREATE TABLE entity_draft_runs (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL,
            kind VARCHAR(16) NOT NULL,
            model VARCHAR(120),
            points_to_check TEXT,
            details JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_entity_draft_runs PRIMARY KEY (id),
            CONSTRAINT fk_entity_draft_runs_entity_id FOREIGN KEY (entity_id)
                REFERENCES entities (id) ON DELETE CASCADE,
            CONSTRAINT ck_entity_draft_runs_kind CHECK (kind IN ('wikidata', 'text')),
            CONSTRAINT ck_entity_draft_runs_model_only_for_text CHECK ((kind = 'text') = (model IS NOT NULL)),
            CONSTRAINT ck_entity_draft_runs_details_is_object CHECK (jsonb_typeof(details) = 'object')
        );
        CREATE INDEX ix_entity_draft_runs_entity_id ON entity_draft_runs (entity_id);

        -- Só o pipeline escreve a origem; o painel apenas mostra ao revisor.
        GRANT SELECT ON entity_draft_runs TO atlas_editor;

        INSERT INTO sources (id, title, source_type, author_or_institution, url, license)
        VALUES ('{WIKIDATA_SOURCE_ID}', 'Wikidata', 'dataset', 'Wikimedia Foundation',
                'https://www.wikidata.org', 'CC0 1.0');
    """)


def downgrade() -> None:
    op.execute(f"""
        DELETE FROM sources WHERE id = '{WIKIDATA_SOURCE_ID}';
        DROP TABLE entity_draft_runs;
        ALTER TABLE entity_sources DROP COLUMN excerpt;
        ALTER TABLE sources DROP COLUMN text_license, DROP COLUMN text_url;
        ALTER TABLE entities DROP COLUMN wikidata_id;
    """)
