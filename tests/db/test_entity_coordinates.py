"""Latitude e longitude do painel sincronizadas com `location` (migration 0008)."""

from decimal import Decimal

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.factories import add_entity

ALEXANDRIA = (Decimal("31.200100"), Decimal("29.918700"))


def coordinates(session: Session, slug: str) -> tuple[object, ...]:
    """Latitude, longitude e o ponto (lat, lon) como o banco os guarda."""
    row = session.execute(
        text("SELECT latitude, longitude, ST_Y(location), ST_X(location) FROM entities WHERE slug = :slug"),
        {"slug": slug},
    ).one()
    return tuple(row)


def update(session: Session, slug: str, assignments: str, **values: object) -> None:
    session.execute(text(f"UPDATE entities SET {assignments} WHERE slug = :slug"), {"slug": slug, **values})


def place(session: Session, slug: str = "alexandria") -> str:
    add_entity(session, slug)
    return slug


def test_typed_coordinates_move_the_point(db_session: Session) -> None:
    slug = place(db_session)

    update(db_session, slug, "latitude = :lat, longitude = :lon", lat=ALEXANDRIA[0], lon=ALEXANDRIA[1])

    assert coordinates(db_session, slug) == (*ALEXANDRIA, 31.2001, 29.9187)


def test_clicking_the_map_fills_the_coordinates_rounded_to_six_places(db_session: Session) -> None:
    slug = place(db_session)

    update(db_session, slug, "location = ST_SetSRID(ST_MakePoint(29.91872345, 31.20012345), 4326)")

    latitude, longitude, *_ = coordinates(db_session, slug)
    assert (latitude, longitude) == (Decimal("31.200123"), Decimal("29.918723"))


def test_coordinates_win_when_both_change_in_the_same_save(db_session: Session) -> None:
    slug = place(db_session)

    update(
        db_session,
        slug,
        "location = ST_SetSRID(ST_MakePoint(0, 0), 4326), latitude = :lat, longitude = :lon",
        lat=ALEXANDRIA[0],
        lon=ALEXANDRIA[1],
    )

    assert coordinates(db_session, slug) == (*ALEXANDRIA, 31.2001, 29.9187)


def test_other_edits_keep_the_point_exactly_as_it_was(db_session: Session) -> None:
    slug = place(db_session)
    update(db_session, slug, "location = ST_SetSRID(ST_MakePoint(29.91872345, 31.20012345), 4326)")

    update(db_session, slug, "name = 'Alexandria do Egito'")

    *_, point_lat, point_lon = coordinates(db_session, slug)
    assert (point_lat, point_lon) == (31.20012345, 29.91872345)


def test_inserting_with_coordinates_creates_the_point(db_session: Session) -> None:
    db_session.execute(
        text(
            "INSERT INTO entities (slug, name, entity_type, latitude, longitude) "
            "VALUES ('mantua', 'Mântua', 'place', 45.1564, 10.7914)"
        )
    )

    assert coordinates(db_session, "mantua") == (Decimal("45.156400"), Decimal("10.791400"), 45.1564, 10.7914)


@pytest.mark.parametrize(
    "assignments",
    ["latitude = NULL, longitude = NULL", "location = NULL"],
    ids=["clearing-coordinates", "clearing-the-map"],
)
def test_clearing_either_side_clears_both(db_session: Session, assignments: str) -> None:
    slug = place(db_session)
    update(db_session, slug, "latitude = :lat, longitude = :lon", lat=ALEXANDRIA[0], lon=ALEXANDRIA[1])

    update(db_session, slug, assignments)

    assert coordinates(db_session, slug) == (None, None, None, None)


@pytest.mark.parametrize(
    ("latitude", "longitude", "constraint"),
    [
        (91, 0, "ck_entities_latitude_range"),
        (0, -181, "ck_entities_longitude_range"),
        (31.2, None, "ck_entities_coordinates_together"),
    ],
)
def test_rejects_impossible_or_half_filled_coordinates(
    db_session: Session, latitude: float, longitude: float | None, constraint: str
) -> None:
    slug = place(db_session)

    with pytest.raises(IntegrityError, match=constraint):
        update(db_session, slug, "latitude = :lat, longitude = :lon", lat=latitude, lon=longitude)


def test_migration_fills_coordinates_of_points_already_saved(db_engine: Engine) -> None:
    with db_engine.connect() as connection:
        transaction = connection.begin()
        config = Config("alembic.ini")
        config.attributes["connection"] = connection
        config.attributes["configure_logger"] = False
        try:
            command.downgrade(config, "0007")
            connection.execute(
                text(
                    "INSERT INTO entities (slug, name, entity_type, updated_at, location) "
                    "VALUES ('roma', 'Roma', 'place', '2026-01-01T00:00:00Z', "
                    "ST_SetSRID(ST_MakePoint(12.476648445898007, 41.89895263395056), 4326))"
                )
            )
            # Os triggers diferidos do INSERT precisam rodar antes do ALTER TABLE da migration.
            connection.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            command.upgrade(config, "head")

            row = connection.execute(
                text(
                    "SELECT latitude, longitude, ST_X(location), updated_at = '2026-01-01T00:00:00Z' "
                    "FROM entities WHERE slug = 'roma'"
                )
            ).one()
            # O ponto gravado não é arredondado (o trigger só passa a existir depois do preenchimento), e preencher o
            # espelho não conta como edição.
            assert tuple(row) == (Decimal("41.898953"), Decimal("12.476648"), 12.476648445898007, True)
        finally:
            transaction.rollback()
