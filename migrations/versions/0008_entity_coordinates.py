"""Latitude e longitude editáveis em `entities`, sincronizadas com `location`.

O mapa do painel só aceita clique, e no aceite da Fase 2b duas entidades ficaram a dezenas de quilômetros do ponto
certo. As colunas deixam o revisor digitar as coordenadas; `location` continua sendo o que a API e o índice espacial
usam. Um trigger mantém os dois lados iguais em qualquer escrita (painel, pipeline ou SQL): coordenadas alteradas
recalculam o ponto; ponto alterado (clique no mapa, importador do Wikidata) recalcula as coordenadas. Se os dois
mudarem na mesma escrita, valem as coordenadas, que são digitadas de propósito.

As colunas são preenchidas antes de o trigger existir, para o arredondamento a 6 casas (cerca de 10 cm) não alterar
os pontos já gravados, e com `trg_entities_updated_at` desligado: preencher um espelho não é edição de conteúdo.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE entities
            ADD COLUMN latitude NUMERIC(9, 6),
            ADD COLUMN longitude NUMERIC(9, 6),
            ADD CONSTRAINT ck_entities_latitude_range CHECK (latitude BETWEEN -90 AND 90),
            ADD CONSTRAINT ck_entities_longitude_range CHECK (longitude BETWEEN -180 AND 180),
            ADD CONSTRAINT ck_entities_coordinates_together CHECK ((latitude IS NULL) = (longitude IS NULL));

        ALTER TABLE entities DISABLE TRIGGER trg_entities_updated_at;
        UPDATE entities
        SET latitude = round(ST_Y(location)::numeric, 6), longitude = round(ST_X(location)::numeric, 6)
        WHERE location IS NOT NULL;
        ALTER TABLE entities ENABLE TRIGGER trg_entities_updated_at;

        CREATE FUNCTION sync_entity_coordinates() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            coordinates_changed boolean;
            location_changed boolean;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                coordinates_changed := NEW.latitude IS NOT NULL OR NEW.longitude IS NOT NULL;
                location_changed := NEW.location IS NOT NULL;
            ELSE
                coordinates_changed := NEW.latitude IS DISTINCT FROM OLD.latitude
                    OR NEW.longitude IS DISTINCT FROM OLD.longitude;
                location_changed := NEW.location IS DISTINCT FROM OLD.location;
            END IF;

            IF coordinates_changed THEN
                -- Só uma das duas preenchida é barrada por ck_entities_coordinates_together.
                NEW.location := CASE
                    WHEN NEW.latitude IS NOT NULL AND NEW.longitude IS NOT NULL
                    THEN ST_SetSRID(ST_MakePoint(NEW.longitude, NEW.latitude), 4326)
                END;
            ELSIF location_changed THEN
                NEW.latitude := round(ST_Y(NEW.location)::numeric, 6);
                NEW.longitude := round(ST_X(NEW.location)::numeric, 6);
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_entities_sync_coordinates
            BEFORE INSERT OR UPDATE OF latitude, longitude, location ON entities
            FOR EACH ROW EXECUTE FUNCTION sync_entity_coordinates();
    """)


def downgrade() -> None:
    op.execute("""
        DROP TRIGGER trg_entities_sync_coordinates ON entities;
        DROP FUNCTION sync_entity_coordinates();
        ALTER TABLE entities DROP COLUMN longitude, DROP COLUMN latitude;
    """)
