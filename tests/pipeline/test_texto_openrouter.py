import json
from typing import Any

import httpx2
import pytest

from pipeline.texto.model import DraftingError, QuotaExhaustedError, create_http_client
from pipeline.texto.openrouter import DEFAULT_MODEL, OpenRouterDrafter
from tests.pipeline.test_texto_gemini import ANSWER, REQUEST

API_KEY = "sk-or-v1-teste"


def completion(content: str | None = None, *, finish: str = "stop") -> dict[str, Any]:
    return {
        "model": DEFAULT_MODEL,
        "provider": "Nvidia",
        "choices": [
            {"finish_reason": finish, "message": {"role": "assistant", "content": content or json.dumps(ANSWER)}}
        ],
        "usage": {"prompt_tokens": 400, "completion_tokens": 120, "total_tokens": 520},
    }


def drafter_answering(*answers: httpx2.Response) -> tuple[OpenRouterDrafter, list[httpx2.Request], list[float]]:
    requests: list[httpx2.Request] = []
    sleeps: list[float] = []
    pending = list(answers)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return pending.pop(0)

    http = create_http_client(httpx2.MockTransport(handle))
    return OpenRouterDrafter(http, api_key=API_KEY, sleep=sleeps.append), requests, sleeps


def test_sends_the_schema_and_records_which_provider_answered() -> None:
    drafter, requests, _ = drafter_answering(httpx2.Response(200, json=completion()))

    reply = drafter.draft(REQUEST)

    [request] = requests
    body = json.loads(request.content)
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert API_KEY not in str(request.url)
    assert body["model"] == DEFAULT_MODEL
    assert body["response_format"]["json_schema"]["schema"]["required"][0] == "summary"
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["reasoning"] == {"effort": "low", "exclude": True}
    assert (reply.payload, reply.model_version) == (ANSWER, f"{DEFAULT_MODEL} via Nvidia")
    assert reply.usage["total_tokens"] == 520


def test_accepts_json_wrapped_in_a_code_block() -> None:
    drafter, _, _ = drafter_answering(httpx2.Response(200, json=completion(f"```json\n{json.dumps(ANSWER)}\n```")))

    assert drafter.draft(REQUEST).payload == ANSWER


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (completion(finish="length"), "resposta cortada"),
        (completion(finish="content_filter"), "recusou responder"),
        (completion("Claro! Aqui está o rascunho."), "não é o JSON pedido"),
        ({"choices": []}, "não devolveu resposta"),
        (
            {"error": {"code": 400, "message": "modelo inexistente"}},
            r"recusou o pedido \(HTTP 400\): modelo inexistente",
        ),
    ],
)
def test_turns_broken_answers_into_reasons(body: dict[str, Any], message: str) -> None:
    drafter, _, _ = drafter_answering(httpx2.Response(200, json=body))

    with pytest.raises(DraftingError, match=message):
        drafter.draft(REQUEST)


def test_says_when_the_model_spent_the_whole_limit_thinking() -> None:
    body = completion(finish="length")
    body["choices"][0]["message"]["content"] = ""
    body["usage"]["completion_tokens_details"] = {"reasoning_tokens": 65536}
    drafter, _, _ = drafter_answering(httpx2.Response(200, json=body))

    with pytest.raises(DraftingError, match="gastou todo o limite raciocinando"):
        drafter.draft(REQUEST)


def test_daily_limit_of_free_models_stops_at_once() -> None:
    limit = {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day. Add credits to unlock."}}
    drafter, requests, _ = drafter_answering(httpx2.Response(429, json=limit))

    with pytest.raises(QuotaExhaustedError, match="cota diária gratuita do OpenRouter esgotada"):
        drafter.draft(REQUEST)
    assert len(requests) == 1


def test_rate_limit_waits_what_the_server_asks_for() -> None:
    limited = httpx2.Response(429, json={"error": {"code": 429, "message": "slow down"}}, headers={"Retry-After": "7"})
    drafter, requests, sleeps = drafter_answering(limited, httpx2.Response(200, json=completion()))

    drafter.draft(REQUEST)

    assert (len(requests), sleeps) == (2, [7.0])


def test_upstream_error_inside_a_successful_response_is_retried() -> None:
    upstream = {"error": {"code": 502, "message": "Provider returned error"}}
    drafter, requests, sleeps = drafter_answering(
        httpx2.Response(200, json=upstream), httpx2.Response(200, json=completion())
    )

    drafter.draft(REQUEST)

    assert (len(requests), sleeps) == (2, [10.0])
