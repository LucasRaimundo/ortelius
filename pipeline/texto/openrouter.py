"""Geração do rascunho de texto pelo OpenRouter (modelos `:free`), alternativa quando o Gemini gratuito falha."""

import json
import re
import time
from collections.abc import Callable, Mapping
from typing import Any, cast

import httpx2

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


# Modelos menores erram com blocos de tipos variados: o Ernie misturou campos (nota em `text`, conteúdo em
# `items`) e, com um schema por tipo, escreveu tudo como listas. Só parágrafos não deixa onde errar; o revisor
# estrutura no painel. O schema segue o modo estrito: todo campo obrigatório e nenhum campo extra.
PARAGRAPHS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Uma ou duas frases para o cartão do globo e a busca."},
        "description": {"type": "string", "description": "Introdução da página, um ou dois parágrafos."},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                    "paragraphs": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "sources", "paragraphs"],
                "additionalProperties": False,
            },
        },
        "points_to_check": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "description", "sections", "points_to_check"],
    "additionalProperties": False,
}

PARAGRAPHS_INSTRUCTION = (
    '\n\nNesta resposta, cada seção tem uma lista de parágrafos em "paragraphs", cada um um texto corrido. '
    "Não use títulos internos, listas nem citações em bloco."
)


def to_blocks_payload(payload: object) -> object:
    """Converte a resposta só de parágrafos no formato de blocos que o validador do pipeline entende."""
    if not isinstance(payload, dict):
        return payload
    answer = cast(dict[str, Any], payload)
    sections = answer.get("sections")
    if not isinstance(sections, list):
        return answer
    converted: list[object] = []
    for raw in cast(list[object], sections):
        data = cast(dict[str, Any], raw) if isinstance(raw, dict) else None
        if data is not None and isinstance(data.get("paragraphs"), list):
            blocks = [{"type": "paragraph", "text": text} for text in cast(list[object], data["paragraphs"])]
            converted.append({key: value for key, value in data.items() if key != "paragraphs"} | {"blocks": blocks})
        else:
            converted.append(cast(object, raw))
    return answer | {"sections": converted}


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
                {"role": "system", "content": SYSTEM_INSTRUCTION + PARAGRAPHS_INSTRUCTION},
                {"role": "user", "content": build_prompt(request)},
            ],
            # Sem strict, o formato é só sugerido: o Ernie omitiu campos obrigatórios em 2 de 5 respostas.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "rascunho", "strict": True, "schema": PARAGRAPHS_SCHEMA},
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
    return ModelReply(payload=to_blocks_payload(payload), model_version=model_version, usage=usage)
