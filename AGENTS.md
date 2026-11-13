# INSTRUÇÕES DO AGENTE — ortelius

As instruções completas estão em `../oAtlas/AGENTS.md` e valem integralmente aqui. Leia-as no início da sessão,
junto com `../oAtlas/CORE.md`, `../oAtlas/ARCH.md` e a spec ativa em `../oAtlas/docs/projects/`.

Específico deste repositório:

- Python gerenciado com `uv`; nada de `pip install` direto.
- O schema do banco pertence às migrations do Alembic. Nenhuma ferramenta externa (ex.: Directus) cria ou altera
  tabelas de domínio.
- Antes de concluir qualquer tarefa: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`.
