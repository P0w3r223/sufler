"""Adaptery źródła transkryptu (port ``TranscriptSource``, Faza 2 M3 / ADR 0009, B1).

``HttpxGraphTranscriptSource`` — realne pobranie transkryptu spotkania z Microsoft
Graph — jest ZABRAMKOWANE dostępem do Azure/M365: wymaga rejestracji aplikacji w
Entra ID + delegowanych uprawnień ``OnlineMeetingTranscript.Read.All`` (treść) oraz
``OnlineMeetings.Read`` (rozwiązanie spotkania po ``joinWebUrl``). Kod jest kompletny i
przetestowany na atrapie ``httpx`` — brakuje TYLKO nadania uprawnień przez admina i
ponownej zgody device-code; wtedy weryfikacja to jedno wywołanie ``sufler-meeting
--source graph`` (patrz ``docs/how-to/meeting-transcript-live-smoke.md``).

``InMemoryTranscriptSource`` pozwala uruchomić i zademonstrować cały przepływ M3
lokalnie (wklejony transkrypt), bez Azure — do testów i lokalnego harnessu.

Wzorzec adaptera jak ``graph_shift_source``: synchroniczny ``httpx`` z dostawcą tokenu,
stronicowanie ``@odata.nextLink`` z twardym capem (ucięcie = TWARDY błąd, nie cichy brak),
import ``httpx`` leniwy (extra ``teams-graph``). Treść transkryptu to DANE, nie polecenia —
parser wydobywa czysty tekst, nie wykonuje niczego z zawartości.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"
# Cap stron listowania transkryptów — backstop przed pętlą. Spotkanie ma zwykle 1 transkrypt;
# 20 stron to zapas ponad potrzebę, a wyczerpanie capu jest anomalią (patrz fail-loud niżej).
_MAX_TRANSCRIPT_PAGES = 20
# Znaczniki WebVTT do usunięcia przy zamianie na czysty tekst: nagłówek, indeks i czas cue.
_VTT_TIMESTAMP = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s*-->")
_VTT_CUE_INDEX = re.compile(r"^\d+$")
# Znacznik mówcy w VTT Teams: ``<v Anna Kowalska>treść</v>`` → zachowujemy etykietę mówcy.
_VTT_VOICE_OPEN = re.compile(r"<v\s+([^>]+)>")
_VTT_TAG = re.compile(r"</?[^>]+>")


def _odata_string(value: str) -> str:
    """Zacytuj wartość na literał string OData (pojedyncze cudzysłowy podwajane)."""
    return "'" + value.replace("'", "''") + "'"


def vtt_to_text(vtt: str) -> str:
    """Zamień transkrypt WebVTT (format Graph) na czysty tekst do streszczenia.

    Usuwa nagłówek ``WEBVTT``, indeksy cue, znaczniki czasu i puste linie; z każdej
    linii treści ściąga tag mówcy ``<v Nazwa>`` do prefiksu ``Nazwa: …`` (kontekst dla
    streszczenia), a pozostałe znaczniki HTML/VTT wycina. Wynik to złączone wypowiedzi.

    Format jest KONTROLOWANYM wejściem (Graph), ale treść wypowiedzi jest NIEZAUFANA —
    zwracamy sam tekst, bez interpretacji zawartych w nim instrukcji (zasada M3/ADR 0009).
    """
    lines: list[str] = []
    for raw in vtt.splitlines():
        line = raw.strip()
        if not line or line == "WEBVTT" or line.startswith("NOTE"):
            continue
        if _VTT_TIMESTAMP.match(line) or _VTT_CUE_INDEX.match(line):
            continue
        speaker_match = _VTT_VOICE_OPEN.search(line)
        speaker = speaker_match.group(1).strip() if speaker_match else ""
        text = _VTT_TAG.sub("", line).strip()
        if not text:
            continue
        lines.append(f"{speaker}: {text}" if speaker else text)
    return "\n".join(lines)


class HttpxGraphTranscriptSource:
    """``TranscriptSource`` czytający transkrypt spotkania z Microsoft Graph (delegowany).

    ``meeting_ref`` to ALBO ``joinWebUrl`` spotkania (zaczyna się od ``http`` → rozwiązanie
    po filtrze OData, wymaga ``OnlineMeetings.Read``), ALBO gotowe ``id`` spotkania. Wybieramy
    NAJNOWSZY transkrypt (po ``createdDateTime``) i pobieramy jego treść jako WebVTT.

    ``client`` można wstrzyknąć (test na ``httpx.MockTransport``); w produkcji ``None`` → własny,
    krótkotrwały ``httpx.Client`` (przebieg jest jednorazowy, koszt zestawienia bez znaczenia).
    """

    def __init__(
        self,
        token: Callable[[], str],
        *,
        client: Any = None,
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._client = client
        self._timeout = timeout

    def fetch(self, meeting_ref: str) -> str:
        if self._client is not None:
            return self._fetch(self._client, meeting_ref)
        import httpx

        with httpx.Client(timeout=self._timeout) as client:
            return self._fetch(client, meeting_ref)

    def _fetch(self, client: Any, meeting_ref: str) -> str:
        import httpx

        try:
            meeting_id = self._resolve_meeting_id(client, meeting_ref)
            transcript_id = self._latest_transcript_id(client, meeting_id)
            vtt = self._transcript_content(client, meeting_id, transcript_id)
        except httpx.HTTPStatusError as exc:
            # Zamień surowy błąd HTTP Graph na CZYTELNY komunikat domenowy (``ValueError`` łapią
            # wołający: CLI i router /notatka). Najczęstszy przypadek to 403 = admin nie nadał
            # zakresu transkryptu — bez tego user dostałby generyczne „błąd wewnętrzny".
            raise ValueError(
                f"Graph odrzucił pobranie transkryptu (HTTP {exc.response.status_code}) — sprawdź "
                "zakresy admina (OnlineMeetingTranscript.Read.All, OnlineMeetings.Read) oraz "
                "uprawnienia do spotkania."
            ) from exc
        text = vtt_to_text(vtt)
        if not text.strip():
            raise ValueError(
                f"transkrypt {transcript_id} spotkania {meeting_id} jest pusty po parsowaniu "
                "(brak wypowiedzi) — nie ma czego streszczać."
            )
        logger.info("Pobrano transkrypt spotkania %s (%d znaków tekstu).", meeting_id, len(text))
        return text

    def _resolve_meeting_id(self, client: Any, meeting_ref: str) -> str:
        """Rozwiąż ``meeting_ref`` do ``id`` spotkania: ``joinWebUrl`` przez filtr albo wprost."""
        if not meeting_ref.lower().startswith("http"):
            return meeting_ref  # już jest id spotkania
        url = f"{_GRAPH}/me/onlineMeetings"
        params = {"$filter": f"JoinWebUrl eq {_odata_string(meeting_ref)}"}
        body = self._get(client, url, params=params)
        items = [item for item in body.get("value", []) if isinstance(item, dict)]
        if not items:
            raise ValueError(
                f"nie znaleziono spotkania dla joinWebUrl {meeting_ref!r} (Graph zwrócił pustą "
                "listę) — sprawdź URL i czy jesteś organizatorem/uczestnikiem spotkania."
            )
        return str(items[0]["id"])

    def _latest_transcript_id(self, client: Any, meeting_id: str) -> str:
        """Id NAJNOWSZEGO transkryptu spotkania; brak transkryptów = TWARDY błąd."""
        transcripts: list[dict[str, Any]] = []
        url: str | None = f"{_GRAPH}/me/onlineMeetings/{meeting_id}/transcripts"
        for _ in range(_MAX_TRANSCRIPT_PAGES):
            if not url:
                break
            body = self._get(client, url)
            transcripts.extend(t for t in body.get("value", []) if isinstance(t, dict))
            url = body.get("@odata.nextLink")
        if url:
            raise ValueError(
                f"lista transkryptów spotkania {meeting_id} jest NIEKOMPLETNA: przerwano po "
                f"{_MAX_TRANSCRIPT_PAGES} stronach — podnieś cap."
            )
        if not transcripts:
            raise ValueError(
                f"spotkanie {meeting_id} nie ma jeszcze transkryptu (nie nagrano/nie przetworzono) "
                "— transkrypt pojawia się dopiero po zakończeniu i przetworzeniu nagrania."
            )
        latest = max(transcripts, key=lambda t: str(t.get("createdDateTime", "")))
        return str(latest["id"])

    def _transcript_content(self, client: Any, meeting_id: str, transcript_id: str) -> str:
        """Pobierz treść transkryptu jako WebVTT (``$format=text/vtt``)."""
        url = f"{_GRAPH}/me/onlineMeetings/{meeting_id}/transcripts/{transcript_id}/content"
        response = client.get(url, params={"$format": "text/vtt"}, headers=self._auth_headers())
        response.raise_for_status()
        return str(response.text)

    def _get(self, client: Any, url: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        response = client.get(url, params=params, headers=self._auth_headers())
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}


class InMemoryTranscriptSource:
    """Transkrypty w pamięci (lokalny harness/test) — mapowanie ``meeting_ref`` → treść."""

    def __init__(self, transcripts: dict[str, str]) -> None:
        self._transcripts = transcripts

    def fetch(self, meeting_ref: str) -> str:
        try:
            return self._transcripts[meeting_ref]
        except KeyError as exc:
            raise KeyError(f"brak transkryptu dla spotkania: {meeting_ref!r}") from exc
