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
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT_S = 10.0

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
) -> bool:
    """Wyślij alert na webhook. Zwraca, czy się udało — NIGDY nie rzuca.

    Brak ``webhook_url`` oznacza świadomą rezygnację z alertowania (konfiguracja opcjonalna),
    więc nie jest błędem i nie generuje ruchu sieciowego.
    """
    if not webhook_url:
        return False

    ladunek: dict[str, Any] = {
        "text": f"{_PREFIKS.get(waga, '')} **{tytul}**\n\n{tresc}".strip(),
        "tytul": tytul,
        "tresc": tresc,
        "waga": waga,
        "zrodlo": "powiadomienia-teams",
    }
    try:
        # `follow_redirects` JAWNIE: httpx domyślnie NIE podąża za przekierowaniem, a webhooki
        # potrafią je zwracać (podniesienie http→https, zmiana adresu Power Automate, reverse
        # proxy). Bez tego 301/302 kończyło się „sukcesem", którego nikt nigdy nie dostał.
        if client is not None:
            odpowiedz = client.post(webhook_url, json=ladunek, follow_redirects=True)
        else:
            odpowiedz = httpx.post(
                webhook_url, json=ladunek, timeout=_TIMEOUT_S, follow_redirects=True
            )
        # Próg 300, nie 400: po podążeniu za przekierowaniami kod 3xx oznacza, że łańcuch się nie
        # domknął — czyli alert NIE dotarł. Cichy zanik jedynego kanału niezależnego od AAD jest
        # najgorszą możliwą awarią tego modułu, więc traktujemy to jako niepowodzenie.
        if odpowiedz.status_code >= 300:
            # Sam URL bywa sekretem (bywa w nim token) — logujemy kod, nigdy adresu.
            logger.warning("Webhook alertu nie przyjął wiadomości: %s", odpowiedz.status_code)
            return False
        return True
    except Exception:
        logger.warning("Nie udało się wysłać alertu %r", tytul, exc_info=True)
        return False
