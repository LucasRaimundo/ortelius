"""Datas de início e fim da entidade: vida de uma pessoa, duração de um evento, vigência de um Estado.

Mesma convenção das relações e dos nomes alternativos: ano astronômico (1 a.C. = 0, 70 a.C. = -69), sem mês e
dia. Datas aproximadas ("c. 70 a.C.", séculos) ficam fora até haver necessidade.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-26
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE entities
            ADD COLUMN start_year INTEGER,
            ADD COLUMN end_year INTEGER,
            ADD CONSTRAINT ck_entities_year_range CHECK (start_year <= end_year);
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE entities DROP COLUMN end_year, DROP COLUMN start_year;")
