"""Onde moram os originais das imagens enviadas pelo painel."""

from pathlib import Path
from typing import Protocol


class MediaStorage(Protocol):
    def read(self, filename: str) -> bytes | None:
        """Bytes do original, ou None se o arquivo não existir."""
        ...


class LocalMediaStorage:
    """Pasta de uploads do Directus (`var/uploads` no ambiente local)."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def read(self, filename: str) -> bytes | None:
        path = (self._root / filename).resolve()
        # O nome vem do banco do CMS; nunca sai da pasta de uploads, mesmo que alguém grave "../" ali.
        if not path.is_relative_to(self._root) or not path.is_file():
            return None
        return path.read_bytes()
