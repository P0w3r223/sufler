"""Testy sync źródła treści WĄTKU kanału (``HttpxGraphThreadSource``, F2, ADR 0048).

Bez sieci: ``httpx.MockTransport`` przechwytuje GET root wątku i ``.../replies`` (jak reszta
adapterów Graph w repo). Klucz F2: złożenie ``Nazwa: treść`` CHRONOLOGICZNIE (root + odpowiedzi
wg ``createdDateTime``), roster REALNYCH nadawców (unikalne ``displayName``, bez botów/„?"),
pomijanie nie-treści (system/skasowane/puste), 404 na root → ``ThreadRootGone``, zły
``external_id`` → ``ValueError``, paginacja ``@odata.nextLink``.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from workmate.adapters.outbound.graph_thread_source import HttpxGraphThreadSource
from workmate.core.errors import ThreadRootGone

_EXTERNAL_ID = "team-1/chan-1/root-1"


def _msg(
    *,
    name: str | None = "Anna",
    text: str = "treść",
    created: str = "2024-01-01T10:00:00Z",
    message_type: str = "message",
    deleted: str | None = None,
) -> dict[str, Any]:
    """Surowy słownik wiadomości Graph — human z ``displayName`` (albo bot, gdy ``name=None``)."""
    raw: dict[str, Any] = {
        "messageType": message_type,
        "createdDateTime": created,
        "body": {"contentType": "html", "content": f"<p>{text}</p>"},
    }
    if deleted is not None:
        raw["deletedDateTime"] = deleted
    if name is None:
        raw["from"] = {"application": {"id": "app-x"}}  # bot/aplikacja: brak from.user.displayName
    else:
        raw["from"] = {"user": {"displayName": name}}
    return raw


def _source(handler, *, sleeps=None) -> HttpxGraphThreadSource:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpxGraphThreadSource(
        client, lambda: "tok", sleep=(sleeps.append if sleeps is not None else (lambda _s: None))
    )


def test_fetch_assembles_root_and_replies_chronologically():
    seen: dict[str, object] = {}
    root = _msg(name="Anna", text="pytanie root", created="2024-01-01T10:00:00Z")
    # Odpowiedzi CELOWO poza kolejnością — źródło ma je posortować po createdDateTime.
    replies = [
        _msg(name="Bob", text="bob mowi", created="2024-01-01T11:00:00Z"),
        _msg(name="Anna", text="anna dodaje", created="2024-01-01T10:30:00Z"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/replies"):
            seen["top"] = request.url.params.get("$top")
            return httpx.Response(200, json={"value": replies})
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=root)

    content = _source(handler).fetch(_EXTERNAL_ID)

    assert content.text == "Anna: pytanie root\nAnna: anna dodaje\nBob: bob mowi"
    # Uczestnicy = unikalni realni nadawcy w kolejności pierwszego wystąpienia.
    assert content.participants == ("Anna", "Bob")
    assert seen["auth"] == "Bearer tok"
    assert seen["top"] == "50"


def test_fetch_skips_system_deleted_and_empty_messages():
    root = _msg(name="Anna", text="root", created="2024-01-01T10:00:00Z")
    replies = [
        _msg(name="Zdarzenie", message_type="systemEventMessage", created="2024-01-01T10:10:00Z"),
        _msg(name="Skasowany", deleted="2024-01-01T10:20:00Z", created="2024-01-01T10:15:00Z"),
        _msg(name="Pusty", text="", created="2024-01-01T10:25:00Z"),  # pusto po strip HTML
        _msg(name="Bob", text="realna", created="2024-01-01T10:30:00Z"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/replies"):
            return httpx.Response(200, json={"value": replies})
        return httpx.Response(200, json=root)

    content = _source(handler).fetch(_EXTERNAL_ID)

    assert content.text == "Anna: root\nBob: realna"
    assert content.participants == ("Anna", "Bob")
    # Nadawcy nie-treści nie trafiają ani do tekstu, ani do rostera.
    assert "Zdarzenie" not in content.text
    assert "Skasowany" not in content.text
    assert "Pusty" not in content.text


def test_fetch_excludes_bot_without_displayname_from_participants():
    # Nadawca bez ``displayName`` (bot/aplikacja) dostaje etykietę „?" w tekście, ale NIE wchodzi
    # do rostera uczestników (allowlista nazwisk = tylko realni ludzie z metadanych Graph).
    root = _msg(name="Anna", text="root", created="2024-01-01T10:00:00Z")
    replies = [_msg(name=None, text="odpowiedź bota", created="2024-01-01T10:30:00Z")]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/replies"):
            return httpx.Response(200, json={"value": replies})
        return httpx.Response(200, json=root)

    content = _source(handler).fetch(_EXTERNAL_ID)

    assert content.participants == ("Anna",)
    assert "?" not in content.participants
    assert "?: odpowiedź bota" in content.text  # linia jest, ale nadawca poza rosterem


def test_fetch_404_on_root_raises_thread_root_gone():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NotFound"}})

    with pytest.raises(ThreadRootGone):
        _source(handler).fetch(_EXTERNAL_ID)


def test_fetch_bad_external_id_raises_value_error():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - nie wywołany
        raise AssertionError("zły external_id nie powinien uderzyć w sieć")

    with pytest.raises(ValueError, match="external_id"):
        _source(handler).fetch("team/chan")  # tylko 2 części


def test_fetch_paginates_replies_via_next_link():
    root = _msg(name="Anna", text="root", created="2024-01-01T10:00:00Z")
    page1 = _msg(name="Anna", text="strona jeden", created="2024-01-01T10:30:00Z")
    page2 = _msg(name="Bob", text="strona dwa", created="2024-01-01T11:00:00Z")
    next_url = (
        "https://graph.microsoft.com/v1.0/teams/team-1/channels/chan-1/messages/root-1/replies"
        "?$skiptoken=NEXT"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/replies"):
            if "skiptoken" in str(request.url):  # druga strona (@odata.nextLink)
                return httpx.Response(200, json={"value": [page2]})
            return httpx.Response(200, json={"value": [page1], "@odata.nextLink": next_url})
        return httpx.Response(200, json=root)

    content = _source(handler).fetch(_EXTERNAL_ID)

    # Obie strony odpowiedzi złożone (paginacja domknięta).
    assert content.text == "Anna: root\nAnna: strona jeden\nBob: strona dwa"
    assert content.participants == ("Anna", "Bob")


@pytest.mark.parametrize("status", [403, 401, 500])
def test_fetch_maps_http_error_to_value_error_with_scope_hint(status: int):
    # 403 (brak zakresu odczytu kanału), 401 (wygasły token), 5xx (awaria Graph) NIE mogą uciec
    # jako surowy HTTPStatusError — router łapie tylko WorkMateError/ValueError/KeyError, więc
    # bez mapowania degradacja by go ominęła (defekt kodu zamiast czytelnej odmowy). Lustro
    # ``HttpxGraphTranscriptSource``: mapujemy na ValueError z podpowiedzią o zakresach.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"code": "Denied"}})

    with pytest.raises(ValueError, match="zakres"):
        _source(handler).fetch(_EXTERNAL_ID)


def test_fetch_retries_on_429_then_succeeds():
    calls = {"n": 0}
    sleeps: list[float] = []
    root = _msg(name="Anna", text="root", created="2024-01-01T10:00:00Z")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/replies"):
            return httpx.Response(200, json={"value": []})
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=root)

    content = _source(handler, sleeps=sleeps).fetch(_EXTERNAL_ID)

    assert calls["n"] == 2  # pierwszy 429, drugi OK
    assert sleeps == [0.0]  # odczekaliśmy wg Retry-After
    assert content.text == "Anna: root"
