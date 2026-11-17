"""Geração do rascunho de texto pelo OpenRouter (modelos `:free`), alternativa quando o Gemini gratuito falha."""

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

API_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "nvidia/nemotron-3.5-super-vl:free"


def _is_daily_quota(error: Mapping[str, Any]) -> bool:
    # Modelos gratuitos têm limite diário por conta; o OpenRouter só o distingue pela mensagem.
    return "per-day" in str(error.get("message", "")).lower()


OPENROUTER = Provider("OpenRouter", is_daily_quota=_is_daily_quota)


class OpenRouterDrafter:
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
            "model": self._model,
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {"role": "user", "content": build_prompt(request)},
            ],
            # strict exige todos os campos obrigatórios; os blocos têm campos opcionais por tipo.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rascunho", "strict": False, "schema": OUTPUT_SCHEMA},
            },
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        reply = post_with_retries(
            self._http,
            API_URL,
            body=body,
            headers={"Authorization": f"Bearer {self._api_key}", "X-Title": "ortelius"},
            provider=OPENROUTER,
            sleep=self._sleep,
            notify=self._notify,
        )
        try:
            return _parse_reply(reply)
        except (AttributeError, IndexError, KeyError, TypeError) as error:
            raise DraftingError(f"resposta do OpenRouter em formato inesperado ({error!r})") from error


def _parse_reply(body: dict[str, Any]) -> ModelReply:
    choices = cast(list[dict[str, Any]], body.get("choices") or [])
    if not choices:
        raise DraftingError("o OpenRouter não devolveu resposta")
    choice = choices[0]
    reason = choice.get("finish_reason")
    if reason == "length":
        raise DraftingError("resposta cortada pelo limite de tamanho; reduza as fontes ou peça menos seções")
    if reason == "content_filter":
        raise DraftingError("o modelo recusou responder (content_filter)")
    if reason not in ("stop", None):
        raise DraftingError(f"o modelo interrompeu a resposta ({reason})")
    content = str(cast(dict[str, Any], choice["message"]).get("content") or "")
    # Alguns modelos embrulham o JSON em bloco de código mesmo com saída estruturada pedida.
    text = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", content)
    try:
        payload = json.loads(text)
    except ValueError as error:
        raise DraftingError("o modelo devolveu texto que não é o JSON pedido") from error
    usage = {k: v for k, v in cast(dict[str, Any], body.get("usage", {})).items() if isinstance(v, int)}
    model_version = f"{body.get('model', '')} via {body.get('provider', '?')}"
    return ModelReply(payload=payload, model_version=model_version, usage=usage)
