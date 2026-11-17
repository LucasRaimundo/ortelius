import json
from typing import Any
from uuid import uuid4

import httpx2
import pytest

from pipeline.texto.gemini import GeminiDrafter
from pipeline.texto.model import DraftingError, DraftRequest, QuotaExhaustedError, build_prompt, create_http_client
from pipeline.texto.sources import SourceText

API_KEY = "chave-de-teste"
REQUEST = DraftRequest(
    "Augusto",
    "person",
    [SourceText("F1", uuid4(), 'Suetônio, "Vida de Augusto"', "excerpt", None, "Augustus was born at Rome.")],
)
ANSWER: dict[str, object] = {"summary": "S", "description": "D", "sections": [], "points_to_check": []}


def gemini_body(*, finish: str = "STOP", text: str | None = None, **extra: Any) -> dict[str, Any]:
    parts = [{"text": "pensando...", "thought": True}, {"text": text if text is not None else json.dumps(ANSWER)}]
    return {
        "candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish}],
        "usageMetadata": {"promptTokenCount": 430, "candidatesTokenCount": 271, "totalTokenCount": 1622},
        "modelVersion": "gemini-3.8-flash",
        **extra,
    }


def gemini_error(status: int, *details: dict[str, Any]) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"code": status, "message": "detalhe", "details": list(details)}})


def drafter_answering(*answers: httpx2.Response | Exception) -> tuple[GeminiDrafter, list[httpx2.Request], list[float]]:
    requests: list[httpx2.Request] = []
    sleeps: list[float] = []
    pending = list(answers)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    http = create_http_client(httpx2.MockTransport(handle))
    drafter = GeminiDrafter(
        http, api_key=API_KEY, model="gemini-flash-latest", sleep=sleeps.append, notify=NOTICES.append
    )
    return drafter, requests, sleeps


NOTICES: list[str] = []


@pytest.fixture(autouse=True)
def clear_notices() -> None:
    NOTICES.clear()


def test_sends_sources_and_schema_with_the_key_only_in_the_header() -> None:
    drafter, requests, _ = drafter_answering(httpx2.Response(200, json=gemini_body()))

    reply = drafter.draft(REQUEST)

    [request] = requests
    body = json.loads(request.content)
    assert request.url.path == "/v1beta/models/gemini-flash-latest:generateContent"
    assert request.headers["x-goog-api-key"] == API_KEY
    assert API_KEY not in str(request.url)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["required"] == [
        "summary",
        "description",
        "sections",
        "points_to_check",
    ]
    assert (reply.payload, reply.model_version, reply.usage["totalTokenCount"]) == (ANSWER, "gemini-3.8-flash", 1622)


def test_prompt_delimits_each_source_with_its_label_and_escaped_title() -> None:
    prompt = build_prompt(REQUEST)

    assert '<fonte id="F1" titulo="Suetônio, &quot;Vida de Augusto&quot;">\nAugustus was born at Rome.\n</fonte>' in (
        prompt
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (gemini_body(promptFeedback={"blockReason": "SAFETY"}), r"recusou as fontes \(SAFETY\)"),
        (gemini_body(finish="MAX_TOKENS"), "resposta cortada"),
        (gemini_body(finish="RECITATION"), "reproduzir as fontes"),
        (gemini_body(finish="SAFETY"), r"interrompeu a resposta \(SAFETY\)"),
        (gemini_body(text="Aqui está o rascunho: {"), "não é o JSON pedido"),
        ({"candidates": []}, "não devolveu resposta"),
        ({"candidates": [{"finishReason": "STOP"}]}, "formato inesperado"),
    ],
)
def test_turns_refusals_and_broken_answers_into_reasons(body: dict[str, Any], message: str) -> None:
    drafter, _, _ = drafter_answering(httpx2.Response(200, json=body))

    with pytest.raises(DraftingError, match=message):
        drafter.draft(REQUEST)


def test_waits_the_delay_the_api_asks_for_when_rate_limited() -> None:
    retry = {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}
    drafter, requests, sleeps = drafter_answering(gemini_error(429, retry), httpx2.Response(200, json=gemini_body()))

    drafter.draft(REQUEST)

    assert (len(requests), sleeps) == (2, [37.0])


def test_daily_quota_fails_at_once() -> None:
    quota = {
        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
        "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}],
    }
    drafter, requests, _ = drafter_answering(gemini_error(429, quota))

    with pytest.raises(QuotaExhaustedError, match="cota diária gratuita do Gemini esgotada"):
        drafter.draft(REQUEST)
    assert len(requests) == 1


def test_waits_minutes_while_the_model_is_overloaded_and_then_says_why() -> None:
    overloaded = httpx2.Response(503, json={"error": {"code": 503, "message": "high demand", "status": "UNAVAILABLE"}})
    drafter, requests, sleeps = drafter_answering(
        overloaded, httpx2.ConnectError("sem rede"), overloaded, overloaded, overloaded
    )

    with pytest.raises(DraftingError, match=r"indisponível após 5 tentativas \(HTTP 503: high demand\)"):
        drafter.draft(REQUEST)
    assert (len(requests), sleeps) == (5, [10.0, 30.0, 60.0, 60.0])
    assert NOTICES[0] == "Gemini indisponível (HTTP 503: high demand); nova tentativa em 10 s"


def test_does_not_resend_the_sources_when_the_answer_times_out() -> None:
    drafter, requests, sleeps = drafter_answering(httpx2.ReadTimeout("lento"), httpx2.Response(200, json=gemini_body()))

    with pytest.raises(DraftingError, match=r"não respondeu a tempo.*nada foi gravado"):
        drafter.draft(REQUEST)
    assert (len(requests), sleeps) == (1, [])


def test_recovers_when_the_overload_passes() -> None:
    drafter, requests, sleeps = drafter_answering(gemini_error(503), httpx2.Response(200, json=gemini_body()))

    drafter.draft(REQUEST)

    assert (len(requests), sleeps) == (2, [10.0])


def test_request_errors_fail_without_retrying_and_without_the_key() -> None:
    drafter, requests, _ = drafter_answering(gemini_error(404))

    with pytest.raises(DraftingError, match=r"HTTP 404\): detalhe") as error:
        drafter.draft(REQUEST)
    assert API_KEY not in str(error.value)
    assert len(requests) == 1
