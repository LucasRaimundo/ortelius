import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.domain.entities import PublicationStatus
from app.infrastructure.database.models import MediaRow


@dataclass(frozen=True)
class CoverFile:
    file_id: str
    filename: str
    modified_at: datetime


# Lê directus_files só para localizar o original: o domínio guarda o id do arquivo, o Directus guarda o nome em
# disco. Uma imagem só é servida se for capa de ao menos uma entidade publicada; rascunhos e arquivos soltos não.
_PUBLISHED_COVER = text(f"""
    SELECT m.file_id, f.filename_disk, COALESCE(f.modified_on, f.uploaded_on)
    FROM media m
    JOIN directus.directus_files f ON f.id::text = m.file_id
    WHERE m.id = :media_id
      AND EXISTS (
          SELECT 1 FROM entities e WHERE e.cover_id = m.id AND e.status = '{PublicationStatus.PUBLISHED.value}'
      )
""")


def find_published_cover_file(session: Session, media_id: uuid.UUID) -> CoverFile | None:
    row = session.execute(_PUBLISHED_COVER, {"media_id": media_id}).one_or_none()
    if row is None or row[1] is None or row[2] is None:
        return None
    return CoverFile(file_id=row[0], filename=row[1], modified_at=row[2])


@dataclass(frozen=True)
class CoverRecord:
    media: MediaRow
    width: int
    height: int
    modified_at: datetime


_FILE_DETAILS = text("""
    SELECT width, height, COALESCE(modified_on, uploaded_on) FROM directus.directus_files WHERE id::text = :file_id
""")


def find_cover(session: Session, media_id: uuid.UUID) -> CoverRecord | None:
    """Capa com as dimensões do original; None se o arquivo sumiu do Directus ou não tem dimensões."""
    media = session.get(MediaRow, media_id)
    if media is None:
        return None
    row = session.execute(_FILE_DETAILS, {"file_id": media.file_id}).one_or_none()
    if row is None or row[0] is None or row[1] is None or row[2] is None:
        return None
    return CoverRecord(media=media, width=row[0], height=row[1], modified_at=row[2])
