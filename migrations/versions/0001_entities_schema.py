"""Schema inicial de entidades, seções, relações e fontes.

Revision ID: 0001
Revises:
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE EXTENSION IF NOT EXISTS unaccent;
        CREATE EXTENSION IF NOT EXISTS pg_trgm;

        CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$;

        CREATE TABLE entities (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            slug VARCHAR(180) NOT NULL,
            name VARCHAR(240) NOT NULL,
            entity_type VARCHAR(40) NOT NULL,
            summary TEXT,
            description TEXT,
            status VARCHAR(24) NOT NULL DEFAULT 'draft',
            cover_media TEXT,
            location geometry(POINT, 4326),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_entities PRIMARY KEY (id),
            CONSTRAINT uq_entities_slug UNIQUE (slug),
            CONSTRAINT ck_entities_slug_format CHECK (slug ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),
            CONSTRAINT ck_entities_entity_type CHECK (entity_type IN
                ('place', 'civilization', 'state', 'person', 'event', 'culture', 'work', 'concept')),
            CONSTRAINT ck_entities_status CHECK (status IN ('draft', 'published', 'archived')),
            CONSTRAINT ck_entities_location_valid CHECK (ST_IsValid(location))
        );
        CREATE INDEX ix_entities_entity_type ON entities (entity_type);
        CREATE INDEX ix_entities_location ON entities USING gist (location);

        CREATE TABLE entity_aliases (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL,
            alias VARCHAR(240) NOT NULL,
            language_code VARCHAR(12),
            alias_type VARCHAR(32),
            start_year INTEGER,
            end_year INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_entity_aliases PRIMARY KEY (id),
            CONSTRAINT fk_entity_aliases_entity_id FOREIGN KEY (entity_id)
                REFERENCES entities (id) ON DELETE CASCADE,
            CONSTRAINT ck_entity_aliases_year_range CHECK (start_year <= end_year)
        );
        CREATE INDEX ix_entity_aliases_entity_id ON entity_aliases (entity_id);

        CREATE TABLE entity_sections (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL,
            section_key VARCHAR(80) NOT NULL,
            title VARCHAR(180) NOT NULL,
            position INTEGER NOT NULL,
            content JSONB NOT NULL DEFAULT '[]'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_entity_sections PRIMARY KEY (id),
            CONSTRAINT fk_entity_sections_entity_id FOREIGN KEY (entity_id)
                REFERENCES entities (id) ON DELETE CASCADE,
            CONSTRAINT uq_entity_sections_entity_id_section_key UNIQUE (entity_id, section_key),
            -- Diferida para que o painel editorial possa trocar a ordem de duas seções numa transação.
            CONSTRAINT uq_entity_sections_entity_id_position UNIQUE (entity_id, position)
                DEFERRABLE INITIALLY DEFERRED,
            CONSTRAINT ck_entity_sections_section_key_format CHECK (section_key ~ '^[a-z0-9]+(_[a-z0-9]+)*$'),
            CONSTRAINT ck_entity_sections_content_is_array CHECK (jsonb_typeof(content) = 'array')
        );

        -- Tabela em vez de CHECK: a lista de tipos de relação é editorial e cresce sem migration,
        -- mas continua controlada pela FK.
        CREATE TABLE relation_types (
            key VARCHAR(60) NOT NULL,
            label VARCHAR(120) NOT NULL,
            inverse_label VARCHAR(120),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_relation_types PRIMARY KEY (key),
            CONSTRAINT ck_relation_types_key_format CHECK (key ~ '^[a-z0-9]+(_[a-z0-9]+)*$')
        );

        CREATE TABLE entity_relations (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            source_entity_id UUID NOT NULL,
            target_entity_id UUID NOT NULL,
            relation_type VARCHAR(60) NOT NULL,
            description TEXT,
            is_directional BOOLEAN NOT NULL DEFAULT true,
            start_year INTEGER,
            end_year INTEGER,
            position INTEGER,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_entity_relations PRIMARY KEY (id),
            CONSTRAINT fk_entity_relations_source_entity_id FOREIGN KEY (source_entity_id)
                REFERENCES entities (id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_relations_target_entity_id FOREIGN KEY (target_entity_id)
                REFERENCES entities (id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_relations_relation_type FOREIGN KEY (relation_type)
                REFERENCES relation_types (key) ON UPDATE CASCADE ON DELETE RESTRICT,
            CONSTRAINT uq_entity_relations_source_target_type
                UNIQUE (source_entity_id, target_entity_id, relation_type),
            CONSTRAINT ck_entity_relations_not_self CHECK (source_entity_id <> target_entity_id),
            CONSTRAINT ck_entity_relations_year_range CHECK (start_year <= end_year)
        );
        CREATE INDEX ix_entity_relations_target_entity_id ON entity_relations (target_entity_id);

        CREATE TABLE sources (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            title VARCHAR(500) NOT NULL,
            source_type VARCHAR(48) NOT NULL,
            author_or_institution VARCHAR(300),
            publication_year INTEGER,
            publisher_or_journal VARCHAR(240),
            identifier VARCHAR(180),
            url TEXT,
            language_code VARCHAR(12),
            source_nature VARCHAR(32),
            reliability_note TEXT,
            license VARCHAR(180),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_sources PRIMARY KEY (id),
            CONSTRAINT ck_sources_source_type CHECK (source_type IN
                ('book', 'article', 'primary_source', 'map', 'archive', 'dataset', 'journalism'))
        );

        CREATE TABLE entity_sources (
            id UUID NOT NULL DEFAULT gen_random_uuid(),
            entity_id UUID NOT NULL,
            source_id UUID NOT NULL,
            contribution_type VARCHAR(48) NOT NULL,
            locator VARCHAR(500),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_entity_sources PRIMARY KEY (id),
            CONSTRAINT fk_entity_sources_entity_id FOREIGN KEY (entity_id)
                REFERENCES entities (id) ON DELETE CASCADE,
            CONSTRAINT fk_entity_sources_source_id FOREIGN KEY (source_id)
                REFERENCES sources (id) ON DELETE RESTRICT
        );
        CREATE INDEX ix_entity_sources_entity_id ON entity_sources (entity_id);
        CREATE INDEX ix_entity_sources_source_id ON entity_sources (source_id);

        CREATE TRIGGER trg_entities_updated_at BEFORE UPDATE ON entities
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER trg_entity_aliases_updated_at BEFORE UPDATE ON entity_aliases
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER trg_entity_sections_updated_at BEFORE UPDATE ON entity_sections
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER trg_relation_types_updated_at BEFORE UPDATE ON relation_types
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER trg_entity_relations_updated_at BEFORE UPDATE ON entity_relations
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER trg_sources_updated_at BEFORE UPDATE ON sources
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();
        CREATE TRIGGER trg_entity_sources_updated_at BEFORE UPDATE ON entity_sources
            FOR EACH ROW EXECUTE FUNCTION set_updated_at();

        -- Regra editorial do MVP: entidade publicada sempre tem ao menos uma fonte. Fica no banco para
        -- valer para qualquer escritor (painel editorial, pipeline, SQL manual). Os triggers são
        -- diferidos para o fim da transação, permitindo criar a entidade e suas fontes juntas.
        CREATE FUNCTION assert_published_entity_has_source(target_entity_id UUID) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM entities WHERE id = target_entity_id AND status = 'published')
               AND NOT EXISTS (SELECT 1 FROM entity_sources WHERE entity_id = target_entity_id) THEN
                RAISE EXCEPTION 'entidade publicada sem fonte: %', target_entity_id
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'published_entity_requires_source';
            END IF;
        END;
        $$;

        CREATE FUNCTION check_entity_source_on_entity_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM assert_published_entity_has_source(NEW.id);
            RETURN NULL;
        END;
        $$;

        CREATE FUNCTION check_entity_source_on_source_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM assert_published_entity_has_source(OLD.entity_id);
            RETURN NULL;
        END;
        $$;

        CREATE CONSTRAINT TRIGGER trg_entities_published_requires_source
            AFTER INSERT OR UPDATE OF status ON entities
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION check_entity_source_on_entity_change();

        CREATE CONSTRAINT TRIGGER trg_entity_sources_keep_published_source
            AFTER DELETE OR UPDATE OF entity_id ON entity_sources
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION check_entity_source_on_source_change();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE entity_sources, sources, entity_relations, relation_types, entity_sections,
            entity_aliases, entities;
        DROP FUNCTION check_entity_source_on_source_change();
        DROP FUNCTION check_entity_source_on_entity_change();
        DROP FUNCTION assert_published_entity_has_source(UUID);
        DROP FUNCTION set_updated_at();
    """)
