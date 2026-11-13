from typing import Literal

import pytest

from app.domain.content import BulletList, Heading, InvalidContentError, ListItem, Paragraph, Quote, TextRun
from app.infrastructure.editorjs import to_blocks, to_rich_text

# Documento gravado pelo editor de blocos do Directus 12.6.1 durante o spike da fase 2.
SPIKE_DOCUMENT: dict[str, object] = {
    "time": 1794596350781,
    "version": "2.31.2",
    "blocks": [
        {"id": "ol3dpqmASA", "type": "header", "data": {"text": "As campanhas na Gália", "level": 2}},
        {
            "id": "tox1joIO4J",
            "type": "paragraph",
            "data": {"text": "Entre 58 e 50 a.C., <b>César</b> conduziu campanhas."},
        },
        {
            "id": "6qFOUq7D-m",
            "type": "nestedlist",
            "data": {
                "style": "unordered",
                "items": [
                    {"items": [], "content": "Batalha de Bibracte (58 a.C.)"},
                    {"items": [], "content": "Cerco de Alésia (52 a.C.)"},
                ],
            },
        },
        {
            "id": "Rd8w-672Ed",
            "type": "quote",
            "data": {"text": "Gallia est omnis divisa in partes tres", "caption": "", "alignment": "left"},
        },
    ],
}


def test_converts_document_written_by_the_panel() -> None:
    assert to_blocks(SPIKE_DOCUMENT) == [
        Heading(level=3, text=(TextRun("As campanhas na Gália"),)),
        Paragraph(text=(TextRun("Entre 58 e 50 a.C., "), TextRun("César", bold=True), TextRun(" conduziu campanhas."))),
        BulletList(
            style="unordered",
            items=(
                ListItem(text=(TextRun("Batalha de Bibracte (58 a.C.)"),)),
                ListItem(text=(TextRun("Cerco de Alésia (52 a.C.)"),)),
            ),
        ),
        Quote(text=(TextRun("Gallia est omnis divisa in partes tres"),), caption=None),
    ]


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("texto simples", (TextRun("texto simples"),)),
        ("<b><i>ambos</i></b> nada", (TextRun("ambos", bold=True, italic=True), TextRun(" nada"))),
        ("<strong>forte</strong><em>ênfase</em>", (TextRun("forte", bold=True), TextRun("ênfase", italic=True))),
        (
            'ver <a href="https://pt.wikipedia.org/wiki/Roma">Roma</a>',
            (TextRun("ver "), TextRun("Roma", href="https://pt.wikipedia.org/wiki/Roma")),
        ),
        ("linha 1<br>linha 2", (TextRun("linha 1\nlinha 2"),)),
        ("Tom &amp; Jerry&nbsp;fim", (TextRun("Tom & Jerry fim"),)),
        ('<span style="color:red">sem</span> <u>marcas</u>', (TextRun("sem marcas"),)),
        ("</b>fecha antes de abrir", (TextRun("fecha antes de abrir"),)),
        ("", ()),
    ],
)
def test_converts_inline_html_to_text_runs(html: str, expected: tuple[TextRun, ...]) -> None:
    assert to_rich_text(html) == expected


@pytest.mark.parametrize(
    "html",
    [
        '<a href="javascript:alert(1)">clique</a>',
        '<a href="data:text/html,oi">clique</a>',
        '<a href="//sem-esquema.com">clique</a>',
        "<a>clique</a>",
    ],
)
def test_drops_links_that_are_not_http(html: str) -> None:
    assert to_rich_text(html) == (TextRun("clique"),)


@pytest.mark.parametrize(
    "html",
    [
        "antes<script>alert(1)</script>depois",
        "antes<style>body{display:none}</style>depois",
        'antes<img src=x onerror="alert(1)">depois',
        "antes<svg><text>x</text></svg>depois",
    ],
)
def test_discards_executable_or_invisible_markup_entirely(html: str) -> None:
    assert to_rich_text(html) == (TextRun("antesdepois"),)


@pytest.mark.parametrize(("editor_level", "atlas_level"), [(1, 3), (2, 3), (3, 4), (6, 4)])
def test_maps_heading_levels_below_the_section_title(editor_level: int, atlas_level: Literal[3, 4]) -> None:
    document = {"blocks": [{"type": "header", "data": {"text": "T", "level": editor_level}}]}

    assert to_blocks(document) == [Heading(level=atlas_level, text=(TextRun("T"),))]


def test_skips_empty_paragraphs_left_by_the_editor() -> None:
    document = {"blocks": [{"type": "paragraph", "data": {"text": ""}}, {"type": "paragraph", "data": {"text": "x"}}]}

    assert to_blocks(document) == [Paragraph(text=(TextRun("x"),))]


def test_keeps_nested_list_structure() -> None:
    nested: dict[str, object] = {"content": "filho", "items": []}
    data: dict[str, object] = {"style": "ordered", "items": [{"content": "pai", "items": [nested]}]}
    document: dict[str, object] = {"blocks": [{"type": "nestedlist", "data": data}]}

    assert to_blocks(document) == [
        BulletList(
            style="ordered",
            items=(ListItem(text=(TextRun("pai"),), items=(ListItem(text=(TextRun("filho"),)),)),),
        )
    ]


def test_quote_caption_is_plain_text() -> None:
    document = {"blocks": [{"type": "quote", "data": {"text": "citação", "caption": "<b>Júlio César</b>"}}]}

    assert to_blocks(document) == [Quote(text=(TextRun("citação"),), caption="Júlio César")]


UNREPRESENTABLE_DOCUMENTS: list[tuple[object, str]] = [
    ([], "não é um objeto"),
    ({"time": 1}, "sem lista de blocos"),
    ({"blocks": [{"type": "image", "data": {}}]}, "tipo de bloco não suportado: 'image'"),
    ({"blocks": [{"type": "paragraph"}]}, "dados do bloco não é um objeto"),
    ({"blocks": [{"type": "paragraph", "data": {"text": 42}}]}, "campo 'text'"),
    ({"blocks": [{"type": "header", "data": {"text": "T", "level": 7}}]}, "nível de título inválido"),
    ({"blocks": [{"type": "header", "data": {"text": "T", "level": True}}]}, "nível de título inválido"),
    ({"blocks": [{"type": "nestedlist", "data": {"style": "checklist", "items": []}}]}, "estilo de lista"),
    ({"blocks": [{"type": "nestedlist", "data": {"style": "ordered", "items": "x"}}]}, "itens da lista"),
]


@pytest.mark.parametrize(("document", "reason"), UNREPRESENTABLE_DOCUMENTS)
def test_rejects_documents_it_cannot_represent(document: object, reason: str) -> None:
    with pytest.raises(InvalidContentError, match=reason):
        to_blocks(document)
