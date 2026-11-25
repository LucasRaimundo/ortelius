"""Imagens servidas pela API: só capas publicadas, em larguras fixas, como WebP (capa da entidade, issue 6)."""

import io
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr
from sqlalchemy.orm import Session

from app.domain.entities import PublicationStatus
from app.domain.media import file_version
from app.infrastructure.database.engine import get_session
from app.infrastructure.images import UnusableImageError, render_webp
from app.main import create_app
from app.settings import Settings
from tests.factories import add_directus_file, add_entity, add_media, ensure_directus_files_table

MODIFIED = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def image_bytes(width: int, height: int, *, fmt: str = "JPEG", exif: Image.Exif | None = None) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 40)).save(output, format=fmt, **({"exif": exif} if exif else {}))
    return output.getvalue()


def decoded(content: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(content))
    image.load()
    return image


@pytest.fixture
def uploads(tmp_path: Path) -> Path:
    path = tmp_path / "uploads"
    path.mkdir()
    return path


@pytest.fixture
def media_client(db_session: Session, tmp_path: Path, uploads: Path) -> Iterator[TestClient]:
    url = db_session.get_bind().engine.url.render_as_string(hide_password=False)
    settings = Settings(
        cors_allowed_origins=["http://localhost:3000"],
        database_url=SecretStr(url),
        media_root=uploads,
        media_cache_dir=tmp_path / "cache",
    )
    app = create_app(settings)
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as client:
        yield client


def published_cover(
    session: Session, uploads: Path, content: bytes, *, status: PublicationStatus = PublicationStatus.PUBLISHED
) -> tuple[str, str]:
    """Grava o original, registra no Directus e o torna capa de uma entidade. Devolve (media_id, versão)."""
    (uploads / "capa.jpg").write_bytes(content)
    file_id = add_directus_file(session, "capa.jpg", modified_on=MODIFIED)
    media = add_media(session, file_id=file_id)
    entity = add_entity(session, "roma", status=status)
    entity.cover_id = media.id
    session.flush()
    return str(media.id), file_version(file_id, MODIFIED)


def test_serves_published_cover_as_webp_at_the_requested_width(
    media_client: TestClient, db_session: Session, uploads: Path
) -> None:
    media_id, version = published_cover(db_session, uploads, image_bytes(2000, 1000))

    response = media_client.get(f"/v1/media/{media_id}/960.webp", params={"v": version})

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert decoded(response.content).size == (960, 480)


def test_never_enlarges_a_small_original(media_client: TestClient, db_session: Session, uploads: Path) -> None:
    media_id, _ = published_cover(db_session, uploads, image_bytes(600, 300))

    response = media_client.get(f"/v1/media/{media_id}/1600.webp")

    assert decoded(response.content).size == (600, 300)


def test_without_the_current_version_the_response_is_cached_only_briefly(
    media_client: TestClient, db_session: Session, uploads: Path
) -> None:
    media_id, _ = published_cover(db_session, uploads, image_bytes(800, 400))

    for params in ({}, {"v": "versaoantiga"}):
        response = media_client.get(f"/v1/media/{media_id}/480.webp", params=params)
        assert response.headers["cache-control"] == "public, max-age=300"


def test_keeps_generated_sizes_and_does_not_reread_the_original(
    media_client: TestClient, db_session: Session, uploads: Path
) -> None:
    media_id, _ = published_cover(db_session, uploads, image_bytes(800, 400))
    first = media_client.get(f"/v1/media/{media_id}/480.webp")

    (uploads / "capa.jpg").unlink()
    second = media_client.get(f"/v1/media/{media_id}/480.webp")

    assert second.status_code == 200
    assert second.content == first.content


def test_applies_the_camera_orientation_and_drops_metadata(
    media_client: TestClient, db_session: Session, uploads: Path
) -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: girar 90° para exibir
    exif[0x013B] = "Fotógrafo"  # Artist
    media_id, _ = published_cover(db_session, uploads, image_bytes(200, 100, exif=exif))

    image = decoded(media_client.get(f"/v1/media/{media_id}/480.webp").content)

    assert image.size == (100, 200)
    assert not image.getexif()


def test_does_not_serve_the_cover_of_a_draft(media_client: TestClient, db_session: Session, uploads: Path) -> None:
    media_id, _ = published_cover(db_session, uploads, image_bytes(800, 400), status=PublicationStatus.DRAFT)

    assert media_client.get(f"/v1/media/{media_id}/480.webp").status_code == 404


def test_does_not_serve_an_image_that_is_no_entity_cover(
    media_client: TestClient, db_session: Session, uploads: Path
) -> None:
    (uploads / "solta.jpg").write_bytes(image_bytes(800, 400))
    media = add_media(db_session, file_id=add_directus_file(db_session, "solta.jpg"))

    assert media_client.get(f"/v1/media/{media.id}/480.webp").status_code == 404


def test_unknown_image_is_not_found(media_client: TestClient, db_session: Session) -> None:
    ensure_directus_files_table(db_session)

    assert media_client.get("/v1/media/00000000-0000-4000-8000-000000000000/480.webp").status_code == 404


@pytest.mark.parametrize("width", [100, 500, 4000])
def test_refuses_widths_outside_the_list(
    media_client: TestClient, db_session: Session, uploads: Path, width: int
) -> None:
    media_id, _ = published_cover(db_session, uploads, image_bytes(800, 400))

    assert media_client.get(f"/v1/media/{media_id}/{width}.webp").status_code == 404


@pytest.mark.parametrize(
    "content",
    [image_bytes(80, 40, fmt="GIF"), b"isto nao e uma imagem", image_bytes(800, 400)[:200]],
    ids=["gif", "nao-imagem", "truncada"],
)
def test_unusable_original_is_not_found_and_logged(
    media_client: TestClient, db_session: Session, uploads: Path, content: bytes, caplog: pytest.LogCaptureFixture
) -> None:
    media_id, _ = published_cover(db_session, uploads, content)

    response = media_client.get(f"/v1/media/{media_id}/480.webp")

    assert response.status_code == 404
    assert "original da imagem inutilizável" in caplog.text


def test_missing_original_is_not_found_and_logged(
    media_client: TestClient, db_session: Session, uploads: Path, caplog: pytest.LogCaptureFixture
) -> None:
    media_id, _ = published_cover(db_session, uploads, image_bytes(800, 400))
    (uploads / "capa.jpg").unlink()

    assert media_client.get(f"/v1/media/{media_id}/480.webp").status_code == 404
    assert "original da imagem ausente" in caplog.text


def test_never_reads_outside_the_uploads_folder(
    media_client: TestClient, db_session: Session, uploads: Path, tmp_path: Path
) -> None:
    (tmp_path / "segredo.jpg").write_bytes(image_bytes(800, 400))
    media = add_media(db_session, file_id=add_directus_file(db_session, "../segredo.jpg"))
    entity = add_entity(db_session, "roma")
    entity.cover_id = media.id
    db_session.flush()

    assert media_client.get(f"/v1/media/{media.id}/480.webp").status_code == 404


def test_images_are_unavailable_when_storage_is_not_configured(client: TestClient) -> None:
    response = client.get("/v1/media/00000000-0000-4000-8000-000000000000/480.webp")

    assert response.status_code == 503


def test_refuses_images_above_the_pixel_limit() -> None:
    with pytest.raises(UnusableImageError, match="excede o limite"):
        render_webp(image_bytes(200, 200), 480, max_pixels=10_000)
