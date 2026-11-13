from enum import StrEnum


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
