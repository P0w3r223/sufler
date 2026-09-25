"""Alerty eksploatacyjne wysyłane kanałem NIEZALEŻNYM od Microsoft Graph.

Niezależność jest tu całym sensem. Najważniejszy alert — „utraciłem sesję, potrzebny `--login`" —
powstaje dokładnie w chwili, gdy token do Graph przestał działać, więc wysłanie go przez Teams
tożsamością bota jest niemożliwe. Zwykły webhook HTTPS nie ma z AAD nic wspólnego i przejdzie.

Kanał jest celowo opisany jako „dowolny endpoint przyjmujący POST z JSON", a nie jako konkretny
produkt: Microsoft wycofuje klasyczne Office 365 connectors na rzecz Workflows (Power Automate),
więc wiązanie się z jednym formatem szybko by się zdezaktualizowało. Ładunek zawiera zarówno
``text`` (czytelne dla Teams i Slacka), jak i pola strukturalne dla innych odbiorców.

Wysyłka jest ZAWSZE best-effort: alert, który wywraca usługę, jest gorszy niż brak alertu.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10.0
# Ponowienia dotyczą wyłącznie alertów, po których ktoś ma coś zrobić (BLAD/KRYTYCZNY).
# Trzy próby z rosnącym odstępem odsiewają typową awarię bramki webhooka (502/504,
# przeciążenie Power Automate), nie zamieniając kanału alertowego w generator ruchu.
_PROBY_WAZNEGO = 3
_ODSTEP_PONOWIENIA_S = 2.0

# Wagi alertów — sterują tylko prefiksem w treści, żeby odbiorca widział rangę bez czytania całości.
INFO = "info"
BLAD = "blad"
KRYTYCZNY = "krytyczny"

_PREFIKS = {INFO: "ℹ️", BLAD: "⚠️", KRYTYCZNY: "🚨"}


def send_alert(
    webhook_url: str,
    tytul: str,
    tresc: str,
    *,
    waga: str = BLAD,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Wyślij alert na webhook. Zwraca, czy się udało — NIGDY nie rzuca.

    Brak ``webhook_url`` oznacza świadomą rezygnację z alertowania (konfiguracja opcjonalna),
    więc nie jest błędem i nie generuje ruchu sieciowego.

    Alerty wagi ``BLAD`` i ``KRYTYCZNY`` są PONAWIANE. Powód nie jest kosmetyczny: tym kanałem
    idą zdania, po których ktoś ma pójść sprawdzić grafik klienta („zapis przerwany w połowie",
    „zapis cudzą tożsamością", „nieudany zapis po potwierdzeniu"), a nadawca nie sprawdzał nawet
    wyniku wysyłki. Jedno 502 z bramki webhooka kasowało cały ślad zdarzenia — tor zapasowy
    (licznik w cotygodniowym podsumowaniu) też bywał pusty, bo ``prune_terminal`` zdążył wpis
    usunąć. ``INFO`` nie jest ponawiane: jego utrata nic nie kosztuje, a pobudki bywają częste.
    """
    if not webhook_url:
        return False

    proby = _PROBY_WAZNEGO if waga in (BLAD, KRYTYCZNY) else 1
    for numer in range(1, proby + 1):
        if _jedna_proba(webhook_url, tytul, tresc, waga, client):
            return True
        if numer < proby:
            sleep(_ODSTEP_PONOWIENIA_S * numer)
    if proby > 1:
        # Ostatnia deska: log. Jeśli i on nie zostanie przeczytany, zdarzenie przepada — dlatego
        # zgłoszenia zastanych zapisów `APPLYING` powtarzają się przy każdym przebiegu.
        logger.error("Nie udało się dostarczyć alertu %r po %d próbach", tytul, proby)
    return False


class _UkryjAdresWebhooka(logging.Filter):
    """Filtr logu: pełny adres webhooka alertów → sam host z ``/<ukryte>`` w miejscu ścieżki."""

    def __init__(self, host: str) -> None:
        super().__init__()
        self._wzorzec = re.compile(rf"(https?://{re.escape(host)})[^\s\"']*", re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        tresc = record.getMessage()
        ukryta = self._wzorzec.sub(r"\1/<ukryte>", tresc)
        if ukryta != tresc:
            record.msg, record.args = ukryta, None
        return True


def ukryj_adres_w_logach(webhook_url: str) -> None:
    """Nie pozwól, żeby adres webhooka alertów (token w ścieżce) trafił do logu kontenera.

    httpx loguje KAŻDE żądanie na poziomie INFO razem z pełnym URL-em — a adres webhooka Discorda
    czy Power Automate niesie token w ścieżce. Do 0.2.25 był to wyciek pewny i udokumentowany
    („znane usterki" 0.2.19): token leżał w `docker logs` i w każdej ich kopii. Wyciszenie
    loggera httpx w całości zabrałoby też logi żądań do Graph, które są jedyną diagnostyką
    dławienia — dlatego maskujemy wyłącznie adresy na hoście webhooka, ścieżkę w całości
    (także po przekierowaniu w obrębie hosta, gdzie ścieżka jest inna niż w konfiguracji).
    Wywołanie wielokrotne nie dubluje filtra.
    """
    if not webhook_url:
        return
    host = httpx.URL(webhook_url).host
    if not host:
        return
    httpx_logger = logging.getLogger("httpx")
    if any(isinstance(f, _UkryjAdresWebhooka) for f in httpx_logger.filters):
        return
    httpx_logger.addFilter(_UkryjAdresWebhooka(host))


_KODY_PRZEKIEROWANIA = frozenset({301, 302, 303, 307, 308})
_MAX_PRZEKIEROWAN = 3


def _post_z_przekierowaniem(
    webhook_url: str, ladunek: dict[str, Any], client: httpx.Client | None
) -> httpx.Response:
    """POST z przekierowaniami obsłużonymi RĘCZNIE — tylko w obrębie TEGO SAMEGO hosta, po HTTPS.

    Do 0.2.25 stało tu ``follow_redirects=True`` (audyt 2026-09-08, pkt 4). httpx przy 307/308
    powtarza wtedy POST z PEŁNĄ treścią alertu pod adres z nagłówka ``Location`` — dowolny, także
    obcy i nieszyfrowany — omijając kontrolę schematu, którą ``Settings.validate`` robi wyłącznie
    dla adresu z konfiguracji. Przekierowanie na inny host jest tu więc odmową z czytelnym
    ostrzeżeniem: właściwą reakcją jest poprawienie ``ALERT_WEBHOOK_URL``, nie ślepe podążanie.

    Przekierowań w obrębie hosta nadal potrzebujemy (zmiana ścieżki Power Automate, reverse
    proxy) — dlatego nie zwykłe ``follow_redirects=False``. Każdy kod 3xx powtarza POST: odbiorca
    webhooka i tak nie przyjmie GET-a, a zamiana metody (tak robią przeglądarki przy 301–303)
    zamieniłaby przekierowanie w cichą utratę alertu.
    """
    url = httpx.URL(webhook_url)
    for _ in range(_MAX_PRZEKIEROWAN + 1):
        if client is not None:
            odpowiedz = client.post(str(url), json=ladunek, follow_redirects=False)
        else:
            odpowiedz = httpx.post(
                str(url), json=ladunek, timeout=_TIMEOUT_S, follow_redirects=False
            )
        lokalizacja = odpowiedz.headers.get("location")
        if odpowiedz.status_code not in _KODY_PRZEKIEROWANIA or not lokalizacja:
            return odpowiedz
        cel = url.join(lokalizacja)
        if cel.scheme != "https" or cel.host.lower() != url.host.lower():
            # Host celu nie jest sekretem (sekret siedzi w ścieżce) i jest dokładnie tym, czego
            # operator potrzebuje, żeby zdecydować, czy nowy adres jest prawowity.
            logger.warning(
                "Webhook alertu przekierowuje na inny adres (%s://%s) — NIE wysyłam tam treści "
                "alertu. Jeśli adres jest prawidłowy, wpisz go do ALERT_WEBHOOK_URL.",
                cel.scheme,
                cel.host,
            )
            return odpowiedz
        url = cel
    return odpowiedz


def _jedna_proba(
    webhook_url: str, tytul: str, tresc: str, waga: str, client: httpx.Client | None
) -> bool:
    ladunek: dict[str, Any] = {
        "text": f"{_PREFIKS.get(waga, '')} **{tytul}**\n\n{tresc}".strip(),
        "tytul": tytul,
        "tresc": tresc,
        "waga": waga,
        "zrodlo": "powiadomienia-teams",
    }
    try:
        odpowiedz = _post_z_przekierowaniem(webhook_url, ladunek, client)
        # Próg 300, nie 400: kod 3xx po wyczerpaniu dozwolonych przekierowań oznacza, że łańcuch
        # się nie domknął — czyli alert NIE dotarł. Cichy zanik jedynego kanału niezależnego od
        # AAD jest najgorszą możliwą awarią tego modułu, więc traktujemy to jako niepowodzenie.
        if odpowiedz.status_code >= 300:
            # Sam URL bywa sekretem (bywa w nim token) — logujemy kod, nigdy adresu.
            logger.warning("Webhook alertu nie przyjął wiadomości: %s", odpowiedz.status_code)
            return False
        return True
    except Exception:
        logger.warning("Nie udało się wysłać alertu %r", tytul, exc_info=True)
        return False
