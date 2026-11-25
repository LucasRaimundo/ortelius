"""Tamanhos já gerados, guardados em disco para não reprocessar o original a cada visita."""

import os
import tempfile
from pathlib import Path


class RenditionCache:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def path(self, version: str, width: int) -> Path:
        # A versão já identifica arquivo e conteúdo (domain.media.file_version); trocar a imagem gera outro nome.
        return self._directory / f"{version}-{width}.webp"

    def store(self, path: Path, data: bytes) -> None:
        """Grava de forma atômica: duas visitas simultâneas nunca leem um arquivo pela metade."""
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(data)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
