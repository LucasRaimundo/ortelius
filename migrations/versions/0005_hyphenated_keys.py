"""Chaves de seção e de tipo de relação com hífen, no mesmo formato do slug das entidades.

O painel converte o que o revisor digita em slug com hífen; com sublinhado, toda chave digitada no painel era
recusada pelo banco.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

HYPHENATED = "^[a-z0-9]+(-[a-z0-9]+)*$"
UNDERSCORED = "^[a-z0-9]+(_[a-z0-9]+)*$"


def _convert(pattern: str, from_char: str, to_char: str) -> None:
    # relation_types.key é referenciada com ON UPDATE CASCADE: as relações acompanham a troca.
    op.execute(f"""
        ALTER TABLE entity_sections DROP CONSTRAINT ck_entity_sections_section_key_format;
        ALTER TABLE relation_types DROP CONSTRAINT ck_relation_types_key_format;
        UPDATE entity_sections SET section_key = replace(section_key, '{from_char}', '{to_char}');
        UPDATE relation_types SET key = replace(key, '{from_char}', '{to_char}');
        ALTER TABLE entity_sections ADD CONSTRAINT ck_entity_sections_section_key_format
            CHECK (section_key ~ '{pattern}');
        ALTER TABLE relation_types ADD CONSTRAINT ck_relation_types_key_format CHECK (key ~ '{pattern}');
    """)


def upgrade() -> None:
    _convert(HYPHENATED, "_", "-")


def downgrade() -> None:
    _convert(UNDERSCORED, "-", "_")
