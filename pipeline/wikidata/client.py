"""Leitura de itens do Wikidata pela API `wbgetentities`, com as regras de uso de robôs da Wikimedia."""

import time
from collections.abc import Callable
from typing import Any, cast

import httpx2

API_URL = "https://www.wikidata.org/w/api.php"
# A Wikimedia exige identificação; o repositório dá o contato sem expor e-mail pessoal.
USER_AGENT = "ortelius/0.2 (+https://github.com/LucasRaimundo/ortelius)"
MAX_ATTEMPTS = 4
MAX_WAIT_SECONDS = 30.0


class WikidataError(Exception):
    """Falha ao ler um item; a mensagem é exibida ao revisor."""


def create_http_client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    return httpx2.Client(headers={"User-Agent": USER_AGENT}, timeout=httpx2.Timeout(10.0), transport=transport)


class WikidataClient:
    def __init__(self, http: httpx2.Client, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._http = http
        self._sleep = sleep

    def fetch_item(self, qid: str) -> dict[str, Any]:
        params = {
            "action": "wbgetentities",
            "ids": qid,
            "props": "info|labels|aliases|descriptions|claims",
            "format": "json",
            "formatversion": "2",
        }
        # Sem `maxlag`: ele inclui o atraso do serviço SPARQL, que existe para frear robôs que editam e costuma
        # passar de minutos; com ele, uma leitura simples seria recusada por horas.
        problem = ""
        retry_after: str | None = None
        for attempt in range(MAX_ATTEMPTS):
            if attempt:
                self._sleep(_wait_seconds(attempt, retry_after))
            try:
                response = self._http.get(API_URL, params=params)
            except httpx2.TransportError as error:
                problem, retry_after = f"falha de rede: {error!r}", None
                continue
            retry_after = response.headers.get("Retry-After")
            if response.status_code == 429 or response.status_code >= 500:
                problem = f"HTTP {response.status_code}"
                continue
            if response.status_code != 200:
                raise WikidataError(f"{qid}: o Wikidata respondeu HTTP {response.status_code}")
            body = _json(qid, response)
            error = body.get("error")
            if not isinstance(error, dict):
                return body
            code = cast(dict[str, Any], error).get("code")
            reason = "não existe no Wikidata" if code == "no-such-entity" else f"erro do Wikidata ({code})"
            raise WikidataError(f"{qid}: {reason}")
        raise WikidataError(f"{qid}: Wikidata indisponível após {MAX_ATTEMPTS} tentativas ({problem})")


def _wait_seconds(attempt: int, retry_after: str | None) -> float:
    try:
        wait = float(retry_after) if retry_after else 2.0**attempt
    except ValueError:
        wait = 2.0**attempt
    return min(wait, MAX_WAIT_SECONDS)


def _json(qid: str, response: httpx2.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise WikidataError(f"{qid}: resposta do Wikidata não é JSON") from error
    if not isinstance(body, dict):
        raise WikidataError(f"{qid}: resposta do Wikidata em formato inesperado")
    return cast(dict[str, Any], body)
