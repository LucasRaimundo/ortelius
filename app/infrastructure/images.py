"""Gera os tamanhos servidos a partir do original."""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

ACCEPTED_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
# Acima disso o Pillow nem decodifica: protege contra imagem-bomba (poucos KB que viram gigabytes na memória).
MAX_PIXELS = 50_000_000
WEBP_QUALITY = 80


class UnusableImageError(Exception):
    """O original não é uma imagem aceita: formato fora da lista, corrompido ou grande demais."""


def render_webp(original: bytes, width: int, *, max_pixels: int = MAX_PIXELS) -> bytes:
    """WebP com no máximo `width` de largura, sem ampliar, com a orientação corrigida e sem metadados."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(original)) as image:
                if image.format not in ACCEPTED_FORMATS:
                    raise UnusableImageError(f"formato {image.format} não aceito")
                if image.width * image.height > max_pixels:
                    raise UnusableImageError(f"{image.width}x{image.height} pixels excede o limite")
                upright = ImageOps.exif_transpose(image)
                upright = upright.convert("RGBA" if upright.mode in ("RGBA", "LA", "P") else "RGB")
                # thumbnail mantém a proporção e nunca amplia; a altura do original não limita nada.
                upright.thumbnail((width, upright.height), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                upright.save(output, format="WEBP", quality=WEBP_QUALITY, method=4)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise UnusableImageError(str(error)) from error
