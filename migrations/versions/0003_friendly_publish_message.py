"""Mensagem da regra "publicar exige fonte" em linguagem de editor.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _replace_assertion(message: str, detail: str | None) -> None:
    using = "ERRCODE = 'check_violation', CONSTRAINT = 'published_entity_requires_source'"
    if detail is not None:
        using += f", DETAIL = {detail}"
    op.execute(f"""
        CREATE OR REPLACE FUNCTION assert_published_entity_has_source(target_entity_id UUID) RETURNS void
        LANGUAGE plpgsql AS $$
        BEGIN
            IF EXISTS (SELECT 1 FROM entities WHERE id = target_entity_id AND status = 'published')
               AND NOT EXISTS (SELECT 1 FROM entity_sources WHERE entity_id = target_entity_id) THEN
                RAISE EXCEPTION {message} USING {using};
            END IF;
        END;
        $$;
    """)


def upgrade() -> None:
    # O painel editorial exibe a mensagem do banco diretamente ao editor; o identificador da entidade
    # vai para DETAIL, que chega aos logs sem poluir a tela.
    _replace_assertion(
        message="'Para publicar, cadastre ao menos uma fonte em \"Fontes\".'",
        detail="format('entidade %s publicada sem fonte', target_entity_id)",
    )


def downgrade() -> None:
    _replace_assertion(message="'entidade publicada sem fonte: %', target_entity_id", detail=None)
