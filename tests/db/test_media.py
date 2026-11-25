"""Imagens com procedência obrigatória e capa da entidade (migration 0009)."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.entities import ImageLicense
from app.infrastructure.database.models import EntityRow
from tests.factories import add_entity, add_media


@pytest.mark.parametrize("license_", list(ImageLicense))
def test_accepts_every_allowed_license(db_session: Session, license_: ImageLicense) -> None:
    assert add_media(db_session, license=license_).license == license_


@pytest.mark.parametrize("license_", ["cc_by_nc", "cc_by_nd", "all_rights_reserved", "outra"])
def test_rejects_licenses_outside_the_list(db_session: Session, license_: str) -> None:
    with pytest.raises(IntegrityError, match="ck_media_license"):
        add_media(db_session, license=license_)


@pytest.mark.parametrize("field", ["alt_text", "caption", "author", "license", "source_url", "file_id"])
def test_credit_fields_are_required(db_session: Session, field: str) -> None:
    with pytest.raises(IntegrityError, match="not-null"):
        add_media(db_session, **{field: None})


@pytest.mark.parametrize("field", ["alt_text", "caption", "author", "file_id"])
def test_rejects_blank_credit_text(db_session: Session, field: str) -> None:
    with pytest.raises(IntegrityError, match="ck_media_texts_not_blank"):
        add_media(db_session, **{field: "   "})


@pytest.mark.parametrize("url", ["commons.wikimedia.org/wiki/File:x.jpg", "javascript:alert(1)", "ftp://x/y.jpg"])
def test_rejects_source_that_is_not_a_web_link(db_session: Session, url: str) -> None:
    with pytest.raises(IntegrityError, match="ck_media_source_url_format"):
        add_media(db_session, source_url=url)


def test_one_image_can_be_the_cover_of_several_entities(db_session: Session) -> None:
    media = add_media(db_session)
    roma, augusto = add_entity(db_session, "roma"), add_entity(db_session, "augusto")

    roma.cover_id = augusto.cover_id = media.id
    db_session.flush()

    assert db_session.scalar(text("SELECT count(*) FROM entities WHERE cover_id = :id"), {"id": media.id}) == 2


def test_deleting_the_image_leaves_the_entity_without_cover(db_session: Session) -> None:
    media = add_media(db_session)
    entity = add_entity(db_session, "roma")
    entity.cover_id = media.id
    db_session.flush()

    db_session.delete(media)
    db_session.flush()
    db_session.expire_all()

    reloaded = db_session.get(EntityRow, entity.id)
    assert reloaded is not None and reloaded.cover_id is None
