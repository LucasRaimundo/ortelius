"""Regras das imagens servidas pelo Atlas."""

import hashlib
from datetime import datetime

# Larguras que a API gera. Uma lista fechada impede que alguém force o servidor a produzir tamanhos arbitrários.
RENDITION_WIDTHS = (480, 960, 1600)


def file_version(file_id: str, modified_at: datetime) -> str:
    """Identifica o conteúdo do arquivo: trocar a imagem no painel muda a versão e, portanto, a URL."""
    return hashlib.sha256(f"{file_id}:{modified_at.isoformat()}".encode()).hexdigest()[:12]
