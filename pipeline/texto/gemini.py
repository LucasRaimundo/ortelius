"""Geração do rascunho de texto pela API REST do Gemini (camada gratuita do Google AI Studio)."""

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx2

from pipeline.texto.draft import OUTPUT_SCHEMA
from pipeline.texto.model import (
    MAX_OUTPUT_TOKENS,
    SYSTEM_INSTRUCTION,
    DraftingError,
    DraftRequest,
    ModelReply,
    Provider,
    build_prompt,
    post_with_retries,
)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
DEFAULT_MODEL = "gemini-flash-latest"


def _details(error: Mapping[str, Any], kind: str) -> list[dict[str, Any]]:
    details = error.get("details")
    if not isinstance(details, list):
        return []
    return [d for d in cast(list[dict[str, Any]], details) if str(d.get("@type", "")).endswith(kind)]


def _is_daily_quota(error: Mapping[str, Any]) -> bool:
    return any(
        "PerDay" in str(violation.get("quotaId", ""))
        for failure in _details(error, "QuotaFailure")
        for violation in cast(list[dict[str, Any]], failure.get("violations", []))
    )


def _retry_delay(error: Mapping[str, Any]) -> float | None:
    for info in _details(error, "RetryInfo"):
        match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(info.get("retryDelay", "")))
        if match:
            return float(match.group(1))
    return None


GEMINI = Provider("Gemini", is_daily_quota=_is_daily_quota, retry_delay=_retry_delay)


class GeminiDrafter:
    def __init__(
        self,
        http: httpx2.Client,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
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
        reply = post_with_retries(
            self._http,
            API_URL.format(model=self._model),
            body=body,
            headers={"x-goog-api-key": self._api_key},
            provider=GEMINI,
            sleep=self._sleep,
            notify=self._notify,
        )
        try:
            return _parse_reply(reply)
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
