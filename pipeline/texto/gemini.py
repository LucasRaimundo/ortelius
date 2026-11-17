"""Geração do rascunho de texto pela API REST do Gemini (camada gratuita do Google AI Studio)."""

import json
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any, Protocol, cast

import httpx2

from pipeline.texto.draft import OUTPUT_SCHEMA
from pipeline.texto.sources import SourceText

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Sobrecarga do modelo (503 "high demand") dura minutos; esperas curtas só gastam as tentativas.
RETRY_WAITS_SECONDS = (10.0, 30.0, 60.0, 60.0)
MAX_ATTEMPTS = len(RETRY_WAITS_SECONDS) + 1
MAX_WAIT_SECONDS = 60.0
MAX_OUTPUT_TOKENS = 32_768

SYSTEM_INSTRUCTION = """\
Você escreve rascunhos de páginas do Atlas, uma enciclopédia em português do Brasil para leitores curiosos.
O rascunho será revisado por uma pessoa antes de ser publicado.

Regras:
- Use somente o que as fontes fornecidas dizem. Não use conhecimento próprio para acrescentar fatos, datas,
  números, nomes ou citações.
- Escreva com suas palavras. Não copie trechos das fontes; para uma citação direta, use um bloco quote curto,
  com o autor ou a obra na legenda.
- Datas antes de Cristo sempre com "a.C." (ex.: 44 a.C.).
- Em cada seção, liste em "sources" os identificadores (F1, F2…) das fontes que a sustentam. Nunca invente
  identificadores.
- Em "points_to_check", liste cada afirmação do texto que não está explícita nas fontes (inferências, datas
  calculadas, interpretações) e cada divergência entre as fontes. Lista vazia só se tudo estiver explícito.
- Conversões também vão para "points_to_check": datas do calendário romano, idades e contagens ordinais
  (ex.: "no seu décimo segundo ano" é aos 11 anos, não "ao completar doze anos").
- Quando as fontes divergirem ou forem lendárias, diga isso no texto.
- Texto puro em todos os campos, sem Markdown nem HTML.
- "summary": uma ou duas frases. "description": introdução de um ou dois parágrafos. Seções com títulos
  curtos, em ordem lógica para a leitura."""


class DraftingError(Exception):
    """O modelo não produziu um rascunho; a mensagem diz o motivo e o que fazer."""


class QuotaExhaustedError(DraftingError):
    """A cota do dia acabou: nenhum pedido seguinte terá sucesso até ela renovar."""


@dataclass(frozen=True)
class DraftRequest:
    entity_name: str
    entity_type: str
    sources: Sequence[SourceText]


@dataclass(frozen=True)
class ModelReply:
    payload: object
    model_version: str
    usage: dict[str, int] = field(default_factory=dict[str, int])


class TextDrafter(Protocol):
    @property
    def model(self) -> str: ...

    def draft(self, request: DraftRequest) -> ModelReply: ...


def build_prompt(request: DraftRequest) -> str:
    documents = "\n\n".join(
        f'<fonte id="{source.label}" titulo="{escape(source.title)}">\n{source.text}\n</fonte>'
        for source in request.sources
    )
    return (
        f"Escreva o rascunho da página de {request.entity_name} (tipo: {request.entity_type}) "
        f"a partir destas fontes:\n\n{documents}"
    )


def create_http_client(transport: httpx2.BaseTransport | None = None) -> httpx2.Client:
    # A geração com a obra inteira no contexto pode levar minutos.
    return httpx2.Client(timeout=httpx2.Timeout(10.0, read=300.0), transport=transport)


class GeminiDrafter:
    def __init__(
        self,
        http: httpx2.Client,
        *,
        api_key: str,
        model: str,
        sleep: Callable[[float], None] = time.sleep,
        notify: Callable[[str], None] = lambda message: None,
    ) -> None:
        self._http = http
        self._api_key = api_key
        self._model = model
        self._sleep = sleep
        self._notify = notify

    @property
    def model(self) -> str:
        return self._model

    def draft(self, request: DraftRequest) -> ModelReply:
        body = {
            "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
            "contents": [{"role": "user", "parts": [{"text": build_prompt(request)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": OUTPUT_SCHEMA,
                "maxOutputTokens": MAX_OUTPUT_TOKENS,
            },
        }
        problem = ""
        for attempt in range(MAX_ATTEMPTS):
            try:
                # A chave vai no cabeçalho, não na URL: URLs aparecem em mensagens de erro e logs.
                response = self._http.post(
                    API_URL.format(model=self._model), json=body, headers={"x-goog-api-key": self._api_key}
                )
            except (httpx2.ConnectError, httpx2.ConnectTimeout) as error:
                problem, wait = f"falha de conexão: {error!r}", _backoff(attempt)
            except httpx2.TransportError as error:
                # O pedido chegou: o Google pode ainda estar gerando e contando a cota. Repetir mandaria as fontes
                # inteiras de novo, gastando mais cota sem garantia de resposta.
                raise DraftingError(
                    f"o Gemini não respondeu a tempo ({error!r}); nada foi gravado, rode de novo mais tarde"
                ) from error
            else:
                if response.status_code == 200:
                    return _reply(_json(response))
                error = _error(response)
                if response.status_code == 429 and _is_daily_quota(error):
                    raise QuotaExhaustedError("cota diária gratuita do Gemini esgotada; tente amanhã")
                if response.status_code != 429 and response.status_code < 500:
                    message = error.get("message", "")
                    raise DraftingError(f"o Gemini recusou o pedido (HTTP {response.status_code}): {message}")
                message = str(error.get("message", "")).strip()
                problem = f"HTTP {response.status_code}: {message}" if message else f"HTTP {response.status_code}"
                wait = _retry_delay(error) or _backoff(attempt)
            if attempt + 1 < MAX_ATTEMPTS:
                wait = min(wait, MAX_WAIT_SECONDS)
                self._notify(f"Gemini indisponível ({problem}); nova tentativa em {wait:.0f} s")
                self._sleep(wait)
        raise DraftingError(f"Gemini indisponível após {MAX_ATTEMPTS} tentativas ({problem})")


def _backoff(attempt: int) -> float:
    return RETRY_WAITS_SECONDS[min(attempt, len(RETRY_WAITS_SECONDS) - 1)]


def _json(response: httpx2.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise DraftingError("resposta do Gemini não é JSON") from error
    if not isinstance(body, dict):
        raise DraftingError("resposta do Gemini em formato inesperado")
    return cast(dict[str, Any], body)


def _error(response: httpx2.Response) -> dict[str, Any]:
    try:
        error = response.json().get("error")
    except (ValueError, AttributeError):
        return {}
    return cast(dict[str, Any], error) if isinstance(error, dict) else {}


def _details(error: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    details = error.get("details")
    if not isinstance(details, list):
        return []
    return [d for d in cast(list[dict[str, Any]], details) if str(d.get("@type", "")).endswith(kind)]


def _is_daily_quota(error: dict[str, Any]) -> bool:
    return any(
        "PerDay" in str(violation.get("quotaId", ""))
        for failure in _details(error, "QuotaFailure")
        for violation in cast(list[dict[str, Any]], failure.get("violations", []))
    )


def _retry_delay(error: dict[str, Any]) -> float | None:
    for info in _details(error, "RetryInfo"):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(info.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


def _reply(body: dict[str, Any]) -> ModelReply:
    try:
        return _parse_reply(body)
    except (AttributeError, IndexError, KeyError, TypeError) as error:
        raise DraftingError(f"resposta do Gemini em formato inesperado ({error!r})") from error


def _parse_reply(body: dict[str, Any]) -> ModelReply:
    blocked = cast(dict[str, Any], body.get("promptFeedback", {})).get("blockReason")
    if blocked:
        raise DraftingError(f"o Gemini recusou as fontes ({blocked})")
    candidates = cast(list[dict[str, Any]], body.get("candidates") or [])
    if not candidates:
        raise DraftingError("o Gemini não devolveu resposta")
    candidate = candidates[0]
    reason = candidate.get("finishReason")
    if reason == "MAX_TOKENS":
        raise DraftingError("resposta cortada pelo limite de tamanho; reduza as fontes ou peça menos seções")
    if reason == "RECITATION":
        raise DraftingError("o Gemini bloqueou a resposta por reproduzir as fontes (RECITATION); tente de novo")
    if reason != "STOP":
        raise DraftingError(f"o Gemini interrompeu a resposta ({reason})")
    parts = cast(list[dict[str, Any]], cast(dict[str, Any], candidate["content"])["parts"])
    text = "".join(str(part.get("text", "")) for part in parts if not part.get("thought"))
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise DraftingError("o Gemini devolveu texto que não é o JSON pedido") from error
    metadata = cast(dict[str, Any], body.get("usageMetadata", {}))
    usage = {key: value for key, value in metadata.items() if isinstance(value, int)}
    return ModelReply(payload=payload, model_version=str(body.get("modelVersion", "")), usage=usage)
