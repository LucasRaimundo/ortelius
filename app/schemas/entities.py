from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities import EntityType, SourceType


class ContentBlock(BaseModel):
    """Bloco de conteúdo de uma seção. O formato de cada `type` é definido na fase editorial."""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)


class PointLocation(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: tuple[float, float] = Field(description="[longitude, latitude] em WGS 84 (EPSG:4326).")


class Alias(BaseModel):
    alias: str
    language_code: str | None
    alias_type: str | None
    start_year: int | None = Field(description="Ano astronômico (1 a.C. = 0).")
    end_year: int | None = Field(description="Ano astronômico (1 a.C. = 0).")


class Section(BaseModel):
    section_key: str
    title: str
    position: int
    content: list[ContentBlock]


class RelatedEntity(BaseModel):
    slug: str
    name: str
    entity_type: EntityType
    summary: str | None


class Relation(BaseModel):
    direction: Literal["outgoing", "incoming", "mutual"] = Field(
        description="`outgoing`: esta entidade é a origem; `incoming`: é o destino; `mutual`: relação sem direção."
    )
    relation_type: str
    label: str | None = Field(
        description="Rótulo já orientado a partir desta entidade. Nulo quando o tipo não tem rótulo inverso."
    )
    description: str | None
    start_year: int | None = Field(description="Ano astronômico (1 a.C. = 0).")
    end_year: int | None = Field(description="Ano astronômico (1 a.C. = 0).")
    entity: RelatedEntity


class Source(BaseModel):
    id: str
    title: str
    source_type: SourceType
    author_or_institution: str | None
    publication_year: int | None = Field(description="Ano astronômico (1 a.C. = 0).")
    publisher_or_journal: str | None
    identifier: str | None
    url: str | None
    contribution_type: str
    locator: str | None


class EntityDetail(BaseModel):
    id: str
    slug: str
    name: str
    entity_type: EntityType
    summary: str | None
    description: str | None
    cover_media: str | None
    location: PointLocation | None
    aliases: list[Alias]
    sections: list[Section]
    relations: list[Relation]
    sources: list[Source]
