from enum import StrEnum
from uuid import UUID


class EntityType(StrEnum):
    PLACE = "place"
    CIVILIZATION = "civilization"
    STATE = "state"
    PERSON = "person"
    EVENT = "event"
    CULTURE = "culture"
    WORK = "work"
    CONCEPT = "concept"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class SourceType(StrEnum):
    BOOK = "book"
    ARTICLE = "article"
    PRIMARY_SOURCE = "primary_source"
    MAP = "map"
    ARCHIVE = "archive"
    DATASET = "dataset"
    JOURNALISM = "journalism"


class TextLicense(StrEnum):
    """Licenças que permitem enviar o texto de uma fonte ao modelo que gera rascunhos."""

    PUBLIC_DOMAIN = "public_domain"
    CC0 = "cc0"
    CC_BY = "cc_by"


class ImageLicense(StrEnum):
    """Licenças aceitas para imagens exibidas no Atlas: permitem uso comercial futuro e exigem no máximo atribuição."""

    PUBLIC_DOMAIN = "public_domain"
    CC0 = "cc0"
    CC_BY = "cc_by"
    CC_BY_SA = "cc_by_sa"


class DraftRunKind(StrEnum):
    WIKIDATA = "wikidata"
    TEXT = "text"


WIKIDATA_SOURCE_ID = UUID("6e2ae8ef-3adb-4c09-86f4-1974f17323ea")
