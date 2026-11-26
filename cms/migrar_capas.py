"""Migra as capas antigas (`entities.cover_media`) para imagens com procedência (`media` + `cover_id`).

Antes da migration 0009, o crédito da capa ficava em texto livre na descrição do arquivo no Directus, no formato
"Autor: … Licença: … Fonte: …". Este script lê esse texto, cria a imagem em `media` e aponta a entidade para ela.
Capas sem crédito reconhecível não são migradas: ficam listadas para o revisor achar a origem ou trocar a imagem.

Por padrão só mostra o que faria. Com --aplicar, grava tudo numa transação.

Uso: uv run python -m cms.migrar_capas [--aplicar] [--textos-alternativos arquivo.json]

O arquivo opcional de textos alternativos é um objeto {"slug": "descrição da imagem"}. Sem ele, o texto
alternativo começa igual à legenda e precisa ser reescrito no painel.
"""

import argparse
import json
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.domain.entities import ImageLicense
from app.infrastructure.database.engine import create_db_engine
from app.infrastructure.database.models import EntityRow, MediaRow
from app.settings import get_settings

_CREDIT = re.compile(
    r"^Autor: (?P<author>.+?)\. Licença: (?P<license>.+?)(?: \((?P<license_url>https?://[^)\s]+)\))?\."
    r" Fonte: (?P<source>https?://\S+?)\.?$"
)
_CC = re.compile(r"^CC (?P<kind>BY(?:-SA)?) (?P<version>1\.0|2\.0|2\.5|3\.0|4\.0)$")


@dataclass(frozen=True)
class Credit:
    author: str
    license: ImageLicense
    license_version: str | None
    source_url: str


@dataclass(frozen=True)
class Migration:
    slug: str
    file_id: str
    caption: str
    alt_text: str
    alt_text_from_caption: bool
    credit: Credit


@dataclass(frozen=True)
class Skipped:
    slug: str
    file_id: str
    caption: str | None
    reason: str


def parse_credit(description: str | None) -> Credit | None:
    """Crédito no formato usado até a 0009; None se não reconhecer autor, licença aceita e fonte."""
    match = _CREDIT.match((description or "").strip())
    if match is None:
        return None
    license_text = match["license"].strip()
    if license_text.lower() in ("public domain", "domínio público"):
        license_, version = ImageLicense.PUBLIC_DOMAIN, None
    elif license_text.upper().startswith("CC0"):
        license_, version = ImageLicense.CC0, None
    elif cc := _CC.match(license_text):
        license_ = ImageLicense.CC_BY_SA if cc["kind"] == "BY-SA" else ImageLicense.CC_BY
        version = cc["version"]
    else:
        return None
    return Credit(match["author"].strip(), license_, version, match["source"])


_PENDING = text("""
    SELECT e.slug, e.cover_media, f.title, f.description
    FROM entities e
    LEFT JOIN directus.directus_files f ON f.id::text = e.cover_media
    WHERE e.cover_media IS NOT NULL AND e.cover_id IS NULL
    ORDER BY e.slug
""")


def plan(session: Session, alt_texts: dict[str, str]) -> list[Migration | Skipped]:
    items: list[Migration | Skipped] = []
    for slug, file_id, title, description in session.execute(_PENDING).all():
        caption = (title or "").strip()
        if not caption:
            reason = "arquivo não existe no Directus" if description is None and title is None else "arquivo sem título"
            items.append(Skipped(slug, file_id, None, reason))
            continue
        credit = parse_credit(description)
        if credit is None:
            items.append(Skipped(slug, file_id, caption, "sem crédito reconhecível (autor, licença aceita e fonte)"))
            continue
        alt_text = alt_texts.get(slug, "").strip()
        items.append(Migration(slug, file_id, caption, alt_text or caption, not alt_text, credit))
    return items


def apply(session: Session, migrations: list[Migration]) -> None:
    """Cria uma imagem por arquivo (reaproveitando a que já existir) e aponta cada entidade para a sua."""
    media_by_file: dict[str, uuid.UUID] = {
        file_id: media_id for media_id, file_id in session.execute(text("SELECT id, file_id FROM media")).all()
    }
    # Trocar a forma de guardar a mesma capa não é edição de conteúdo: a data de atualização não muda.
    # O PostgreSQL só altera triggers sem checagens diferidas pendentes (a regra de publicação é diferida); por
    # isso elas rodam agora, antes de desligar e antes de religar.
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("ALTER TABLE entities DISABLE TRIGGER trg_entities_updated_at"))
    for migration in migrations:
        if migration.file_id not in media_by_file:
            media = MediaRow(
                file_id=migration.file_id,
                alt_text=migration.alt_text,
                caption=migration.caption,
                author=migration.credit.author,
                license=migration.credit.license,
                license_version=migration.credit.license_version,
                source_url=migration.credit.source_url,
            )
            session.add(media)
            session.flush()
            media_by_file[migration.file_id] = media.id
        session.execute(
            update(EntityRow).where(EntityRow.slug == migration.slug).values(cover_id=media_by_file[migration.file_id])
        )
    session.flush()
    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    session.execute(text("ALTER TABLE entities ENABLE TRIGGER trg_entities_updated_at"))
    session.flush()


def report(items: list[Migration | Skipped]) -> str:
    lines: list[str] = []
    for item in items:
        if isinstance(item, Migration):
            credit = item.credit
            version = f" {credit.license_version}" if credit.license_version else ""
            lines.append(f"migra   {item.slug}: {item.caption}")
            lines.append(f"        autor {credit.author} · {credit.license.value}{version} · {credit.source_url}")
            alt_note = " (igual à legenda: reescreva no painel)" if item.alt_text_from_caption else ""
            lines.append(f"        texto alternativo: {item.alt_text}{alt_note}")
        else:
            lines.append(f"pula    {item.slug}: {item.reason}" + (f" ({item.caption})" if item.caption else ""))
    migrated = sum(isinstance(item, Migration) for item in items)
    lines.append(f"{migrated} capa(s) a migrar, {len(items) - migrated} sem crédito para revisar.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migra as capas antigas para imagens com procedência.")
    parser.add_argument("--aplicar", action="store_true", help="grava; sem isto, só mostra o que faria")
    parser.add_argument("--textos-alternativos", type=Path, help='JSON {"slug": "texto alternativo"}')
    args = parser.parse_args(argv)

    alt_texts: dict[str, str] = {}
    if args.textos_alternativos:
        alt_texts = json.loads(args.textos_alternativos.read_text(encoding="utf-8"))

    engine = create_db_engine(get_settings().database_url.get_secret_value())
    try:
        with Session(engine) as session:
            items = plan(session, alt_texts)
            print(report(items))
            if not args.aplicar:
                print("Nada foi gravado. Rode com --aplicar para gravar.")
                return 0
            apply(session, [item for item in items if isinstance(item, Migration)])
            session.commit()
        print("Capas migradas.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
