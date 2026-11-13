import logging

import pytest
from fastapi.testclient import TestClient
from geoalchemy2 import WKTElement
from sqlalchemy.orm import Session

from app.domain.entities import EntityType, PublicationStatus
from app.infrastructure.database.models import EntityAliasRow
from tests.factories import add_entity, add_section, relate


def test_returns_published_entity_page(client: TestClient, db_session: Session) -> None:
    roma = add_entity(db_session, "roma")
    roma.location = WKTElement("POINT(12.4964 41.9028)", srid=4326)
    db_session.add(EntityAliasRow(entity_id=roma.id, alias="Roma Aeterna", language_code="la"))
    add_section(db_session, roma.id, "historia", 2, {"blocks": [{"type": "paragraph", "text": "Fundada..."}]})
    add_section(db_session, roma.id, "visao_geral", 1, {"blocks": []})
    db_session.flush()

    response = client.get("/v1/entities/roma")

    assert response.status_code == 200
    body = response.json()
    assert body["location"] == {"type": "Point", "coordinates": [12.4964, 41.9028]}
    assert [s["section_key"] for s in body["sections"]] == ["visao_geral", "historia"]
    assert body["sections"][1]["content"] == [{"type": "paragraph", "text": "Fundada..."}]
    assert [a["alias"] for a in body["aliases"]] == ["Roma Aeterna"]
    assert [s["title"] for s in body["sources"]] == ["Fonte de teste"]


def test_entity_without_location_has_null_location(client: TestClient, db_session: Session) -> None:
    add_entity(db_session, "eneida", entity_type=EntityType.WORK)

    assert client.get("/v1/entities/eneida").json()["location"] is None


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
    add_section(db_session, roma.id, "visao_geral", 1, {"blocks": [{"type": "paragraph"}]})
    add_section(db_session, roma.id, "quebrada", 2, {"blocks": [{"text": "bloco sem type"}]})

    with caplog.at_level(logging.WARNING, logger="app.api.v1.entities"):
        response = client.get("/v1/entities/roma")

    assert response.status_code == 200
    assert [s["section_key"] for s in response.json()["sections"]] == ["visao_geral"]
    assert [getattr(r, "section_key", None) for r in caplog.records] == ["quebrada"]


def test_database_unavailable_returns_503(client_without_database: TestClient) -> None:
    response = client_without_database.get("/v1/entities/roma")

    assert response.status_code == 503
    assert response.json() == {"detail": "serviço temporariamente indisponível"}
