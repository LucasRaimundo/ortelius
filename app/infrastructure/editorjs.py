"""Converte o documento do editor de blocos do Directus (Editor.js) nos blocos de conteúdo do Atlas.

O Editor.js guarda a formatação dentro do texto como HTML. Ela é convertida em trechos estruturados e o HTML
nunca sai deste módulo: só negrito, itálico e links http(s) sobrevivem; o resto vira texto puro.
"""

from collections.abc import Mapping
from html.parser import HTMLParser
from typing import Literal, cast
from urllib.parse import urlparse

from app.domain.content import (
    Block,
    BulletList,
    Heading,
    InvalidContentError,
    ListItem,
    Paragraph,
    Quote,
    RichText,
    TextRun,
)

_BOLD_TAGS = frozenset({"b", "strong"})
_ITALIC_TAGS = frozenset({"i", "em"})
# Conteúdo destas tags nunca é texto do editor; é descartado por inteiro, não só a marcação.
_DISCARDED_TAGS = frozenset({"script", "style", "template", "iframe", "object", "svg", "math"})


def to_blocks(document: object) -> list[Block]:
    if not isinstance(document, Mapping):
        raise InvalidContentError("documento não é um objeto")
    blocks = cast(Mapping[str, object], document).get("blocks")
    if not isinstance(blocks, list):
        raise InvalidContentError("documento sem lista de blocos")
    converted = (_to_block(block) for block in cast(list[object], blocks))
    return [block for block in converted if block is not None]


def to_rich_text(html: str) -> RichText:
    parser = _InlineHtmlParser()
    parser.feed(html)
    parser.close()
    return parser.runs()


def _to_block(raw: object) -> Block | None:
    block = _mapping(raw, "bloco")
    data = _mapping(block.get("data"), "dados do bloco")
    match block.get("type"):
        case "paragraph":
            text = to_rich_text(_string(data, "text"))
            return Paragraph(text) if text else None  # o editor cria parágrafos vazios ao apertar Enter
        case "header":
            return Heading(level=_heading_level(data.get("level")), text=to_rich_text(_string(data, "text")))
        case "nestedlist":
            style = data.get("style")
            if style not in ("ordered", "unordered"):
                raise InvalidContentError(f"estilo de lista não suportado: {style!r}")
            return BulletList(style=style, items=_list_items(data.get("items")))
        case "quote":
            caption = "".join(run.text for run in to_rich_text(_string(data, "caption", default=""))).strip()
            return Quote(text=to_rich_text(_string(data, "text")), caption=caption or None)
        case other:
            raise InvalidContentError(f"tipo de bloco não suportado: {other!r}")


def _heading_level(level: object) -> Literal[3, 4]:
    # Na página, o nível 1 é o nome da entidade e o 2 é o título da seção; títulos dentro dela começam no 3.
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 6:
        raise InvalidContentError(f"nível de título inválido: {level!r}")
    return 3 if level <= 2 else 4


def _list_items(raw: object) -> tuple[ListItem, ...]:
    if not isinstance(raw, list):
        raise InvalidContentError("itens da lista não são uma lista")
    items: list[ListItem] = []
    for raw_item in cast(list[object], raw):
        item = _mapping(raw_item, "item da lista")
        items.append(ListItem(text=to_rich_text(_string(item, "content")), items=_list_items(item.get("items", []))))
    return tuple(items)


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise InvalidContentError(f"{what} não é um objeto")
    return cast(Mapping[str, object], value)


def _string(data: Mapping[str, object], key: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise InvalidContentError(f"campo {key!r} ausente ou não é texto")
    return value


def _safe_href(value: str | None) -> str | None:
    if value is None:
        return None
    url = urlparse(value.strip())
    return url.geturl() if url.scheme in ("http", "https") and url.netloc else None


class _InlineHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._bold = 0
        self._italic = 0
        self._links: list[str | None] = []
        self._discarding = 0
        self._runs: list[TextRun] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _DISCARDED_TAGS:
            self._discarding += 1
        elif tag in _BOLD_TAGS:
            self._bold += 1
        elif tag in _ITALIC_TAGS:
            self._italic += 1
        elif tag == "a":
            self._links.append(_safe_href(dict(attrs).get("href")))
        elif tag == "br":
            self.handle_data("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DISCARDED_TAGS:
            self._discarding = max(0, self._discarding - 1)
        elif tag in _BOLD_TAGS:
            self._bold = max(0, self._bold - 1)
        elif tag in _ITALIC_TAGS:
            self._italic = max(0, self._italic - 1)
        elif tag == "a" and self._links:
            self._links.pop()

    def handle_data(self, data: str) -> None:
        if self._discarding or not data:
            return
        href = next((link for link in reversed(self._links) if link is not None), None)
        run = TextRun(text=data.replace("\xa0", " "), bold=self._bold > 0, italic=self._italic > 0, href=href)
        previous = self._runs[-1] if self._runs else None
        if previous and (previous.bold, previous.italic, previous.href) == (run.bold, run.italic, run.href):
            self._runs[-1] = TextRun(previous.text + run.text, run.bold, run.italic, run.href)
        else:
            self._runs.append(run)

    def runs(self) -> RichText:
        return tuple(run for run in self._runs if run.text)
