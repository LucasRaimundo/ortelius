#!/usr/bin/env bash
# Gera as notas de versão em Markdown a partir dos commits entre duas referências.
# Uso: scripts/release-notes.sh <de|""> <até>
#   <de> vazio usa o histórico inteiro até <até> (primeira versão).
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "uso: $0 <de|\"\"> <até>" >&2
    exit 2
fi

from="$1"
to="$2"
range="${from:+$from..}$to"

features=()
fixes=()
maintenance=()
others=()

while IFS=$'\t' read -r subject hash; do
    [[ -z "$subject" ]] && continue
    line="- ${subject} (${hash})"
    if [[ "$subject" =~ ^feat(\(.+\))?!?: ]]; then
        features+=("$line")
    elif [[ "$subject" =~ ^fix(\(.+\))?!?: ]]; then
        fixes+=("$line")
    elif [[ "$subject" =~ ^(chore|docs|test|refactor|perf|build|ci)(\(.+\))?!?: ]]; then
        maintenance+=("$line")
    else
        others+=("$line")
    fi
done < <(git log --no-merges --reverse --format='%s%x09%h' "$range")

print_group() {
    local title="$1"
    shift
    [[ $# -eq 0 ]] && return
    printf '## %s\n\n' "$title"
    printf '%s\n' "$@"
    printf '\n'
}

print_group "Funcionalidades" "${features[@]}"
print_group "Correções" "${fixes[@]}"
print_group "Manutenção" "${maintenance[@]}"
print_group "Outros" "${others[@]}"
