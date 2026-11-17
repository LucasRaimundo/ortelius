import copy
from typing import Any

import pytest

from app.domain.content import BulletList, Heading, ListItem, Paragraph, Quote, TextRun
from app.infrastructure.editorjs import to_blocks
from pipeline.texto.draft import InvalidDraftError, parse_draft

LABELS = {"F1", "F2"}


def payload(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "summary": " Primeiro imperador romano. ",
        "description": "Otávio, depois chamado Augusto.",
        "sections": [
            {
                "key": "origem",
                "title": "Origem",
                "sources": ["F1", "F1"],
                "blocks": [
                    {"type": "heading", "level": 3, "text": "Nascimento"},
                    {"type": "paragraph", "text": "Nasceu em Roma.\nPerdeu o pai aos quatro anos."},
                    {"type": "list", "ordered": True, "items": ["Pai: Caio Otávio", " "]},
                    {"type": "quote", "text": "Encontrei Roma de tijolos", "caption": "Suetônio"},
                ],
            }
        ],
        "points_to_check": ["Data do discurso.", "  "],
    }
    base.update(changes)
    return base


def with_section(**changes: Any) -> dict[str, Any]:
    data = payload()
    data["sections"][0].update(changes)
    return data


def test_converts_the_model_answer_into_what_the_page_renders() -> None:
    draft = parse_draft(payload(), LABELS)

    [section] = draft.sections
    assert (draft.summary, draft.points_to_check) == ("Primeiro imperador romano.", ("Data do discurso.",))
    assert (section.key, section.title, section.source_labels) == ("origem", "Origem", ("F1",))
    assert to_blocks(section.document) == [
        Heading(level=3, text=(TextRun("Nascimento"),)),
        Paragraph(text=(TextRun("Nasceu em Roma.\nPerdeu o pai aos quatro anos."),)),
        BulletList(style="ordered", items=(ListItem(text=(TextRun("Pai: Caio Otávio"),)),)),
        Quote(text=(TextRun("Encontrei Roma de tijolos"),), caption="Suetônio"),
    ]


def test_markup_from_the_model_stays_as_visible_text() -> None:
    draft = parse_draft(
        with_section(blocks=[{"type": "paragraph", "text": "<b>negrito</b> & <script>x()</script>"}]), LABELS
    )

    assert to_blocks(draft.sections[0].document) == [
        Paragraph(text=(TextRun("<b>negrito</b> & <script>x()</script>"),))
    ]


def test_heading_level_four_stays_below_level_three() -> None:
    draft = parse_draft(with_section(blocks=[{"type": "heading", "level": 4, "text": "Sub"}]), LABELS)

    assert to_blocks(draft.sections[0].document) == [Heading(level=4, text=(TextRun("Sub"),))]


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (with_section(sources=["F3"]), r"sections\[0\]\.sources: fontes que não foram fornecidas: F3"),
        (with_section(sources=[]), "não indica nenhuma fonte"),
        (with_section(blocks=[]), r"sections\[0\]\.blocks: seção sem conteúdo"),
        (with_section(blocks=[{"type": "image"}]), "tipo de bloco desconhecido 'image'"),
        (with_section(blocks=[{"type": "heading", "level": 2, "text": "T"}]), "nível 3 ou 4"),
        (with_section(blocks=[{"type": "list", "items": [" "]}]), "lista vazia"),
        (with_section(blocks=[{"type": "paragraph", "text": "  "}]), r"blocks\[0\]\.text: vazio"),
        (payload(summary=""), r"resposta\.summary: vazio"),
        (payload(sections=[]), "esperadas de 1 a 12"),
        (payload(points_to_check="nenhum"), "points_to_check: esperada uma lista"),
        ([], "resposta: esperado um objeto"),
    ],
)
def test_refuses_answers_that_cannot_become_a_draft(data: object, message: str) -> None:
    with pytest.raises(InvalidDraftError, match=message):
        parse_draft(data, LABELS)


@pytest.mark.parametrize(
    ("key", "title", "expected"),
    [
        ("origens_e_nascimento", "Origens", "origens-e-nascimento"),
        ("Origem Familiar", "Origem", "origem-familiar"),
        (None, "Contexto e cerco à Alésia", "contexto-e-cerco-a-alesia"),
        ("___", "Morte", "morte"),
        ("a" * 90, "T", "a" * 80),
    ],
)
def test_section_key_is_normalized_instead_of_refused(key: object, title: str, expected: str) -> None:
    draft = parse_draft(with_section(key=key, title=title), LABELS)

    assert draft.sections[0].key == expected


def test_repeated_section_keys_get_a_numeric_suffix() -> None:
    data = payload()
    data["sections"] += [copy.deepcopy(data["sections"][0]), copy.deepcopy(data["sections"][0])]

    assert [section.key for section in parse_draft(data, LABELS).sections] == ["origem", "origem-2", "origem-3"]


def test_title_without_usable_characters_is_refused() -> None:
    with pytest.raises(InvalidDraftError, match="não foi possível formar uma chave"):
        parse_draft(with_section(key=None, title="Ἀλησία"), LABELS)
