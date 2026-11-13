"""Blocos de conteúdo das seções, independentes do editor que os produziu."""

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class TextRun:
    text: str
    bold: bool = False
    italic: bool = False
    href: str | None = None


type RichText = tuple[TextRun, ...]


@dataclass(frozen=True)
class Heading:
    level: Literal[3, 4]
    text: RichText


@dataclass(frozen=True)
class Paragraph:
    text: RichText


@dataclass(frozen=True)
class ListItem:
    text: RichText
    items: tuple["ListItem", ...] = field(default_factory=tuple["ListItem", ...])


@dataclass(frozen=True)
class BulletList:
    style: Literal["ordered", "unordered"]
    items: tuple[ListItem, ...]


@dataclass(frozen=True)
class Quote:
    text: RichText
    caption: str | None


type Block = Heading | Paragraph | BulletList | Quote


class InvalidContentError(ValueError):
    pass
