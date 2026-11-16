from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.entities import WIKIDATA_SOURCE_ID, DraftRunKind, PublicationStatus, TextLicense
from app.infrastructure.database.models import EntityDraftRunRow, EntityRow, EntitySectionRow, EntitySourceRow
from app.infrastructure.editorjs import to_blocks
from app.settings import get_settings
from pipeline.texto.__main__ import main
from pipeline.texto.draft import InvalidDraftError
from pipeline.texto.gemini import DraftRequest, ModelReply
from pipeline.texto.sources import SourceTextError
from pipeline.texto.writer import TextRefusedError, generate_text
from tests.factories import add_entity, add_section, add_source, cite

ANSWER: dict[str, Any] = {
    "summary": "Primeiro imperador romano.",
    "description": "Otávio, depois chamado Augusto, governou Roma.",
    "sections": [
        {"key": "origem", "title": "Origem", "sources": ["F1"], "blocks": [{"type": "paragraph", "text": "Nasceu."}]},
        {"key": "governo", "title": "Governo", "sources": ["F1"], "blocks": [{"type": "paragraph", "text": "Reinou."}]},
    ],
    "points_to_check": ["Idade no discurso.", "Data do nascimento."],
}


class FakeDrafter:
    model = "gemini-flash-latest"

    def __init__(self, payload: object = ANSWER) -> None:
        self.payload = payload
        self.requests: list[DraftRequest] = []

    def draft(self, request: DraftRequest) -> ModelReply:
        self.requests.append(request)
        return ModelReply(self.payload, "gemini-3.8-flash", {"totalTokenCount": 1622})


@pytest.fixture
def session_factory(db_session: Session) -> Callable[[], Session]:
    connection = db_session.connection()
    return lambda: Session(bind=connection, join_transaction_mode="create_savepoint")


@pytest.fixture
def augusto(db_session: Session) -> EntityRow:
    """Rascunho como sai do importador: resumo curto do Wikidata, citação do Wikidata e uma fonte aberta."""
    entity = add_entity(db_session, "augusto", status=PublicationStatus.DRAFT, with_source=False)
    entity.summary = "primeiro imperador romano (27 a.C.-14 d.C.)"
    cite(db_session, entity.id, WIKIDATA_SOURCE_ID)
    suetonio = add_source(db_session, "Suetônio, Vida de Augusto")
    suetonio.text_license = TextLicense.PUBLIC_DOMAIN
    cite(db_session, entity.id, suetonio.id).excerpt = "Augustus was born at Rome."
    db_session.flush()
    return entity


def generate(session_factory: Callable[[], Session], drafter: FakeDrafter, **options: Any) -> str:
    return generate_text(
        "augusto",
        session_factory=session_factory,
        drafter=drafter,
        fetch_text=lambda url: pytest.fail("nenhuma fonte deveria ser baixada"),
        **options,
    )


def runs(session: Session, entity: EntityRow) -> list[EntityDraftRunRow]:
    return list(session.scalars(select(EntityDraftRunRow).where(EntityDraftRunRow.entity_id == entity.id)))


def sections(session: Session, entity: EntityRow) -> list[EntitySectionRow]:
    query = select(EntitySectionRow).where(EntitySectionRow.entity_id == entity.id).order_by(EntitySectionRow.position)
    return list(session.scalars(query))


def test_writes_the_draft_and_records_where_it_came_from(
    db_session: Session, session_factory: Callable[[], Session], augusto: EntityRow
) -> None:
    drafter = FakeDrafter()

    message = generate(session_factory, drafter)

    db_session.refresh(augusto)
    [request] = drafter.requests
    assert [(s.label, s.title, s.text) for s in request.sources] == [
        ("F1", "Suetônio, Vida de Augusto", "Augustus was born at Rome.")
    ]
    assert message == "2 seção(ões), 2 ponto(s) a conferir"
    assert (augusto.summary, augusto.description) == (ANSWER["summary"], ANSWER["description"])
    written = sections(db_session, augusto)
    assert [(s.section_key, s.title, s.position) for s in written] == [
        ("origem", "Origem", 1),
        ("governo", "Governo", 2),
    ]
    assert to_blocks(written[0].content)
    [origin] = runs(db_session, augusto)
    assert (origin.kind, origin.model) == (DraftRunKind.TEXT, "gemini-3.8-flash")
    assert origin.points_to_check == "Idade no discurso.\nData do nascimento."
    assert origin.details["previous_summary"] == "primeiro imperador romano (27 a.C.-14 d.C.)"
    assert origin.details["skipped_sources"] == ["Wikidata: sem licença do texto"]
    [source] = origin.details["sources"]
    assert (source["origin"], source["chars"], len(source["sha256"])) == ("excerpt", 26, 64)
    assert origin.details["sections"]["origem"] == [source["source_id"]]


def test_refuses_to_overwrite_existing_text_unless_asked(
    db_session: Session, session_factory: Callable[[], Session], augusto: EntityRow
) -> None:
    add_section(db_session, augusto.id, "escrita-pelo-revisor", 1, {"blocks": []})

    with pytest.raises(TextRefusedError, match="use --substituir"):
        generate(session_factory, FakeDrafter())
    generate(session_factory, FakeDrafter(), replace=True)

    assert [s.section_key for s in sections(db_session, augusto)] == ["origem", "governo"]


def test_never_writes_text_on_an_entity_that_left_draft(
    db_session: Session, session_factory: Callable[[], Session], augusto: EntityRow
) -> None:
    augusto.status = PublicationStatus.PUBLISHED
    db_session.flush()
    drafter = FakeDrafter()

    with pytest.raises(TextRefusedError, match="não está em rascunho"):
        generate(session_factory, drafter, replace=True)
    assert drafter.requests == []


@pytest.mark.parametrize(
    ("excerpt", "payload", "error", "message"),
    [
        (None, ANSWER, SourceTextError, "nenhuma fonte com texto utilizável"),
        ("texto", {**ANSWER, "sections": [{**ANSWER["sections"][0], "sources": ["F9"]}]}, InvalidDraftError, "F9"),
    ],
)
def test_failures_before_writing_leave_the_entity_untouched(
    db_session: Session,
    session_factory: Callable[[], Session],
    augusto: EntityRow,
    excerpt: str | None,
    payload: object,
    error: type[Exception],
    message: str,
) -> None:
    for citation in augusto_citations(db_session, augusto):
        citation.excerpt = excerpt
    db_session.flush()

    with pytest.raises(error, match=message):
        generate(session_factory, FakeDrafter(payload))

    db_session.refresh(augusto)
    assert augusto.description is None
    assert sections(db_session, augusto) == [] and runs(db_session, augusto) == []


def augusto_citations(session: Session, entity: EntityRow) -> list[EntitySourceRow]:
    return list(session.scalars(select(EntitySourceRow).where(EntitySourceRow.entity_id == entity.id)))


def test_unknown_entity_is_refused(session_factory: Callable[[], Session]) -> None:
    with pytest.raises(TextRefusedError, match="entidade não encontrada"):
        generate_text("nao-existe", session_factory=session_factory, drafter=FakeDrafter(), fetch_text=str)


def test_command_requires_the_api_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ORTELIUS_GEMINI_API_KEY", "")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as exit_info:
            main(["augusto"])
    finally:
        get_settings.cache_clear()
    assert exit_info.value.code == 2
    assert "ORTELIUS_GEMINI_API_KEY" in capsys.readouterr().err
