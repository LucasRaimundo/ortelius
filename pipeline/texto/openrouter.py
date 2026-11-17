"""Geração do rascunho de texto pelo OpenRouter (modelos `:free`), alternativa quando o Gemini gratuito falha."""

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx2

from pipeline.texto.draft import OUTPUT_SCHEMA
from pipeline.texto.model import (
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
# Modelos de raciocínio contam o pensamento como saída: sem limite, o Nemotron gastou os 32 mil tokens pensando
# e não escreveu a resposta. O teto maior deixa espaço ao texto mesmo com raciocínio.
MAX_COMPLETION_TOKENS = 65_536


def strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Versão do schema aceita pelo modo estrito: todo campo obrigatório, os opcionais aceitando null."""
    node = dict(schema)
    if node.get("type") == "object":
        required = set(cast(list[str], node.get("required", [])))
        properties = cast(dict[str, Any], node.get("properties", {}))
        node["properties"] = {
            name: strict_schema(spec) if name in required else _nullable(strict_schema(spec))
            for name, spec in properties.items()
        }
        node["required"] = list(properties)
        node["additionalProperties"] = False
    if node.get("type") == "array" and "items" in node:
        node["items"] = strict_schema(cast(dict[str, Any], node["items"]))
    return node


def _nullable(schema: dict[str, Any]) -> dict[str, Any]:
    node = {**schema, "type": [schema["type"], "null"]}
    if "enum" in node:
        node["enum"] = [*node["enum"], None]
    return node


STRICT_SCHEMA = strict_schema(OUTPUT_SCHEMA)


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
            # Sem strict, o formato é só sugerido: o Ernie omitiu campos obrigatórios em 2 de 5 respostas.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rascunho", "strict": True, "schema": STRICT_SCHEMA},
            },
            "max_tokens": MAX_COMPLETION_TOKENS,
            "reasoning": {"effort": "low", "exclude": True},
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
        details = cast(dict[str, Any], cast(dict[str, Any], body.get("usage", {})).get("completion_tokens_details", {}))
        if not cast(dict[str, Any], choice["message"]).get("content") and details.get("reasoning_tokens"):
            raise DraftingError(
                "o modelo gastou todo o limite raciocinando e não escreveu a resposta; troque de modelo"
            )
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
