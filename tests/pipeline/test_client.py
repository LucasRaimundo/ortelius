import httpx2
import pytest

from pipeline.wikidata.client import USER_AGENT, WikidataClient, WikidataError, create_http_client

ITEM = {"entities": {"Q1": {"id": "Q1"}}, "success": 1}
MAXLAG = {"error": {"code": "maxlag", "info": "lagged"}}


def client_answering(*answers: httpx2.Response | Exception) -> tuple[WikidataClient, list[httpx2.Request], list[float]]:
    requests: list[httpx2.Request] = []
    sleeps: list[float] = []
    pending = list(answers)

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        answer = pending.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = WikidataClient(create_http_client(httpx2.MockTransport(handle)), sleep=sleeps.append)
    return client, requests, sleeps


def ok(body: object, headers: dict[str, str] | None = None) -> httpx2.Response:
    return httpx2.Response(200, json=body, headers=headers)


def test_identifies_itself_and_asks_to_wait_when_servers_lag() -> None:
    client, requests, _ = client_answering(ok(ITEM))

    assert client.fetch_item("Q1") == ITEM
    assert requests[0].headers["User-Agent"] == USER_AGENT
    assert requests[0].url.params["maxlag"] == "5"
    assert requests[0].url.params["ids"] == "Q1"


@pytest.mark.parametrize(
    "first",
    [
        ok(MAXLAG, {"Retry-After": "5"}),
        httpx2.Response(429, headers={"Retry-After": "5"}),
        httpx2.Response(503, headers={"Retry-After": "5"}),
    ],
)
def test_retries_after_the_wait_the_server_asks_for(first: httpx2.Response) -> None:
    client, requests, sleeps = client_answering(first, ok(ITEM))

    assert client.fetch_item("Q1") == ITEM
    assert (len(requests), sleeps) == (2, [5.0])


def test_retries_network_failures_with_growing_waits() -> None:
    client, _, sleeps = client_answering(
        httpx2.ConnectError("sem rede"), httpx2.ReadTimeout("lento"), httpx2.ConnectError("sem rede"), ok(ITEM)
    )

    assert client.fetch_item("Q1") == ITEM
    assert sleeps == [2.0, 4.0, 8.0]


def test_caps_the_wait_requested_by_the_server() -> None:
    client, _, sleeps = client_answering(httpx2.Response(503, headers={"Retry-After": "3600"}), ok(ITEM))

    client.fetch_item("Q1")

    assert sleeps == [30.0]


def test_gives_up_after_the_last_attempt_saying_why() -> None:
    client, requests, _ = client_answering(*(httpx2.Response(503) for _ in range(4)))

    with pytest.raises(WikidataError, match=r"Q1: Wikidata indisponível após 4 tentativas \(HTTP 503\)"):
        client.fetch_item("Q1")
    assert len(requests) == 4


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (ok({"error": {"code": "no-such-entity"}}), "Q1: não existe no Wikidata"),
        (ok({"error": {"code": "param-invalid"}}), r"Q1: erro do Wikidata \(param-invalid\)"),
        (httpx2.Response(403), "Q1: o Wikidata respondeu HTTP 403"),
        (httpx2.Response(200, text="<html>"), "Q1: resposta do Wikidata não é JSON"),
    ],
)
def test_fails_without_retrying_when_retrying_cannot_help(answer: httpx2.Response, message: str) -> None:
    client, requests, _ = client_answering(answer)

    with pytest.raises(WikidataError, match=message):
        client.fetch_item("Q1")
    assert len(requests) == 1
