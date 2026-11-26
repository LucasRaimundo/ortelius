import logging
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy.orm import Session

from app.domain.chronology import Era, to_astronomical_year
from app.domain.entities import EntityType, ImageLicense, PublicationStatus
from app.domain.media import file_version
from app.infrastructure.database.models import EntityAliasRow
from tests.factories import (
    add_directus_file,
    add_entity,
    add_media,
    add_section,
    ensure_directus_files_table,
    relate,
)


def test_returns_published_entity_page(client: TestClient, db_session: Session) -> None:
    roma = add_entity(db_session, "roma")
    roma.location = WKTElement("POINT(12.4964 41.9028)", srid=4326)
    db_session.add(EntityAliasRow(entity_id=roma.id, alias="Roma Aeterna", language_code="la"))
    paragraph = {"type": "paragraph", "data": {"text": "Fundada por <b>Rômulo</b>, segundo a lenda."}}
    add_section(db_session, roma.id, "historia", 2, {"time": 1, "version": "2.31.2", "blocks": [paragraph]})
    add_section(db_session, roma.id, "visao-geral", 1, {"blocks": []})
    db_session.flush()

    response = client.get("/v1/entities/roma")

    assert response.status_code == 200
    body = response.json()
    assert body["location"] == {"type": "Point", "coordinates": [12.4964, 41.9028]}
    assert [s["section_key"] for s in body["sections"]] == ["visao-geral", "historia"]
    assert body["sections"][1]["content"] == [
        {
            "type": "paragraph",
            "text": [
                {"text": "Fundada por ", "bold": False, "italic": False, "href": None},
                {"text": "Rômulo", "bold": True, "italic": False, "href": None},
                {"text": ", segundo a lenda.", "bold": False, "italic": False, "href": None},
            ],
        }
    ]
    assert [a["alias"] for a in body["aliases"]] == ["Roma Aeterna"]
    assert [s["title"] for s in body["sources"]] == ["Fonte de teste"]


def test_entity_without_location_has_null_location(client: TestClient, db_session: Session) -> None:
    add_entity(db_session, "eneida", entity_type=EntityType.WORK)

    assert client.get("/v1/entities/eneida").json()["location"] is None


def test_entity_dates_are_astronomical_years(client: TestClient, db_session: Session) -> None:
    virgilio = add_entity(db_session, "virgilio", entity_type=EntityType.PERSON)
    virgilio.start_year = to_astronomical_year(70, Era.BCE)
    virgilio.end_year = to_astronomical_year(19, Era.BCE)
    add_entity(db_session, "eneida", entity_type=EntityType.WORK)
    db_session.flush()

    body = client.get("/v1/entities/virgilio").json()
    assert (body["start_year"], body["end_year"]) == (-69, -18)
    undated = client.get("/v1/entities/eneida").json()
    assert (undated["start_year"], undated["end_year"]) == (None, None)


@pytest.mark.parametrize("status", [PublicationStatus.DRAFT, PublicationStatus.ARCHIVED])
def test_unpublished_entity_is_not_found(client: TestClient, db_session: Session, status: PublicationStatus) -> None:
    add_entity(db_session, "roma", status=status, with_source=False)

    response = client.get("/v1/entities/roma")

    assert response.status_code == 404
    assert response.json() == {"detail": "entidade não encontrada"}


def test_relations_are_visible_from_both_ends_with_oriented_labels(client: TestClient, db_session: Session) -> None:
    cesar = add_entity(db_session, "julio-cesar", entity_type=EntityType.PERSON)
    guerras = add_entity(db_session, "guerras-galicas", entity_type=EntityType.EVENT)
    pompeu = add_entity(db_session, "pompeu", entity_type=EntityType.PERSON)
    relate(db_session, cesar, guerras, label="participou de", inverse_label="teve a participação de")
    relate(db_session, cesar, pompeu, label="aliado de", is_directional=False)

    from_cesar = client.get("/v1/entities/julio-cesar").json()["relations"]
    from_guerras = client.get("/v1/entities/guerras-galicas").json()["relations"]
    from_pompeu = client.get("/v1/entities/pompeu").json()["relations"]

    assert [(r["direction"], r["label"], r["entity"]["slug"]) for r in from_cesar] == [
        ("outgoing", "participou de", "guerras-galicas"),
        ("mutual", "aliado de", "pompeu"),
    ]
    assert [(r["direction"], r["label"], r["entity"]["slug"]) for r in from_guerras] == [
        ("incoming", "teve a participação de", "julio-cesar"),
    ]
    assert [(r["direction"], r["label"], r["entity"]["slug"]) for r in from_pompeu] == [
        ("mutual", "aliado de", "julio-cesar"),
    ]


def test_relations_to_unpublished_entities_are_hidden(client: TestClient, db_session: Session) -> None:
    roma = add_entity(db_session, "roma")
    rascunho = add_entity(db_session, "cartago", status=PublicationStatus.DRAFT, with_source=False)
    relate(db_session, roma, rascunho, label="guerreou contra")

    assert client.get("/v1/entities/roma").json()["relations"] == []


def test_section_with_invalid_blocks_is_dropped_and_logged(
    client: TestClient, db_session: Session, caplog: pytest.LogCaptureFixture
) -> None:
    roma = add_entity(db_session, "roma")
    add_section(db_session, roma.id, "visao-geral", 1, {"blocks": [{"type": "paragraph", "data": {"text": "ok"}}]})
    image: dict[str, object] = {"type": "image", "data": {"file": {}}}
    add_section(db_session, roma.id, "quebrada", 2, {"blocks": [image]})

    with caplog.at_level(logging.WARNING, logger="app.api.v1.entities"):
        response = client.get("/v1/entities/roma")

    assert response.status_code == 200
    assert [s["section_key"] for s in response.json()["sections"]] == ["visao-geral"]
    assert [(getattr(r, "section_key", None), getattr(r, "reason", None)) for r in caplog.records] == [
        ("quebrada", "tipo de bloco não suportado: 'image'")
    ]


def test_database_unavailable_returns_503(client_without_database: TestClient) -> None:
    response = client_without_database.get("/v1/entities/roma")

    assert response.status_code == 503
    assert response.json() == {"detail": "serviço temporariamente indisponível"}


def test_section_content_uses_typed_blocks_and_never_exposes_html(client: TestClient, db_session: Session) -> None:
    roma = add_entity(db_session, "roma")
    document: dict[str, object] = {
        "blocks": [
            {"type": "header", "data": {"text": "Fundação", "level": 2}},
            {"type": "paragraph", "data": {"text": 'Ver <a href="javascript:alert(1)">isto</a><script>x()</script>.'}},
            {"type": "nestedlist", "data": {"style": "ordered", "items": [{"content": "Reino", "items": []}]}},
            {"type": "quote", "data": {"text": "Alea iacta est", "caption": "Suetônio"}},
        ]
    }
    add_section(db_session, roma.id, "historia", 1, document)

    content = client.get("/v1/entities/roma").json()["sections"][0]["content"]

    assert [block["type"] for block in content] == ["heading", "paragraph", "list", "quote"]
    assert content[0]["level"] == 3
    assert [run["text"] for run in content[1]["text"]] == ["Ver isto."]
    assert content[1]["text"][0]["href"] is None
    plain = {"bold": False, "italic": False, "href": None}
    assert content[2]["items"] == [{"text": [{"text": "Reino", **plain}], "items": []}]
    assert content[3]["caption"] == "Suetônio"


def test_entity_without_cover_has_null_cover(client: TestClient, db_session: Session) -> None:
    add_entity(db_session, "roma")

    body = client.get("/v1/entities/roma").json()

    assert body["cover"] is None
    assert "cover_media" not in body


def test_cover_carries_credit_license_and_sizes(client: TestClient, db_session: Session) -> None:
    modified = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    file_id = add_directus_file(db_session, "capa.jpg", modified_on=modified, width=2000, height=1000)
    media = add_media(db_session, file_id=file_id, license=ImageLicense.CC_BY_SA, license_version="4.0")
    roma = add_entity(db_session, "roma")
    roma.cover_id = media.id
    db_session.flush()

    cover = client.get("/v1/entities/roma").json()["cover"]

    version = file_version(file_id, modified)
    assert cover == {
        "id": str(media.id),
        "alt_text": media.alt_text,
        "caption": media.caption,
        "author": "David Bjorgen",
        "license": {
            "code": "cc_by_sa",
            "label": "CC BY-SA 4.0",
            "url": "https://creativecommons.org/licenses/by-sa/4.0/",
        },
        "source_url": media.source_url,
        "width": 2000,
        "height": 1000,
        "renditions": [
            {"width": width, "path": f"/v1/media/{media.id}/{width}.webp?v={version}"} for width in (480, 960, 1600)
        ],
    }


def test_small_cover_lists_only_sizes_up_to_the_original(client: TestClient, db_session: Session) -> None:
    file_id = add_directus_file(db_session, "capa.jpg", width=600, height=400)
    media = add_media(db_session, file_id=file_id)
    roma = add_entity(db_session, "roma")
    roma.cover_id = media.id
    db_session.flush()

    renditions = client.get("/v1/entities/roma").json()["cover"]["renditions"]

    assert [(r["width"], r["path"].split("?")[0].rsplit("/", 1)[1]) for r in renditions] == [
        (480, "480.webp"),
        (600, "960.webp"),
    ]


def test_cover_whose_file_left_the_panel_is_omitted(client: TestClient, db_session: Session) -> None:
    ensure_directus_files_table(db_session)
    media = add_media(db_session, file_id="00000000-0000-4000-8000-00000000dead")
    roma = add_entity(db_session, "roma")
    roma.cover_id = media.id
    db_session.flush()

    assert client.get("/v1/entities/roma").json()["cover"] is None
