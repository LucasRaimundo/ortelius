# Ortelius

Backend do Atlas: API, domínio, PostGIS, painel editorial e pipeline de ingestão. Visão, arquitetura e
especificações ficam no repositório `oAtlas` (`CORE.md`, `ARCH.md`, `docs/projects/`).

## Rodar

```bash
cp .env.example .env
docker compose up -d --wait   # PostGIS local
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:create_app --factory --reload
```

## Painel editorial (Directus)

O Directus guarda as próprias tabelas no schema `directus` e acessa as tabelas de domínio pelo papel
`atlas_editor`, que só lê e escreve dados: ele não consegue criar, alterar nem apagar estrutura
(migration `0002`). Para subir localmente, com o PostGIS rodando e as migrations aplicadas:

```bash
cp cms/.env.example cms/.env          # troque SECRET por um valor aleatório
cms/create-dev-role.sh                # cria o usuário de login `directus`, membro de atlas_editor
docker compose --profile cms up -d    # painel em http://localhost:8055
```

Configuração do painel (rótulos, campos, relações) vive em `cms/config.json` e é aplicada pela API do
Directus, alterando só o que difere:

```bash
uv run python -m cms.apply_config --check   # mostra o que mudaria
uv run python -m cms.apply_config           # aplica
```

O config também cria o papel **Editor** (lê e escreve só as coleções de conteúdo e arquivos) e dois Flows
que recusam publicar uma entidade sem fonte, com mensagem legível. A garantia real continua sendo o trigger
do banco; os Flows só melhoram a mensagem. Os scripts dos Flows ficam em `cms/flows/`. Flows gerenciados
pelo config são recriados quando mudam: não os edite pelo painel.

Editores entram por convite (Usuários → Convidar, papel Editor); o cadastro público fica desligado. No plano
Core, o limite é de 3 usuários, incluindo o admin.

**Telemetria:** no plano Core, a licença do Directus exige telemetria e `TELEMETRY=false` é ignorado. A cada
6 horas ele envia contagens agregadas (usuários, coleções, itens, tamanho do banco), a versão e a URL pública
do painel, sem conteúdo nem e-mails. O envio de dados do dono do projeto fica desligado
(`PROJECT_OWNER_ENABLED=false`).

Toda coluna de domínio precisa estar configurada em `cms/config.json` (ou na lista `hidden_fields`); um
teste falha caso contrário. Fora do ambiente local, o applier usa `DIRECTUS_URL`, `DIRECTUS_TOKEN` e
`DIRECTUS_DB_URL`.

Em produção, o usuário `directus` é provisionado pela infraestrutura, com a senha no gerenciador de
segredos. O usuário que roda as migrations precisa da permissão `CREATEROLE`, porque a `0002` cria o
papel `atlas_editor`.

## Importar do Wikidata

Cria rascunhos a partir de itens do Wikidata (CC0), com o banco rodando e as migrations aplicadas:

```bash
uv run python -m pipeline.wikidata Q1405 Q131386       # Augusto e Batalha de Alésia
uv run python -m pipeline.wikidata Q60220 --slug eneida-de-virgilio
uv run python -m pipeline.wikidata Q123 --tipo concept  # quando o P31 do item não indica o tipo
```

- Traz nome, tipo, resumo curto, localização, nomes alternativos (rótulos em vários idiomas e aliases em
  português) e relações com entidades que **já existem** no Atlas, ligadas pelo campo "Código no Wikidata".
  Preencha esse código nas entidades cadastradas à mão para que as novas se liguem a elas.
- Tudo nasce em rascunho, citando a fonte Wikidata. A lista "Origem do rascunho" da entidade mostra o que veio,
  os pontos a conferir e as relações cujo alvo ainda não está no Atlas.
- Rodar de novo num rascunho só completa campos vazios e acrescenta o que falta: edições do revisor ficam.
  Entidades publicadas ou arquivadas nunca são alteradas.
- Cada item é gravado numa transação. Falha num item (rede, tipo desconhecido, endereço já usado) não grava
  nada dele e não interrompe os demais; o comando termina com código 1 e diz o motivo de cada falha.
- O tipo vem de uma tabela de valores de P31 e as relações de uma tabela de propriedades, ambas em
  `pipeline/wikidata/mapping.py`. Datas da entidade ainda não são importadas (dependem do modelo de período da
  fase 6).

## Verificar

Os testes usam o PostGIS do `compose.yaml`. O CI (`.github/workflows/ci.yml`) roda a mesma verificação em todo
PR para a `main`.

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

## Lançar versão

Fluxo e convenção de versões em `oAtlas/ARCH.md` §17. Na `main` atualizada, com o PostGIS rodando:

```bash
scripts/release.sh 0.2.0 --dry-run   # checagens, verificação e prévia das notas, sem criar nada
scripts/release.sh 0.2.0             # cria a tag v0.2.0 e envia ao GitHub
```

O workflow `release` cria a release no GitHub com as notas agrupadas por tipo de commit (`0.x` sai como
pre-release). Para regerar a release de uma tag existente, rode o workflow manualmente informando a tag.
