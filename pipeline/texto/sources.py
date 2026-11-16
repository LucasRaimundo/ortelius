"""Reúne o texto das fontes de uma entidade para o modelo ler (plan.md §3.8).

Só entra texto com licença aberta declarada pelo revisor, vindo de domínios conhecidos ou colado na citação.
Nada é cortado em silêncio: fonte grande demais falha, porque o modelo escreveria sobre um pedaço achando que
leu a obra.
"""

import hashlib
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal
from uuid import UUID

import httpx2

ALLOWED_DOMAINS = ("perseus.tufts.edu", "penelope.uchicago.edu", "wikisource.org", "gutenberg.org")
MAX_DOWNLOAD_BYTES = 5_000_000
MAX_TOTAL_CHARS = 600_000
USER_AGENT = "ortelius/0.2 (+https://github.com/LucasRaimundo/ortelius)"

_SKIPPED_TAGS = frozenset({"script", "style", "noscript", "nav", "header", "footer", "aside", "form", "svg"})
_BLOCK_TAGS = frozenset({"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "blockquote", "section"})


class SourceTextError(Exception):
    """O texto das fontes não pôde ser reunido; a mensagem diz o que o revisor precisa fazer."""


@dataclass(frozen=True)
class Citation:
    source_id: UUID
    title: str
    text_url: str | None
    text_license: str | None
    excerpt: str | None


@dataclass(frozen=True)
class SourceText:
    label: str
    source_id: UUID
    title: str
    origin: Literal["url", "excerpt"]
    url: str | None
    text: str

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


@dataclass(frozen=True)
class GatheredSources:
    texts: tuple[SourceText, ...]
    skipped: tuple[str, ...]


def gather_sources(citations: Iterable[Citation], fetch_text: Callable[[str], str]) -> GatheredSources:
    texts: list[SourceText] = []
    skipped: list[str] = []
    seen: set[UUID] = set()
    for citation in citations:
        if citation.source_id in seen:
            continue
        seen.add(citation.source_id)
        if citation.text_license is None:
            skipped.append(f"{citation.title}: sem licença do texto")
            continue
        label = f"F{len(texts) + 1}"
        # O trecho colado tem precedência: o revisor escolheu a parte relevante, e é menor que a obra.
        if citation.excerpt and citation.excerpt.strip():
            texts.append(
                SourceText(label, citation.source_id, citation.title, "excerpt", None, citation.excerpt.strip())
            )
        elif citation.text_url:
            text = fetch_text(citation.text_url)
            texts.append(SourceText(label, citation.source_id, citation.title, "url", citation.text_url, text))
        else:
            skipped.append(f"{citation.title}: sem endereço do texto nem trecho colado")
    if not texts:
        reasons = "; ".join(skipped) or "nenhuma fonte citada"
        raise SourceTextError(f"nenhuma fonte com texto utilizável ({reasons})")
    total = sum(len(text.text) for text in texts)
    if total > MAX_TOTAL_CHARS:
        raise SourceTextError(
            f"texto das fontes grande demais ({total} caracteres, limite {MAX_TOTAL_CHARS}): "
            "aponte o endereço de um livro ou capítulo, ou cole o trecho relevante na citação"
        )
    return GatheredSources(tuple(texts), tuple(skipped))


def create_http_client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    return httpx2.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=httpx2.Timeout(20.0),
        follow_redirects=True,
        transport=transport,
    )


class SourceFetcher:
    def __init__(self, http: httpx2.Client) -> None:
        self._http = http

    def fetch_text(self, url: str) -> str:
        _require_allowed(httpx2.URL(url))
        try:
            with self._http.stream("GET", url) as response:
                for step in (*response.history, response):
                    _require_allowed(step.url)
                if response.status_code != 200:
                    raise SourceTextError(f"{url}: o site respondeu HTTP {response.status_code}")
                content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
                if content_type not in ("text/html", "text/plain"):
                    raise SourceTextError(f"{url}: formato {content_type or 'desconhecido'} não suportado")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_DOWNLOAD_BYTES:
                        raise SourceTextError(f"{url}: página maior que {MAX_DOWNLOAD_BYTES // 1_000_000} MB")
                encoding = response.encoding or "utf-8"
        except httpx2.TransportError as error:
            raise SourceTextError(f"{url}: falha de rede ({error!r})") from error
        text = bytes(body).decode(encoding, errors="replace")
        return html_to_text(text) if content_type == "text/html" else _normalize(text)


def _require_allowed(url: httpx2.URL) -> None:
    host = url.host.lower()
    allowed = url.scheme in ("http", "https") and any(
        host == domain or host.endswith(f".{domain}") for domain in ALLOWED_DOMAINS
    )
    if not allowed:
        domains = ", ".join(ALLOWED_DOMAINS)
        raise SourceTextError(f"{url}: endereço fora dos domínios permitidos ({domains})")


def html_to_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    parser.close()
    return _normalize("".join(parser.parts))


def _normalize(text: str) -> str:
    lines = (re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED_TAGS:
            self._skipping = max(0, self._skipping - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.parts.append(data)
