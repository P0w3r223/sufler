"""Jedyne miejsce, w którym powstaje klient HTTP — reguła granic 11.

`client._checked_host` sprawdza host w **adresie**, a to kontrola napisu: nie widzi, dokąd
naprawdę idzie gniazdo. `httpx.Client` budowany bez transportu i z domyślnym `trust_env=True`
bierze `HTTPS_PROXY`/`ALL_PROXY` ze środowiska (httpx 0.28.1, `_client.py`:
`allow_env_proxies = trust_env and transport is None`), więc każde żądanie — razem z nagłówkiem
`Authorization`, a token niesie PESEL — wychodziło przez host, którego nikt nie porównał
z `ALLOWED_HOSTS`. `SSL_CERT_FILE` i `SSL_CERT_DIR` podmieniały przy tym zestaw zaufanych
certyfikatów, więc „TLS z weryfikacją certyfikatu" znaczyło „z weryfikacją wobec tego, co
wskaże zmienna środowiskowa".

Trzy różne mechanizmy zamykają trzy różne dziury i warto ich nie mylić — przegląd kodu
z 2026-09-07 wykazał, że pierwsza wersja tego modułu przypisywała wszystko jednemu:

1. **Proxy ze środowiska** znika, bo transport jest podawany **zawsze**. To podanie transportu,
   a nie `trust_env`, zeruje `_mounts`; `trust_env=False` na kliencie zostaje jako drugi zamek
   (wyłącza też `.netrc`), ale sam z siebie niczego by tu nie zmienił.
2. **Podmianę zaufanych certyfikatów** blokuje `trust_env=False` przekazane
   `httpx.HTTPTransport`, bo to ono trafia do `create_ssl_context`. Ta jedna wartość jest całym
   mechanizmem połowy „TLS z weryfikacją certyfikatu" z §B — stąd jej własny test.
3. **Obcy host** odbija się od `AllowedHostsTransport`, czyli od warstwy, w której otwiera się
   połączenie, a nie od tej, w której składa się adres.

uzupelnienie-01.md §B mówi „połączenia tylko do hostów `dane.biznes.gov.pl`
i `test-dane.biznes.gov.pl`", a §E chce na to testu z zaślepką DNS
(`tests/resilience/test_egress_allowlist.py`).

Świadoma konsekwencja: na maszynie za firmowym proxy albo z własnym CA w `SSL_CERT_FILE`
narzędzie teraz odmówi połączenia zamiast po cichu przepuścić token przez pośrednika. To jest
zachowanie, którego żąda §B; gdyby kiedyś miało się zmienić, zmienia się tutaj i tylko tutaj.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:  # `httpx2` przychodzi z opcjonalną extrą `asystent` — typy tak, import nie.
    import httpx2

from .config import ALLOWED_HOSTS, MODEL_ALLOWED_HOSTS
from .errors import UntrustedLinkError


def refuse_foreign_host(scheme: str, host: str | None, allowed: frozenset[str]) -> None:
    """Jedyna kopia reguły „dokąd wolno wyjść". Dwa transporty, dwie biblioteki, jedno zdanie.

    Reguła stoi osobno od klas transportu, bo stosy HTTP są teraz dwa (`httpx` dla CEIDG,
    `httpx2` pod SDK modelu), a ten projekt dwukrotnie odmówił trzymania dwóch kopii reguły
    krytycznej dla bezpieczeństwa — raz przy `safe()` w ADR-0009, raz przy polityce wyjścia.
    """
    if scheme != "https" or host not in allowed:
        raise UntrustedLinkError(
            f"Odmowa połączenia z {host or scheme!r}: dozwolone są tylko hosty "
            + ", ".join(sorted(allowed))
        )


class AllowedHostsTransport(httpx.BaseTransport):
    """Ostatnia bramka przed gniazdem: żądanie do obcego hosta nie opuszcza procesu.

    Kontrola siedzi w transporcie, a nie w kliencie, bo tędy przechodzi **każde** żądanie —
    także takie, które powstałoby z pominięciem `CeidgClient` (przekierowanie, nowe wywołanie
    dopisane w przyszłości, biblioteka doklejona do tego samego klienta). Dublowanie kontroli
    z `client._checked_host` jest zamierzone: tamta daje czytelny komunikat i odmawia, zanim
    żądanie zajmie miejsce w limiterze; ta jest niezależna od tego, czy ktoś o niej pamiętał.
    """

    def __init__(self, inner: httpx.BaseTransport, allowed: frozenset[str]) -> None:
        self._inner = inner
        self._allowed = allowed

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        refuse_foreign_host(request.url.scheme, request.url.host, self._allowed)
        return self._inner.handle_request(request)

    def close(self) -> None:
        self._inner.close()


def build_http_client(
    *, transport: httpx.BaseTransport | None = None, allowed: frozenset[str] = ALLOWED_HOSTS
) -> httpx.Client:
    """Klient HTTP z polityką wyjścia §B. `transport` podstawiają testy (ADR-0006).

    `allowed` zawęża bramkę do jednego hosta: `build_deps` zna już środowisko i jego host, więc
    podczas pracy na teście `links.next` wskazujący produkcję nie ma prawa wyjść. Wartość
    domyślna obejmuje oba hosty, żeby wywołanie bez argumentów pozostało bezpieczne.

    Atrapa z testu też jest opakowana — inaczej testy jeździłyby po innej ścieżce niż produkcja,
    a to właśnie ta różnica ukrywała problem do 2026-09-07: `tests/support.py` podawał transport,
    więc httpx pomijał proxy ze środowiska i defekt nie miał jak się pokazać.
    """
    inner = transport or httpx.HTTPTransport(verify=True, trust_env=False)
    return httpx.Client(
        transport=AllowedHostsTransport(inner, allowed),
        trust_env=False,
        follow_redirects=False,
    )


def build_model_http_client(
    *,
    transport: httpx2.BaseTransport | None = None,
    allowed: frozenset[str] = MODEL_ALLOWED_HOSTS,
) -> httpx2.Client:
    """Klient dla SDK modelu — druga biblioteka, ta sama polityka (ADR-0011, decyzja 1).

    `anthropic` 1.x stoi na `httpx2`, osobnej dystrybucji od przypiętego `httpx`. Importujemy ją
    **wewnątrz funkcji**, żeby narzędzie startowało na maszynie bez opcjonalnej extry `asystent`:
    precedens to `config.read_token_from_keyring`. §B zabrania importów dynamicznych w sensie
    `importlib.import_module(nazwa_z_danych)`; import literalnej nazwy w ciele funkcji to co
    innego i pakiet już tak robi.

    Zmierzone na `httpx2` 2.12.0, a nie założone z `httpx` 0.28 (ADR-0011 nazwał to ryzykiem
    numer jeden): `trust_env=False` na `HTTPTransport` blokuje `SSL_CERT_FILE` tak samo, a
    rozwiązywanie nazw idzie przez `socket.getaddrinfo`, więc zaślepka DNS z `tests/conftest.py`
    obejmuje także ruch do modelu.

    Typy `httpx2` widzi wyłącznie `mypy` (`TYPE_CHECKING`), więc moduł importuje się także tam,
    gdzie extra `asystent` nie została zainstalowana.
    """
    import httpx2

    class _ModelGate(httpx2.BaseTransport):
        """Bliźniak `AllowedHostsTransport` dla drugiego stosu. Klasa powstaje w środku funkcji,
        bo jej klasa bazowa żyje w bibliotece, której moduł nie ma prawa importować na wierzchu."""

        def __init__(self, inner: httpx2.BaseTransport, allowed: frozenset[str]) -> None:
            self._inner = inner
            self._allowed = allowed

        def handle_request(self, request: httpx2.Request) -> httpx2.Response:
            refuse_foreign_host(request.url.scheme, request.url.host, self._allowed)
            return self._inner.handle_request(request)

        def close(self) -> None:
            self._inner.close()

    inner: httpx2.BaseTransport = transport or httpx2.HTTPTransport(verify=True, trust_env=False)
    return httpx2.Client(
        transport=_ModelGate(inner, allowed),
        trust_env=False,
        follow_redirects=False,
    )
