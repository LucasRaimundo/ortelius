from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.domain.entities import EntityType, ImageLicense, SourceType


class TextRun(BaseModel):
    """Trecho de texto com formatação uniforme. O frontend monta o texto a partir dos trechos, nunca de HTML."""

    text: str
    bold: bool
    italic: bool
    href: str | None = Field(description="Link http(s), quando o trecho é um link.")


class HeadingBlock(BaseModel):
    type: Literal["heading"]
    level: Literal[3, 4] = Field(description="3 ou 4: o nível 1 é o nome da entidade e o 2, o título da seção.")
    text: list[TextRun]


class ParagraphBlock(BaseModel):
    type: Literal["paragraph"]
    text: list[TextRun]


class ListItem(BaseModel):
    text: list[TextRun]
    items: list["ListItem"] = Field(description="Subitens, para listas aninhadas.")


class ListBlock(BaseModel):
    type: Literal["list"]
    style: Literal["ordered", "unordered"]
    items: list[ListItem]


class QuoteBlock(BaseModel):
    type: Literal["quote"]
    text: list[TextRun]
    caption: str | None = Field(description="Autor ou origem da citação, em texto puro.")


ContentBlock = Annotated[HeadingBlock | ParagraphBlock | ListBlock | QuoteBlock, Field(discriminator="type")]


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


class License(BaseModel):
    code: ImageLicense
    label: str
    url: str | None


class Rendition(BaseModel):
    width: int
    path: str = Field(description="Caminho na API, com a versão do arquivo; o conteúdo de um caminho nunca muda.")


class Cover(BaseModel):
    id: str
    alt_text: str
    caption: str
    author: str
    license: License
    source_url: str
    width: int = Field(description="Largura do original; com `height`, dá a proporção da imagem.")
    height: int
    renditions: list[Rendition] = Field(description="Tamanhos disponíveis, do menor para o maior.")


class EntityDetail(BaseModel):
    id: str
    slug: str
    name: str
    entity_type: EntityType
    summary: str | None
    description: str | None
    cover_media: str | None = Field(
        deprecated=True, description="Substituído por `cover`; sai depois que o frontend migrar."
    )
    cover: Cover | None
    location: PointLocation | None
    start_year: int | None = Field(
        description="Ano astronômico (1 a.C. = 0): nascimento, começo do evento ou fundação."
    )
    end_year: int | None = Field(description="Ano astronômico (1 a.C. = 0): morte, fim do evento ou dissolução.")
    aliases: list[Alias]
    sections: list[Section]
    relations: list[Relation]
    sources: list[Source]
