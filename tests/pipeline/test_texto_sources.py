from uuid import UUID, uuid4

import httpx2
import pytest

from pipeline.texto import sources
from pipeline.texto.sources import (
    Citation,
    SourceFetcher,
    SourceTextError,
    create_http_client,
    gather_sources,
    html_to_text,
)

SUETONIUS = "https://penelope.uchicago.edu/Thayer/E/Roman/Texts/Suetonius/12Caesars/Augustus*.html"


def fetcher_answering(handler: httpx2.MockTransport) -> SourceFetcher:
    return SourceFetcher(create_http_client(handler))


def html_page(body: str) -> httpx2.Response:
    return httpx2.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8"})


def citation(
    *,
    title: str = "Suetônio, Vida de Augusto",
    text_url: str | None = SUETONIUS,
    text_license: str | None = "public_domain",
    excerpt: str | None = None,
    source_id: UUID | None = None,
) -> Citation:
    return Citation(source_id or uuid4(), title, text_url, text_license, excerpt)


def test_html_becomes_plain_text_without_menus_or_scripts() -> None:
    html = """<html><head><style>p{}</style><script>track()</script></head><body>
        <nav>Início | Índice</nav><h1>Vida de Augusto</h1>
        <p>Otávio nasceu&nbsp;em Roma.</p><p>Foi   adotado por <i>César</i>.</p>
        <footer>LacusCurtius</footer></body></html>"""

    assert html_to_text(html) == "Vida de Augusto\n\nOtávio nasceu em Roma.\n\nFoi adotado por César."


def test_fetches_html_from_an_allowed_domain() -> None:
    fetcher = fetcher_answering(httpx2.MockTransport(lambda request: html_page("<p>Texto da obra.</p>")))

    assert fetcher.fetch_text(SUETONIUS) == "Texto da obra."


def test_refuses_domains_outside_the_list_without_any_request() -> None:
    requests: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return html_page("x")

    fetcher = fetcher_answering(httpx2.MockTransport(handle))

    for url in (
        "https://pt.wikipedia.org/wiki/Augusto",
        "https://penelope.uchicago.edu.evil.com/x",
        "file:///etc/passwd",
    ):
        with pytest.raises(SourceTextError, match="fora dos domínios permitidos"):
            fetcher.fetch_text(url)
    assert requests == []


def test_refuses_redirect_to_a_domain_outside_the_list() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "penelope.uchicago.edu":
            return httpx2.Response(302, headers={"Location": "https://outro-site.com/texto"})
        return html_page("texto de outro site")

    with pytest.raises(SourceTextError, match=r"outro-site\.com.*fora dos domínios"):
        fetcher_answering(httpx2.MockTransport(handle)).fetch_text(SUETONIUS)


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx2.Response(404), "HTTP 404"),
        (httpx2.Response(200, content=b"%PDF", headers={"Content-Type": "application/pdf"}), "application/pdf"),
        (html_page("x" * (sources.MAX_DOWNLOAD_BYTES + 1)), "maior que 5 MB"),
    ],
)
def test_refuses_pages_it_cannot_use(response: httpx2.Response, message: str) -> None:
    with pytest.raises(SourceTextError, match=message):
        fetcher_answering(httpx2.MockTransport(lambda request: response)).fetch_text(SUETONIUS)


def test_network_failure_names_the_address() -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("sem rede")

    with pytest.raises(SourceTextError, match=r"penelope.*falha de rede"):
        fetcher_answering(httpx2.MockTransport(handle)).fetch_text(SUETONIUS)


def test_gathers_numbered_texts_preferring_the_pasted_excerpt() -> None:
    fetched: list[str] = []

    def fetch(url: str) -> str:
        fetched.append(url)
        return "obra inteira"

    gathered = gather_sources(
        [
            citation(title="Suetônio", excerpt="  trecho escolhido  "),
            citation(title="Res Gestae", text_url="https://la.wikisource.org/wiki/Res_gestae"),
        ],
        fetch,
    )

    assert [(t.label, t.title, t.origin, t.text) for t in gathered.texts] == [
        ("F1", "Suetônio", "excerpt", "trecho escolhido"),
        ("F2", "Res Gestae", "url", "obra inteira"),
    ]
    assert fetched == ["https://la.wikisource.org/wiki/Res_gestae"]


def test_skips_citations_without_open_text_and_says_why() -> None:
    gathered = gather_sources(
        [
            citation(title="Wikidata", text_url=None, text_license=None),
            citation(title="Livro moderno", text_url=None, text_license=None, excerpt="trecho com direitos"),
            citation(title="Suetônio", text_url=None),
            citation(title="Res Gestae", excerpt="texto"),
        ],
        lambda url: pytest.fail("nada deveria ser baixado"),
    )

    assert [t.title for t in gathered.texts] == ["Res Gestae"]
    assert gathered.skipped == (
        "Wikidata: sem licença do texto",
        "Livro moderno: sem licença do texto",
        "Suetônio: sem endereço do texto nem trecho colado",
    )


def test_fails_when_no_citation_has_usable_text() -> None:
    with pytest.raises(SourceTextError, match=r"nenhuma fonte com texto utilizável \(Wikidata: sem licença do texto\)"):
        gather_sources([citation(title="Wikidata", text_url=None, text_license=None)], lambda url: "")


def test_fails_instead_of_cutting_texts_that_are_too_long() -> None:
    long_text = "a" * (sources.MAX_TOTAL_CHARS + 1)

    with pytest.raises(SourceTextError, match="grande demais"):
        gather_sources([citation(excerpt=long_text)], lambda url: "")


def test_the_same_source_cited_twice_is_read_once() -> None:
    source_id = uuid4()

    gathered = gather_sources([citation(source_id=source_id), citation(source_id=source_id)], lambda url: "texto")

    assert len(gathered.texts) == 1
