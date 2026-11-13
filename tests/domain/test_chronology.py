import pytest

from app.domain.chronology import (
    Era,
    InvalidHistoricalYearError,
    YearRange,
    from_astronomical_year,
    to_astronomical_year,
)


@pytest.mark.parametrize(
    ("year", "era", "astronomical"),
    [
        (1, Era.CE, 1),
        (1, Era.BCE, 0),
        (2, Era.BCE, -1),
        (44, Era.BCE, -43),
        (500, Era.BCE, -499),
        (117, Era.CE, 117),
        (1453, Era.CE, 1453),
    ],
)
def test_converts_historical_year_to_astronomical(year: int, era: Era, astronomical: int) -> None:
    assert to_astronomical_year(year, era) == astronomical
    assert from_astronomical_year(astronomical) == (year, era)


def test_one_bce_and_one_ce_are_consecutive() -> None:
    assert to_astronomical_year(1, Era.CE) - to_astronomical_year(1, Era.BCE) == 1


@pytest.mark.parametrize("era", list(Era))
def test_rejects_year_zero_and_negative_historical_years(era: Era) -> None:
    with pytest.raises(InvalidHistoricalYearError):
        to_astronomical_year(0, era)
    with pytest.raises(InvalidHistoricalYearError):
        to_astronomical_year(-5, era)


def test_roundtrip_across_era_boundary() -> None:
    for astronomical in range(-3000, 2101):
        year, era = from_astronomical_year(astronomical)
        assert to_astronomical_year(year, era) == astronomical


def test_rejects_range_ending_before_start() -> None:
    with pytest.raises(InvalidHistoricalYearError):
        YearRange(start_year=117, end_year=-43)


def test_range_spanning_era_boundary_contains_both_sides() -> None:
    augustus_reign = YearRange(start_year=to_astronomical_year(27, Era.BCE), end_year=to_astronomical_year(14, Era.CE))
    assert augustus_reign.contains(to_astronomical_year(1, Era.BCE))
    assert augustus_reign.contains(to_astronomical_year(1, Era.CE))
    assert not augustus_reign.contains(to_astronomical_year(28, Era.BCE))
    assert not augustus_reign.contains(to_astronomical_year(15, Era.CE))


def test_open_ended_ranges() -> None:
    assert YearRange(start_year=None, end_year=-43).contains(-10_000)
    assert YearRange(start_year=1453, end_year=None).contains(2026)
    assert not YearRange(start_year=1453, end_year=None).contains(1452)
