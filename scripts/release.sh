#!/usr/bin/env bash
# Cria e publica a tag de uma versão (ver oAtlas/ARCH.md §17). A release no GitHub é gerada pelo
# workflow .github/workflows/release.yml quando a tag chega ao remoto.
# Uso: scripts/release.sh <X.Y.Z> [--dry-run]
set -euo pipefail

usage() {
    echo "uso: $0 <X.Y.Z> [--dry-run]" >&2
    exit 2
}

fail() {
    echo "erro: $*" >&2
    exit 1
}

[[ $# -ge 1 && $# -le 2 ]] || usage
version="$1"
dry_run=false
if [[ $# -eq 2 ]]; then
    [[ "$2" == "--dry-run" ]] || usage
    dry_run=true
fi

[[ "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || fail "versão '$version' não está no formato X.Y.Z"
tag="v$version"

cd "$(git rev-parse --show-toplevel)"

[[ "$(git branch --show-current)" == "main" ]] || fail "releases só saem da main"
[[ -z "$(git status --porcelain)" ]] || fail "árvore de trabalho com alterações; faça commit ou descarte antes"

git fetch --quiet --tags origin main
[[ "$(git rev-parse HEAD)" == "$(git rev-parse origin/main)" ]] || fail "main local diferente de origin/main; rode git pull --ff-only"

if git rev-parse --quiet --verify "refs/tags/$tag" >/dev/null; then
    fail "a tag $tag já existe. Se ela ficou só local por uma execução interrompida: git tag -d $tag"
fi

previous_tag="$(git tag --list 'v*' --sort=-version:refname | head -n 1)"
if [[ -n "$previous_tag" ]]; then
    highest="$(printf '%s\n%s\n' "${previous_tag#v}" "$version" | sort --version-sort | tail -n 1)"
    [[ "$highest" == "$version" ]] || fail "$tag não é maior que a última versão ($previous_tag)"
fi

echo "==> Verificação (ruff, pyright, pytest)"
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest -q

notes="$(scripts/release-notes.sh "$previous_tag" HEAD)"
printf '\n==> Notas de %s (desde %s)\n\n%s\n\n' "$tag" "${previous_tag:-o início}" "$notes"

if $dry_run; then
    echo "--dry-run: nenhuma tag criada."
    exit 0
fi

read -r -p "Criar e enviar a tag $tag? [s/N] " answer
[[ "$answer" == "s" || "$answer" == "S" ]] || fail "cancelado; nenhuma tag criada"

git tag --annotate "$tag" --message "$tag" --message "$notes"
git push origin "refs/tags/$tag"
echo "==> $tag enviada. A release será criada pelo workflow 'release' no GitHub."
