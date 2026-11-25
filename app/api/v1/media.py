import logging
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.domain.media import RENDITION_WIDTHS, file_version
from app.infrastructure.database.engine import get_session
from app.infrastructure.images import UnusableImageError, render_webp
from app.infrastructure.media_storage import MediaStorage
from app.infrastructure.rendition_cache import RenditionCache
from app.repositories.media import find_published_cover_file
from app.schemas.errors import ErrorResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/media", tags=["media"])

# A URL publicada carrega a versão do arquivo (?v=): com ela, o conteúdo nunca muda e pode ficar em cache para
# sempre. Sem ela, ou com uma versão antiga, a resposta vale por pouco tempo.
IMMUTABLE = "public, max-age=31536000, immutable"
SHORT_LIVED = "public, max-age=300"


@dataclass(frozen=True)
class MediaFiles:
    storage: MediaStorage
    cache: RenditionCache


def get_media_files(request: Request) -> MediaFiles:
    media_files: MediaFiles | None = request.app.state.media_files
    if media_files is None:
        logger.error("ORTELIUS_MEDIA_ROOT não configurado; imagens indisponíveis")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="imagens temporariamente indisponíveis")
    return media_files


@router.get(
    "/{media_id}/{width}.webp",
    response_class=FileResponse,
    responses={
        status.HTTP_200_OK: {"content": {"image/webp": {}}},
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
    },
)
def get_rendition(
    media_id: uuid.UUID,
    width: int,
    session: Annotated[Session, Depends(get_session)],
    media_files: Annotated[MediaFiles, Depends(get_media_files)],
    v: str | None = None,
) -> FileResponse:
    not_found = HTTPException(status.HTTP_404_NOT_FOUND, detail="imagem não encontrada")
    if width not in RENDITION_WIDTHS:
        raise not_found
    cover = find_published_cover_file(session, media_id)
    if cover is None:
        raise not_found

    version = file_version(cover.file_id, cover.modified_at)
    path = media_files.cache.path(version, width)
    if not path.is_file():
        original = media_files.storage.read(cover.filename)
        if original is None:
            logger.error("original da imagem ausente", extra={"media_id": str(media_id), "file_id": cover.file_id})
            raise not_found
        try:
            media_files.cache.store(path, render_webp(original, width))
        except UnusableImageError as error:
            logger.error(
                "original da imagem inutilizável",
                extra={"media_id": str(media_id), "file_id": cover.file_id, "reason": str(error)},
            )
            raise not_found from error

    cache_control = IMMUTABLE if v == version else SHORT_LIVED
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": cache_control})
