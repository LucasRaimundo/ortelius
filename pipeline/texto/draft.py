"""Formato da resposta do modelo e sua conversão no documento do editor de blocos.

O modelo devolve texto puro em blocos simples; nenhuma marcação dele chega ao banco. O documento gerado passa
pelo mesmo conversor da API (`to_blocks`) antes de ser aceito: o que a página não renderiza, o pipeline não grava.
"""

import html
import time
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any, cast

from app.domain.content import InvalidContentError
from app.infrastructure.editorjs import to_blocks
from pipeline.wikidata.mapping import slugify

# Versão do Editor.js usada pelo painel (Directus 12.6.1); o painel abre o documento como se ele mesmo o tivesse salvo.
EDITORJS_VERSION = "2.31.2"
MAX_SECTIONS = 12
MAX_KEY_LENGTH = 80

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Uma ou duas frases para o cartão do globo e a busca."},
        "description": {"type": "string", "description": "Introdução da página, um ou dois parágrafos."},
        "sections": {
            "type": "array",
            "minItems": 1,
            "maxItems": MAX_SECTIONS,
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Minúsculas sem acento e hífens, ex.: origem-e-familia"},
                    "title": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Identificadores (F1, F2…) das fontes que sustentam a seção.",
                    },
                    "blocks": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": ["paragraph", "heading", "list", "quote"]},
                                "text": {"type": "string", "description": "Texto puro, sem marcação."},
                                "level": {"type": "integer", "enum": [3, 4], "description": "Só para heading."},
                                "items": {"type": "array", "items": {"type": "string"}, "description": "Só para list."},
                                "ordered": {"type": "boolean", "description": "Só para list."},
                                "caption": {"type": "string", "description": "Só para quote: autor ou obra."},
                            },
                            "required": ["type"],
                        },
                    },
                },
                "required": ["title", "sources", "blocks"],
            },
        },
        "points_to_check": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Afirmações do texto que não estão explícitas nas fontes.",
        },
    },
    "required": ["summary", "description", "sections", "points_to_check"],
}


class InvalidDraftError(ValueError):
    """A resposta do modelo não pode virar rascunho; a mensagem aponta o campo."""


@dataclass(frozen=True)
class DraftSection:
    key: str
    title: str
    document: dict[str, Any]
    source_labels: tuple[str, ...]


@dataclass(frozen=True)
class TextDraft:
    summary: str
    description: str
    sections: tuple[DraftSection, ...]
    points_to_check: tuple[str, ...]


def parse_draft(payload: object, source_labels: Collection[str]) -> TextDraft:
    data = _object(payload, "resposta")
    sections_data = _list(data.get("sections"), "sections")
    if not 1 <= len(sections_data) <= MAX_SECTIONS:
        raise InvalidDraftError(f"sections: esperadas de 1 a {MAX_SECTIONS}, vieram {len(sections_data)}")
    sections: list[DraftSection] = []
    for index, raw in enumerate(sections_data):
        section = _section(raw, f"sections[{index}]", source_labels)
        used = {existing.key for existing in sections}
        if section.key in used:
            suffix = next(n for n in range(2, MAX_SECTIONS + 2) if _with_suffix(section.key, n) not in used)
            section = replace(section, key=_with_suffix(section.key, suffix))
        sections.append(section)
    return TextDraft(
        summary=_text(data, "summary", "resposta"),
        description=_text(data, "description", "resposta"),
        sections=tuple(sections),
        points_to_check=_points(data.get("points_to_check")),
    )


def _points(raw: object) -> tuple[str, ...]:
    points = (_string(item, "points_to_check").strip() for item in _list(raw, "points_to_check"))
    return tuple(point for point in points if point)


def _section_key(proposed: object, title: str, path: str) -> str:
    """A chave é técnica: o pipeline a normaliza em vez de recusar o texto por causa dela.

    Modelos menores ignoram o formato pedido (ex.: `origens_e_nascimento`); o título serve quando a sugestão
    não aproveita nada.
    """
    key = (slugify(proposed) if isinstance(proposed, str) else "") or slugify(title)
    key = key[:MAX_KEY_LENGTH].rstrip("-")
    if not key:
        raise InvalidDraftError(f"{path}.key: não foi possível formar uma chave a partir de {title!r}")
    return key


def _with_suffix(key: str, number: int) -> str:
    suffix = f"-{number}"
    return f"{key[: MAX_KEY_LENGTH - len(suffix)].rstrip('-')}{suffix}"


def _section(raw: object, path: str, source_labels: Collection[str]) -> DraftSection:
    data = _object(raw, path)
    title = _text(data, "title", path)[:180]
    key = _section_key(data.get("key"), title, path)
    labels = tuple(dict.fromkeys(_string(label, f"{path}.sources") for label in _list(data.get("sources"), path)))
    if not labels:
        raise InvalidDraftError(f"{path}.sources: a seção não indica nenhuma fonte")
    unknown = [label for label in labels if label not in source_labels]
    if unknown:
        raise InvalidDraftError(f"{path}.sources: fontes que não foram fornecidas: {', '.join(unknown)}")
    blocks = [_editor_block(block, f"{path}.blocks[{i}]") for i, block in enumerate(_list(data.get("blocks"), path))]
    document: dict[str, Any] = {"time": int(time.time() * 1000), "version": EDITORJS_VERSION, "blocks": blocks}
    try:
        if not to_blocks(document):
            raise InvalidDraftError(f"{path}.blocks: seção sem conteúdo")
    except InvalidContentError as error:
        raise InvalidDraftError(f"{path}.blocks: {error}") from error
    return DraftSection(key=key, title=title, document=document, source_labels=labels)


def _editor_block(raw: object, path: str) -> dict[str, Any]:
    block = _object(raw, path)
    match block.get("type"):
        case "paragraph":
            return {"type": "paragraph", "data": {"text": _inline(_text(block, "text", path))}}
        case "heading":
            level = block.get("level")
            if level not in (3, 4):
                raise InvalidDraftError(f"{path}.level: título precisa de nível 3 ou 4, veio {level!r}")
            # No editor, os níveis 1 e 2 viram 3 na página, e o 3 vira 4 (app/infrastructure/editorjs.py).
            return {"type": "header", "data": {"text": _inline(_text(block, "text", path)), "level": level - 1}}
        case "list":
            items = [_inline(_string(item, f"{path}.items")) for item in _list(block.get("items"), path)]
            if not any(item.strip() for item in items):
                raise InvalidDraftError(f"{path}.items: lista vazia")
            style = "ordered" if block.get("ordered") is True else "unordered"
            entries: list[dict[str, object]] = [{"content": item, "items": []} for item in items if item.strip()]
            return {"type": "nestedlist", "data": {"style": style, "items": entries}}
        case "quote":
            caption = block.get("caption")
            return {
                "type": "quote",
                "data": {
                    "text": _inline(_text(block, "text", path)),
                    "caption": _inline(caption) if isinstance(caption, str) else "",
                    "alignment": "left",
                },
            }
        case other:
            raise InvalidDraftError(f"{path}.type: tipo de bloco desconhecido {other!r}")


def _inline(text: str) -> str:
    return html.escape(text.strip(), quote=False).replace("\n", "<br>")


def _object(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidDraftError(f"{path}: esperado um objeto")
    return cast(Mapping[str, object], value)


def _list(value: object, path: str) -> list[object]:
    if not isinstance(value, list):
        raise InvalidDraftError(f"{path}: esperada uma lista")
    return cast(list[object], value)


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise InvalidDraftError(f"{path}: esperado texto")
    return value


def _text(data: Mapping[str, object], key: str, path: str) -> str:
    value = _string(data.get(key), f"{path}.{key}").strip()
    if not value:
        raise InvalidDraftError(f"{path}.{key}: vazio")
    return value
