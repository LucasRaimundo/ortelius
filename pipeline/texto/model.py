"""O que todo provedor de modelo compartilha: instruções, pedido, resposta e novas tentativas."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from html import escape
from typing import Any, Protocol, cast

import httpx2

from pipeline.texto.sources import SourceText

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
- Escreva com suas palavras, em português do Brasil, mesmo quando a fonte estiver em outra língua; use a forma
  portuguesa dos nomes (Augusto, não Augustus). Não copie trechos das fontes; para uma citação direta, use um
  bloco quote curto, com o autor ou a obra na legenda.
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


@dataclass(frozen=True)
class Provider:
    """Como cada provedor descreve cota diária e espera pedida, a partir do corpo de erro."""

    name: str
    is_daily_quota: Callable[[Mapping[str, Any]], bool]
    retry_delay: Callable[[Mapping[str, Any]], float | None] = lambda error: None


def post_with_retries(
    http: httpx2.Client,
    url: str,
    *,
    body: Mapping[str, Any],
    headers: Mapping[str, str],
    provider: Provider,
    sleep: Callable[[float], None],
    notify: Callable[[str], None],
) -> dict[str, Any]:
    problem = ""
    for attempt in range(MAX_ATTEMPTS):
        try:
            # A chave vai no cabeçalho, não na URL: URLs aparecem em mensagens de erro e logs.
            response = http.post(url, json=dict(body), headers=dict(headers))
        except (httpx2.ConnectError, httpx2.ConnectTimeout) as error:
            problem, wait = f"falha de conexão: {error!r}", _backoff(attempt)
        except httpx2.TransportError as error:
            # O pedido chegou: o provedor pode ainda estar gerando e contando a cota. Repetir mandaria as fontes
            # inteiras de novo, gastando mais cota sem garantia de resposta.
            raise DraftingError(
                f"o {provider.name} não respondeu a tempo ({error!r}); nada foi gravado, rode de novo mais tarde"
            ) from error
        else:
            body_json = _json(response, provider)
            error = error_of(body_json)
            status = response.status_code if response.status_code != 200 or not error else _status(error)
            if status == 200:
                return body_json
            if status == 429 and provider.is_daily_quota(error):
                raise QuotaExhaustedError(f"cota diária gratuita do {provider.name} esgotada; tente amanhã")
            message = str(error.get("message", "")).strip()
            if status != 429 and status < 500:
                raise DraftingError(f"o {provider.name} recusou o pedido (HTTP {status}): {message}")
            problem = f"HTTP {status}: {message}" if message else f"HTTP {status}"
            wait = provider.retry_delay(error) or _retry_after(response) or _backoff(attempt)
        if attempt + 1 < MAX_ATTEMPTS:
            wait = min(wait, MAX_WAIT_SECONDS)
            notify(f"{provider.name} indisponível ({problem}); nova tentativa em {wait:.0f} s")
            sleep(wait)
    raise DraftingError(f"{provider.name} indisponível após {MAX_ATTEMPTS} tentativas ({problem})")


def error_of(body: Mapping[str, Any]) -> dict[str, Any]:
    error = body.get("error")
    return cast(dict[str, Any], error) if isinstance(error, dict) else {}


def _status(error: Mapping[str, Any]) -> int:
    code = error.get("code")
    return code if isinstance(code, int) and code >= 400 else 502


def _backoff(attempt: int) -> float:
    return RETRY_WAITS_SECONDS[min(attempt, len(RETRY_WAITS_SECONDS) - 1)]


def _retry_after(response: httpx2.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None


def _json(response: httpx2.Response, provider: Provider) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        if response.status_code != 200:
            return {"error": {"code": response.status_code, "message": ""}}
        raise DraftingError(f"resposta do {provider.name} não é JSON") from error
    if not isinstance(body, dict):
        raise DraftingError(f"resposta do {provider.name} em formato inesperado")
    return cast(dict[str, Any], body)
