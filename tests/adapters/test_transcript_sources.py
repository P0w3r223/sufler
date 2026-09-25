"""Testy adapterów źródła transkryptu (``transcript_sources``, M3 produkcyjnie, ADR 0009, B1).

``HttpxGraphTranscriptSource`` testujemy na ``httpx.MockTransport`` (jak reszta adapterów Graph
w repo) — bez sieci i bez Azure. ``vtt_to_text`` to czysta funkcja parsująca, testowana wprost.
Klucz: rozwiązanie spotkania po ``joinWebUrl``, wybór NAJNOWSZEGO transkryptu, fail-loud przy braku.
"""

from __future__ import annotations

import httpx
import pytest

from sufler.adapters.outbound.transcript_sources import (
    HttpxGraphTranscriptSource,
    InMemoryTranscriptSource,
    vtt_to_text,
)

_JOIN_URL = "https://teams.microsoft.com/l/meetup-join/19%3ameeting_abc/0"
_MEETING_ID = "MSoxMg-meeting-id"

_VTT = """WEBVTT

NOTE recording

1
00:00:01.000 --> 00:00:04.000
<v Anna Kowalska>Zamrażamy kontrakt v1.</v>

2
00:00:05.000 --> 00:00:07.500
<v Jan Nowak>Zgoda, przygotuję draft.</v>
"""


# --- vtt_to_text (czysty parser) -------------------------------------------


def test_vtt_to_text_keeps_speaker_labels_and_drops_metadata():
    text = vtt_to_text(_VTT)

    assert text == "Anna Kowalska: Zamrażamy kontrakt v1.\nJan Nowak: Zgoda, przygotuję draft."
    # Metadane VTT nie przeciekają do tekstu dla streszczenia.
    assert "WEBVTT" not in text
    assert "-->" not in text
    assert "<v" not in text


def test_vtt_to_text_line_without_speaker_kept_verbatim():
    text = vtt_to_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\nBez mówcy tekst")

    assert text == "Bez mówcy tekst"


def test_vtt_to_text_empty_input_is_empty():
    assert vtt_to_text("WEBVTT\n\n") == ""


# --- HttpxGraphTranscriptSource (na MockTransport) --------------------------


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _transcripts_page(*ids_with_dates: tuple[str, str]) -> dict:
    return {"value": [{"id": tid, "createdDateTime": created} for tid, created in ids_with_dates]}


def test_fetch_by_join_url_resolves_lists_and_parses(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization", "")
        path = request.url.path
        if path.endswith("/onlineMeetings"):
            # Filtr po joinWebUrl zwraca id spotkania (params dekoduje %-encoding).
            assert (request.url.params.get("$filter") or "").startswith("JoinWebUrl eq")
            return httpx.Response(200, json={"value": [{"id": _MEETING_ID}]})
        if path.endswith("/transcripts"):
            return httpx.Response(200, json=_transcripts_page(("t-1", "2026-07-20T10:00:00Z")))
        if path.endswith("/content"):
            assert request.url.params.get("$format") == "text/vtt"
            return httpx.Response(200, text=_VTT)
        raise AssertionError(f"nieoczekiwana ścieżka: {path}")

    source = HttpxGraphTranscriptSource(lambda: "tok-123", client=_client(handler))
    text = source.fetch(_JOIN_URL)

    assert "Anna Kowalska: Zamrażamy kontrakt v1." in text
    assert seen["auth"] == "Bearer tok-123"


def test_fetch_by_meeting_id_skips_resolution():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/transcripts"):
            return httpx.Response(200, json=_transcripts_page(("t-1", "2026-07-20T10:00:00Z")))
        return httpx.Response(200, text=_VTT)

    source = HttpxGraphTranscriptSource(lambda: "tok", client=_client(handler))
    source.fetch(_MEETING_ID)  # id, nie URL → brak /onlineMeetings

    assert not any(p.endswith("/onlineMeetings") for p in calls)
    assert any(p.endswith("/transcripts") for p in calls)


def test_fetch_picks_latest_transcript_by_created_date():
    requested: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transcripts"):
            return httpx.Response(
                200,
                json=_transcripts_page(
                    ("older", "2026-07-19T09:00:00Z"),
                    ("newer", "2026-07-20T18:00:00Z"),
                ),
            )
        # Zapamiętaj, o który transcript id poszła treść.
        requested["id"] = request.url.path.split("/transcripts/")[1].split("/content")[0]
        return httpx.Response(200, text=_VTT)

    source = HttpxGraphTranscriptSource(lambda: "tok", client=_client(handler))
    source.fetch(_MEETING_ID)

    assert requested["id"] == "newer"


def test_fetch_no_transcript_raises_clear_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/transcripts"):
            return httpx.Response(200, json={"value": []})
        raise AssertionError("nie powinno pobierać treści bez transkryptu")

    source = HttpxGraphTranscriptSource(lambda: "tok", client=_client(handler))
    with pytest.raises(ValueError, match="nie ma jeszcze transkryptu"):
        source.fetch(_MEETING_ID)


def test_fetch_unknown_join_url_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"value": []})

    source = HttpxGraphTranscriptSource(lambda: "tok", client=_client(handler))
    with pytest.raises(ValueError, match="nie znaleziono spotkania"):
        source.fetch(_JOIN_URL)


def test_fetch_forbidden_maps_to_actionable_value_error():
    # 403 (najczęściej: admin nie nadał zakresu transkryptu) → CZYTELNY ValueError ze wskazówką,
    # nie surowy HTTPStatusError (który omijałby przyjazny komunikat routera /notatka, ADR 0043).
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"message": "Forbidden"}})

    source = HttpxGraphTranscriptSource(lambda: "tok", client=_client(handler))
    with pytest.raises(ValueError, match="Graph odrzucił pobranie transkryptu"):
        source.fetch(_MEETING_ID)


# --- InMemoryTranscriptSource (lokalny harness) ----------------------------


def test_in_memory_returns_mapped_transcript():
    source = InMemoryTranscriptSource({"ref-1": "treść"})
    assert source.fetch("ref-1") == "treść"


def test_in_memory_missing_ref_raises_key_error():
    source = InMemoryTranscriptSource({})
    with pytest.raises(KeyError):
        source.fetch("brak")
