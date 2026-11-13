"""Convenção cronológica do Atlas.

Anos são armazenados como ano astronômico: 1 d.C. = 1, 1 a.C. = 0, 2 a.C. = -1. Assim a ordenação e a
aritmética funcionam sem casos especiais, enquanto o calendário histórico não tem ano zero.
"""

from dataclasses import dataclass
from enum import StrEnum


class Era(StrEnum):
    BCE = "a.C."
    CE = "d.C."


class DatePrecision(StrEnum):
    EXACT = "exact"
    APPROXIMATE = "approximate"
    CENTURY = "century"
    RANGE = "range"
    UNKNOWN = "unknown"


class InvalidHistoricalYearError(ValueError):
    pass


def to_astronomical_year(year: int, era: Era) -> int:
    if year < 1:
        raise InvalidHistoricalYearError(f"ano histórico deve ser >= 1, recebido {year} {era}")
    return year if era is Era.CE else 1 - year


def from_astronomical_year(astronomical_year: int) -> tuple[int, Era]:
    if astronomical_year >= 1:
        return astronomical_year, Era.CE
    return 1 - astronomical_year, Era.BCE


@dataclass(frozen=True)
class YearRange:
    start_year: int | None
    end_year: int | None

    def __post_init__(self) -> None:
        if self.start_year is not None and self.end_year is not None and self.start_year > self.end_year:
            raise InvalidHistoricalYearError(f"início ({self.start_year}) depois do fim ({self.end_year})")

    def contains(self, astronomical_year: int) -> bool:
        after_start = self.start_year is None or astronomical_year >= self.start_year
        before_end = self.end_year is None or astronomical_year <= self.end_year
        return after_start and before_end
