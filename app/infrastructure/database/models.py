import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from geoalchemy2 import Geometry, WKBElement, WKTElement
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql.elements import conv

from app.domain.entities import DraftRunKind, EntityType, ImageLicense, PublicationStatus, SourceType, TextLicense

NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


def _in_list(column: str, values: type[StrEnum]) -> str:
    allowed = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({allowed})"


SLUG_PATTERN = "^[a-z0-9]+(-[a-z0-9]+)*$"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class _Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


class MediaRow(_Timestamps, Base):
    """Imagem usada pelo Atlas, com procedência obrigatória. O arquivo mora no Directus (`file_id`, sem FK)."""

    __tablename__ = "media"
    __table_args__ = (
        CheckConstraint(_in_list("license", ImageLicense), name="license"),
        CheckConstraint("source_url ~ '^https?://'", name="source_url_format"),
        CheckConstraint(
            "CASE WHEN license IN ('cc_by', 'cc_by_sa') "
            "THEN license_version IS NOT NULL AND license_version IN ('1.0', '2.0', '2.5', '3.0', '4.0') "
            "ELSE license_version IS NULL END",
            name="license_version",
        ),
        CheckConstraint(
            "btrim(alt_text) <> '' AND btrim(caption) <> '' AND btrim(author) <> '' AND btrim(file_id) <> ''",
            name="texts_not_blank",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    file_id: Mapped[str] = mapped_column(Text)
    alt_text: Mapped[str] = mapped_column(String(300))
    caption: Mapped[str] = mapped_column(String(300))
    author: Mapped[str] = mapped_column(String(200))
    license: Mapped[str] = mapped_column(String(24))
    license_version: Mapped[str | None] = mapped_column(String(8))
    source_url: Mapped[str] = mapped_column(Text)


class EntityRow(_Timestamps, Base):
    __tablename__ = "entities"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG_PATTERN}'", name="slug_format"),
        CheckConstraint(_in_list("entity_type", EntityType), name="entity_type"),
        CheckConstraint(_in_list("status", PublicationStatus), name="status"),
        CheckConstraint("ST_IsValid(location)", name="location_valid"),
        CheckConstraint("wikidata_id ~ '^Q[1-9][0-9]*$'", name="wikidata_id_format"),
        CheckConstraint("latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint("longitude BETWEEN -180 AND 180", name="longitude_range"),
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="coordinates_together"),
        Index("ix_entities_location", "location", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    slug: Mapped[str] = mapped_column(String(180), unique=True)
    name: Mapped[str] = mapped_column(String(240))
    entity_type: Mapped[str] = mapped_column(String(40), index=True)
    summary: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), server_default=PublicationStatus.DRAFT.value)
    # Substituída por `cover_id`; sai depois que as capas atuais forem migradas (migration 0009).
    cover_media: Mapped[str | None] = mapped_column(Text)
    cover_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("media.id", ondelete="SET NULL"), index=True)
    location: Mapped[WKBElement | WKTElement | None] = mapped_column(
        Geometry("POINT", srid=4326, spatial_index=False), nullable=True
    )
    wikidata_id: Mapped[str | None] = mapped_column(String(16), unique=True)
    # Espelho editável de `location`, sincronizado por trigger (migration 0008); a API lê só `location`.
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))


class EntityAliasRow(_Timestamps, Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (CheckConstraint("start_year <= end_year", name="year_range"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(240))
    language_code: Mapped[str | None] = mapped_column(String(12))
    alias_type: Mapped[str | None] = mapped_column(String(32))
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)


class EntitySectionRow(_Timestamps, Base):
    __tablename__ = "entity_sections"
    __table_args__ = (
        UniqueConstraint("entity_id", "section_key"),
        UniqueConstraint("entity_id", "position", deferrable=True, initially="DEFERRED"),
        CheckConstraint(f"section_key ~ '{SLUG_PATTERN}'", name="section_key_format"),
        CheckConstraint(
            "jsonb_typeof(content) = 'object' AND COALESCE(jsonb_typeof(content -> 'blocks'), '') = 'array'",
            name="content_is_document",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    section_key: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(180))
    position: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("""'{"blocks": []}'::jsonb"""))


class RelationTypeRow(_Timestamps, Base):
    __tablename__ = "relation_types"
    __table_args__ = (CheckConstraint(f"key ~ '{SLUG_PATTERN}'", name="key_format"),)

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    label: Mapped[str] = mapped_column(String(120))
    inverse_label: Mapped[str | None] = mapped_column(String(120))


class EntityRelationRow(_Timestamps, Base):
    __tablename__ = "entity_relations"
    __table_args__ = (
        UniqueConstraint(
            "source_entity_id",
            "target_entity_id",
            "relation_type",
            name=conv("uq_entity_relations_source_target_type"),
        ),
        CheckConstraint("source_entity_id <> target_entity_id", name="not_self"),
        CheckConstraint("start_year <= end_year", name="year_range"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    source_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"))
    target_entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    relation_type: Mapped[str] = mapped_column(
        ForeignKey("relation_types.key", onupdate="CASCADE", ondelete="RESTRICT")
    )
    description: Mapped[str | None] = mapped_column(Text)
    is_directional: Mapped[bool] = mapped_column(server_default=text("true"))
    start_year: Mapped[int | None] = mapped_column(Integer)
    end_year: Mapped[int | None] = mapped_column(Integer)
    position: Mapped[int | None] = mapped_column(Integer)


class SourceRow(_Timestamps, Base):
    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(_in_list("source_type", SourceType), name="source_type"),
        CheckConstraint("text_url ~ '^https?://'", name="text_url_format"),
        CheckConstraint(_in_list("text_license", TextLicense), name="text_license"),
        CheckConstraint("text_url IS NULL OR text_license IS NOT NULL", name="text_url_requires_license"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    title: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(48))
    author_or_institution: Mapped[str | None] = mapped_column(String(300))
    publication_year: Mapped[int | None] = mapped_column(Integer)
    publisher_or_journal: Mapped[str | None] = mapped_column(String(240))
    identifier: Mapped[str | None] = mapped_column(String(180))
    url: Mapped[str | None] = mapped_column(Text)
    language_code: Mapped[str | None] = mapped_column(String(12))
    source_nature: Mapped[str | None] = mapped_column(String(32))
    reliability_note: Mapped[str | None] = mapped_column(Text)
    license: Mapped[str | None] = mapped_column(String(180))
    text_url: Mapped[str | None] = mapped_column(Text)
    text_license: Mapped[str | None] = mapped_column(String(24))


class EntitySourceRow(_Timestamps, Base):
    __tablename__ = "entity_sources"

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sources.id", ondelete="RESTRICT"), index=True)
    contribution_type: Mapped[str] = mapped_column(String(48), server_default="fundamenta")
    locator: Mapped[str | None] = mapped_column(String(500))
    notes: Mapped[str | None] = mapped_column(Text)
    excerpt: Mapped[str | None] = mapped_column(Text)


class EntityDraftRunRow(Base):
    __tablename__ = "entity_draft_runs"
    __table_args__ = (
        CheckConstraint(_in_list("kind", DraftRunKind), name="kind"),
        CheckConstraint(f"(kind = '{DraftRunKind.TEXT}') = (model IS NOT NULL)", name="model_only_for_text"),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="details_is_object"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    model: Mapped[str | None] = mapped_column(String(120))
    points_to_check: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Tabelas que existem no banco mas não pertencem ao domínio (criadas por extensões).
EXTERNAL_TABLES = frozenset({"spatial_ref_sys"})


def is_domain_object(name: str | None, type_: str, _parent_names: object) -> bool:
    """Filtro `include_name` do Alembic: ignora objetos que as migrations não gerenciam."""
    return not (type_ == "table" and name in EXTERNAL_TABLES)
