"""Regras das imagens servidas pelo Atlas."""

import hashlib
from dataclasses import dataclass
from datetime import datetime

from app.domain.entities import ImageLicense

# Larguras que a API gera. Uma lista fechada impede que alguém force o servidor a produzir tamanhos arbitrários.
RENDITION_WIDTHS = (480, 960, 1600)


def file_version(file_id: str, modified_at: datetime) -> str:
    """Identifica o conteúdo do arquivo: trocar a imagem no painel muda a versão e, portanto, a URL."""
    return hashlib.sha256(f"{file_id}:{modified_at.isoformat()}".encode()).hexdigest()[:12]


def rendition_widths(original_width: int) -> list[int]:
    """Larguras reais de cada tamanho servido: nunca maiores que o original, sem repetir."""
    return sorted({min(width, original_width) for width in RENDITION_WIDTHS})


def rendition_for(actual_width: int) -> int:
    """A largura da lista que produz um tamanho real (o menor pedido que chega nele)."""
    return next(width for width in RENDITION_WIDTHS if width >= actual_width)


@dataclass(frozen=True)
class LicenseInfo:
    label: str
    url: str | None


def license_info(license_: ImageLicense, version: str | None) -> LicenseInfo:
    """Nome exibido no crédito e link para o texto da licença (o banco garante a versão para CC BY e CC BY-SA)."""
    match license_:
        case ImageLicense.PUBLIC_DOMAIN:
            return LicenseInfo("Domínio público", None)
        case ImageLicense.CC0:
            return LicenseInfo("CC0 1.0", "https://creativecommons.org/publicdomain/zero/1.0/")
        case ImageLicense.CC_BY:
            return LicenseInfo(f"CC BY {version}", f"https://creativecommons.org/licenses/by/{version}/")
        case ImageLicense.CC_BY_SA:
            return LicenseInfo(f"CC BY-SA {version}", f"https://creativecommons.org/licenses/by-sa/{version}/")
