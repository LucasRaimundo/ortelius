"""Datas de início e fim da entidade (migration 0010), em ano astronômico."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.chronology import Era, to_astronomical_year
from tests.factories import add_entity


def test_stores_a_life_before_christ(db_session: Session) -> None:
    virgilio = add_entity(db_session, "virgilio")

    virgilio.start_year = to_astronomical_year(70, Era.BCE)
    virgilio.end_year = to_astronomical_year(19, Era.BCE)
    db_session.flush()

    assert (virgilio.start_year, virgilio.end_year) == (-69, -18)


@pytest.mark.parametrize(("start", "end"), [(None, None), (-26, None), (None, 476), (-43, -43)])
def test_dates_are_optional_and_may_be_a_single_year(db_session: Session, start: int | None, end: int | None) -> None:
    entity = add_entity(db_session, "roma")

    entity.start_year, entity.end_year = start, end
    db_session.flush()


def test_rejects_an_end_before_the_start(db_session: Session) -> None:
    entity = add_entity(db_session, "augusto")

    entity.start_year, entity.end_year = 14, -62
    with pytest.raises(IntegrityError, match="ck_entities_year_range"):
        db_session.flush()
