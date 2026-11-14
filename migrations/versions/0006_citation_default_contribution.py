"""Papel padrão "fundamenta" nas citações.

Ao escolher uma fonte existente no painel, a citação nasce só com entidade e fonte; o papel é ajustado depois,
se for outro.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE entity_sources ALTER COLUMN contribution_type SET DEFAULT 'fundamenta'")


def downgrade() -> None:
    op.execute("ALTER TABLE entity_sources ALTER COLUMN contribution_type DROP DEFAULT")
