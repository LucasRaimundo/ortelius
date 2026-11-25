"""Migração das capas antigas para imagens com procedência (cms/migrar_capas.py)."""

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities import ImageLicense
from app.infrastructure.database.models import EntityRow, MediaRow
from cms.migrar_capas import Credit, Migration, Skipped, apply, parse_credit, plan
from tests.factories import add_directus_file, add_entity

COMMONS = "https://commons.wikimedia.org/wiki/File:"


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        (
            f"Autor: Till Niermann. Licença: Public domain. Fonte: {COMMONS}Statue-Augustus.jpg",
            Credit("Till Niermann", ImageLicense.PUBLIC_DOMAIN, None, f"{COMMONS}Statue-Augustus.jpg"),
        ),
        (
            "Autor: Myrabella. Licença: CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0). "
            f"Fonte: {COMMONS}Alise.jpg",
            Credit("Myrabella", ImageLicense.CC_BY_SA, "4.0", f"{COMMONS}Alise.jpg"),
        ),
        (
            "Autor: CNG. Licença: CC BY-SA 2.5 (https://creativecommons.org/licenses/by-sa/2.5). "
            f"Fonte: {COMMONS}Mark_Antony_%26_Cleopatra,_denarius,_34_BC,_543-1.jpg",
            Credit(
                "CNG", ImageLicense.CC_BY_SA, "2.5", f"{COMMONS}Mark_Antony_%26_Cleopatra,_denarius,_34_BC,_543-1.jpg"
            ),
        ),
        (
            f"Autor: J. C. Rolfe. Licença: CC BY 3.0. Fonte: {COMMONS}x.jpg",
            Credit("J. C. Rolfe", ImageLicense.CC_BY, "3.0", f"{COMMONS}x.jpg"),
        ),
        (
            f"Autor: Fulano. Licença: CC0 1.0. Fonte: {COMMONS}y.jpg",
            Credit("Fulano", ImageLicense.CC0, None, f"{COMMONS}y.jpg"),
        ),
    ],
)
def test_reads_the_credit_written_before_migration_0009(description: str, expected: Credit) -> None:
    assert parse_credit(description) == expected


@pytest.mark.parametrize(
    "description",
    [
        None,
        "",
        "Foto do Google Imagens",
        f"Autor: Getty. Licença: Direitos reservados. Fonte: {COMMONS}z.jpg",
        f"Autor: Fulano. Licença: CC BY-NC 4.0. Fonte: {COMMONS}z.jpg",
        "Autor: Fulano. Licença: CC BY 4.0. Fonte: commons/z.jpg",
    ],
    ids=["nulo", "vazio", "texto-livre", "direitos-reservados", "nao-comercial", "fonte-sem-link"],
)
def test_refuses_missing_or_unacceptable_credit(description: str | None) -> None:
    assert parse_credit(description) is None


def legacy_cover(session: Session, slug: str, *, title: str | None, description: str | None) -> str:
    file_id = add_directus_file(session, f"{slug}.jpg", title=title, description=description)
    entity = add_entity(session, slug)
    entity.cover_media = file_id
    session.flush()
    return file_id


AUGUSTO_CREDIT = f"Autor: Till Niermann. Licença: Public domain. Fonte: {COMMONS}Statue-Augustus.jpg"


def test_plans_covers_with_credit_and_lists_the_rest(db_session: Session) -> None:
    legacy_cover(db_session, "augusto", title="Augusto de Prima Porta", description=AUGUSTO_CREDIT)
    legacy_cover(db_session, "roma", title="Roma", description=None)

    items = plan(db_session, {"augusto": "Estátua de mármore de um homem de armadura com o braço erguido"})

    assert [type(item) for item in items] == [Migration, Skipped]
    augusto, roma = items
    assert isinstance(augusto, Migration) and isinstance(roma, Skipped)
    assert augusto.caption == "Augusto de Prima Porta"
    assert augusto.alt_text == "Estátua de mármore de um homem de armadura com o braço erguido"
    assert not augusto.alt_text_from_caption
    assert roma.reason.startswith("sem crédito")


def test_alt_text_falls_back_to_the_caption_and_says_so(db_session: Session) -> None:
    legacy_cover(db_session, "augusto", title="Augusto de Prima Porta", description=AUGUSTO_CREDIT)

    (item,) = plan(db_session, {})

    assert isinstance(item, Migration)
    assert item.alt_text == "Augusto de Prima Porta" and item.alt_text_from_caption


def test_applies_without_touching_the_entity_update_date(db_session: Session) -> None:
    file_id = legacy_cover(db_session, "augusto", title="Augusto de Prima Porta", description=AUGUSTO_CREDIT)
    # Data antiga gravada com o trigger desligado; com ele ligado, o próprio UPDATE gravaria now().
    db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    db_session.execute(text("ALTER TABLE entities DISABLE TRIGGER trg_entities_updated_at"))
    db_session.execute(text("UPDATE entities SET updated_at = '2026-01-01' WHERE slug = 'augusto'"))
    db_session.execute(text("ALTER TABLE entities ENABLE TRIGGER trg_entities_updated_at"))
    migrations = [item for item in plan(db_session, {}) if isinstance(item, Migration)]

    apply(db_session, migrations)
    db_session.expire_all()

    entity = db_session.query(EntityRow).filter_by(slug="augusto").one()
    media = db_session.get(MediaRow, entity.cover_id)
    assert media is not None
    assert (media.file_id, media.author, media.license) == (file_id, "Till Niermann", ImageLicense.PUBLIC_DOMAIN)
    assert entity.cover_media == file_id
    assert entity.updated_at.year == 2026 and entity.updated_at.month == 1


def test_same_file_on_two_entities_becomes_one_image(db_session: Session) -> None:
    file_id = legacy_cover(db_session, "augusto", title="Augusto de Prima Porta", description=AUGUSTO_CREDIT)
    otaviano = add_entity(db_session, "otaviano")
    otaviano.cover_media = file_id
    db_session.flush()

    apply(db_session, [item for item in plan(db_session, {}) if isinstance(item, Migration)])

    assert db_session.scalar(text("SELECT count(*) FROM media")) == 1
    assert db_session.scalar(text("SELECT count(DISTINCT cover_id) FROM entities WHERE cover_id IS NOT NULL")) == 1


def test_running_again_finds_nothing_to_migrate(db_session: Session) -> None:
    legacy_cover(db_session, "augusto", title="Augusto de Prima Porta", description=AUGUSTO_CREDIT)
    apply(db_session, [item for item in plan(db_session, {}) if isinstance(item, Migration)])

    assert plan(db_session, {}) == []
