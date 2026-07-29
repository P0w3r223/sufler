"""Testy pętli pollingu Teams (delegowany Graph) na WSTRZYKNIĘTYM porcie klienta.

Klient Graph (``GraphChannelClient``) i handler są wstrzykiwane, więc pełną orkiestrację
I/O testujemy atrapą portu — bez ``httpx``, bez MSAL, bez sieci. Atrapa nagrywa wywołania
``post_reply``; handler nagrywa ``conversation_id``. Sedno: WIELOTURA (druga odpowiedź w
tym samym wątku po już wysłanej), self-skip, dedup, watermark startowy i odporność pętli na
błąd pojedynczego kanału.
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from workmate.adapters.inbound.teams_graph import poller as poller_module
from workmate.adapters.inbound.teams_graph.poller import ChannelPoller
from workmate.core.ports.llm import Attachment

_ME = "me-bot"
_STARTUP = "2024-01-01T11:00:00Z"
_NOW = datetime(2024, 1, 1, 11, 5, 0, tzinfo=timezone.utc)
_ACTIVE_IDLE = timedelta(hours=24)


def _raw(
    *,
    msg_id: str,
    created: str,
    text: str = "hej",
    reply_to: str | None = None,
    sender_id: str = "u-anna",
) -> dict[str, Any]:
    raw: dict[str, Any] = {
        "id": msg_id,
        "messageType": "message",
        "createdDateTime": created,
        "body": {"contentType": "text", "content": text},
        "from": {"user": {"id": sender_id, "displayName": "Anna"}},
    }
    if reply_to is not None:
        raw["replyToId"] = reply_to
    return raw


class FakeGraphClient:
    """Atrapa portu ``GraphChannelClient`` — skryptowana per RUNDA pollingu.

    Runda = jedno wywołanie ``list_root_messages`` (raz na ``_poll_channel``). ``rounds`` to
    lista ``{"roots": [...], "replies": {root_id: [...]}}``; po ostatniej rundzie zwraca ją
    w kółko. Nagrywa ``post_reply`` jako krotki (do asercji, co i gdzie wysłano).
    """

    def __init__(self, rounds: list[dict[str, Any]], *, me_id: str = _ME) -> None:
        self._rounds = rounds
        self._me_id = me_id
        self._idx = -1
        self.posted: list[tuple[str, str, str, str]] = []
        self.refresh_calls = 0

    async def refresh_auth(self) -> None:
        self.refresh_calls += 1

    async def get_me_id(self) -> str:
        return self._me_id

    def _current(self) -> dict[str, Any]:
        return self._rounds[min(self._idx, len(self._rounds) - 1)]

    async def list_root_messages(
        self, team_id: str, channel_id: str, *, top: int
    ) -> list[dict[str, Any]]:
        self._idx += 1
        return list(self._current()["roots"])

    async def list_replies(
        self, team_id: str, channel_id: str, root_id: str, *, top: int
    ) -> list[dict[str, Any]]:
        return list(self._current().get("replies", {}).get(root_id, []))

    async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
        self.posted.append((team_id, channel_id, root_id, text))


class RecordingHandler:
    """Atrapa handlera: nagrywa wywołania i zwraca ustaloną odpowiedź (``None`` = brak)."""

    def __init__(self, reply: str | None = "odp") -> None:
        self._reply = reply
        self.calls: list[dict[str, str]] = []

    async def __call__(self, message: Any, conversation_id: str) -> str | None:
        self.calls.append(
            {
                "text": message.text,
                "conversation_id": conversation_id,
                "root": message.thread_root_id,
            }
        )
        return self._reply


def _make_poller(
    client: Any,
    handle: Any,
    *,
    watch: tuple[tuple[str, str], ...] = (("team", "chan"),),
    state: dict[str, Any] | None = None,
    clock: Any = lambda: _NOW,
    stop: Any = None,
) -> tuple[ChannelPoller, list[int]]:
    persist_calls: list[int] = []
    poller = ChannelPoller(
        client,
        handle,
        watch=watch,
        state={} if state is None else state,
        persist=lambda _s: persist_calls.append(1),
        top_roots=5,
        top_replies=5,
        poll_interval=0,
        active_idle=_ACTIVE_IDLE,
        clock=clock,
        stop=stop,
    )
    return poller, persist_calls


# --- _poll_channel: pojedyncza runda ----------------------------------------


def test_poll_channel_replies_to_new_root_with_thread_conversation_id():
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z", text="ustalenia")
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-1", "odp")]
    assert handler.calls[0]["conversation_id"] == "team/chan/root-1"
    assert "root-1" in poller._state["replied"]


def test_poll_channel_skips_own_message_delegated_mode():
    """Tryb delegowany: bot JEST userem — nie odpowiada na własny post (brak pętli)."""
    own = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z", sender_id=_ME)
    client = FakeGraphClient([{"roots": [own], "replies": {}}])
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == []
    assert handler.calls == []


def test_poll_channel_marks_replied_even_when_reply_is_empty():
    """Pusta odpowiedź handlera → nic nie wysyłamy, ale wiadomość liczymy jako obsłużoną."""
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler(reply=None)
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == []
    assert "root-1" in poller._state["replied"]


# --- _poll_channel: WIELOTURA i dedup przez rundy ---------------------------


def test_poll_channel_multiturn_replies_again_to_later_reply_in_same_thread():
    """REGRESJA (sedno drzwi): po odpowiedzi na post root, KOLEJNA odpowiedź w tym wątku

    także dostaje odpowiedź. Spike milkł tu, bo bramkował po ``root.lastModifiedDateTime``;
    watermark per wątek sprawia, że runda 2 widzi nową odpowiedź.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z", text="pytanie 1")
    reply2 = _raw(msg_id="r-2", created="2024-01-01T11:45:00Z", reply_to="root-1", text="pytanie 2")
    client = FakeGraphClient(
        [
            {"roots": [root], "replies": {}},
            {"roots": [root], "replies": {"root-1": [reply2]}},
        ]
    )
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))  # runda 1: post root
    asyncio.run(poller._poll_channel("team", "chan", _ME))  # runda 2: nowa odpowiedź

    assert client.posted == [
        ("team", "chan", "root-1", "odp"),
        ("team", "chan", "root-1", "odp"),
    ]
    assert [c["text"] for c in handler.calls] == ["pytanie 1", "pytanie 2"]


def test_poll_channel_does_not_reprocess_same_message_next_round():
    """Bez nowej aktywności druga runda nie odpowiada powtórnie (watermark + dedup)."""
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    client = FakeGraphClient(
        [
            {"roots": [root], "replies": {}},
            {"roots": [root], "replies": {"root-1": []}},
        ]
    )
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))
    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-1", "odp")]  # tylko raz


# --- _poll_channel: semantyka „co najmniej raz" przy błędzie wysyłki --------


class _FailingPostClient(FakeGraphClient):
    async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
        raise RuntimeError("Graph 500")


def test_poll_channel_does_not_advance_state_when_post_reply_fails():
    """Watermark przesuwamy DOPIERO po wysłaniu — awaria wysyłki nie zapisuje postępu,

    a wiadomość nie trafia do ``replied`` (następna runda ją ponowi: at-least-once).
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    client = _FailingPostClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    with pytest.raises(RuntimeError, match="Graph 500"):
        asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert poller._state["replied"] == []  # nie oznaczono jako odpisane
    # Stan kanału nietknięty — nowy watermark nie został utrwalony.
    assert poller._state["channels"]["team/chan"] == {
        "since_roots": _STARTUP,
        "threads": {},
    }


class _HttpError(Exception):
    """Wyjątek udający ``httpx.HTTPStatusError`` — niesie ``response.status_code`` (duck typing)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"HTTP {status}")
        self.response = types.SimpleNamespace(status_code=status)


class _PermanentFailPostClient(FakeGraphClient):
    async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
        raise _HttpError(403)  # np. brak zgody ChannelMessage.Send na tym kanale


def test_poll_channel_swallows_permanent_post_failure_to_avoid_looping_channel():
    """Trwały (4xx) błąd wysyłki jest połknięty: wiadomość liczy się jako obsłużona i

    watermark idzie do przodu — jedna niewysyłalna wiadomość nie zapętla kanału ani LLM.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    client = _PermanentFailPostClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    # W przeciwieństwie do błędu przejściowego — NIE propaguje.
    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == []  # wysyłka nieudana
    assert "root-1" in poller._state["replied"]  # ale uznana za obsłużoną
    # Watermark przesunięty — kolejna runda tu nie wróci (brak zapętlenia).
    assert poller._state["channels"]["team/chan"]["since_roots"] == "2024-01-01T11:30:00Z"


def test_is_permanent_classifies_only_non_429_4xx_as_permanent():
    assert poller_module._is_permanent(_HttpError(403)) is True
    assert poller_module._is_permanent(_HttpError(404)) is True
    assert poller_module._is_permanent(_HttpError(429)) is False  # przejściowy → ponów
    assert poller_module._is_permanent(_HttpError(500)) is False  # przejściowy → ponów
    assert poller_module._is_permanent(RuntimeError("bez response")) is False


# --- _poll_channel: materializacja załączników ------------------------------


class _FakeMaterializer:
    """Atrapa materializera — nagrywa wywołania, zwraca ustalone załączniki."""

    def __init__(self, attachments: tuple[Attachment, ...]) -> None:
        self._attachments = attachments
        self.calls: list[tuple[str, str, str]] = []

    async def materialize(self, team_id: str, channel_id: str, msg: Any) -> tuple[Attachment, ...]:
        self.calls.append((team_id, channel_id, msg.id))
        return self._attachments


def test_poll_channel_materializes_attachment_refs_and_passes_them_to_handler():
    """Wiadomość z referencją pliku → materializer wywołany, handler dostaje bajty."""
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z", text="zobacz plik")
    root["attachments"] = [
        {"contentType": "reference", "contentUrl": "https://sp/f.pdf", "name": "f.pdf"}
    ]
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    seen: dict[str, Any] = {}

    async def handler(message: Any, conversation_id: str) -> str:
        seen["attachments"] = message.attachments
        seen["text"] = message.text
        return "odp"

    attachments = (Attachment("document", "application/pdf", "f.pdf", data_base64="QQ=="),)
    materializer = _FakeMaterializer(attachments)
    poller, _ = _make_poller(client, handler)
    poller._materializer = materializer  # wstrzyknięcie (jak w app.py)
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert materializer.calls == [("team", "chan", "root-1")]
    assert seen["attachments"] == attachments
    assert seen["text"] == "zobacz plik"


def test_poll_channel_skips_materializer_when_no_attachment_refs():
    """Bez referencji materializer NIE jest wołany (brak zbędnego I/O)."""
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    materializer = _FakeMaterializer(())
    poller, _ = _make_poller(client, RecordingHandler("odp"))
    poller._materializer = materializer
    poller._seed(_STARTUP)

    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert materializer.calls == []


# --- _seed + _mark_replied --------------------------------------------------


def test_seed_initializes_startup_watermark_and_is_idempotent():
    state: dict[str, Any] = {}
    poller, _ = _make_poller(FakeGraphClient([]), RecordingHandler(), state=state)

    poller._seed(_STARTUP)

    assert state["replied"] == []
    assert state["channels"]["team/chan"] == {
        "since_roots": _STARTUP,
        "threads": {},
    }

    # Ponowny seed nie nadpisuje zapisanej pozycji (idempotencja).
    poller._seed("2024-01-01T23:00:00Z")
    assert state["channels"]["team/chan"]["since_roots"] == _STARTUP


def test_mark_replied_caps_history_to_last_500():
    state: dict[str, Any] = {}
    poller, _ = _make_poller(FakeGraphClient([]), RecordingHandler(), state=state)
    poller._seed(_STARTUP)

    for i in range(600):
        poller._mark_replied(f"m{i}")

    replied = state["replied"]
    assert len(replied) == 500
    assert replied[0] == "m100"  # najstarsze 100 wypadło
    assert replied[-1] == "m599"


# --- run(): watermark startowy + odporność pętli ----------------------------


class _StopLoop(BaseException):
    """Wyrwanie z ``while True`` — BaseException omija ``except Exception`` w pętli."""


def _break_after(monkeypatch, *, calls: int) -> None:
    """Podmień ``asyncio.sleep`` w module pollera tak, by po N wywołaniach zerwać pętlę."""
    counter = {"n": 0}

    async def fake_sleep(_seconds: float) -> None:
        counter["n"] += 1
        if counter["n"] >= calls:
            raise _StopLoop

    monkeypatch.setattr(poller_module.asyncio, "sleep", fake_sleep)


def test_run_startup_watermark_skips_backlog_before_launch(monkeypatch):
    """Watermark startowy = ``clock()`` → post sprzed uruchomienia nie dostaje odpowiedzi."""
    backlog = _raw(msg_id="old", created="2024-01-01T09:00:00Z")  # sprzed startu (11:00)
    client = FakeGraphClient([{"roots": [backlog], "replies": {}}])
    handler = RecordingHandler("odp")
    clock = lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    poller, persist_calls = _make_poller(client, handler, clock=clock)
    _break_after(monkeypatch, calls=2)  # sleep(1) kanału + sleep(poll_interval) rundy

    with pytest.raises(_StopLoop):
        asyncio.run(poller.run())

    assert client.posted == []  # backlog przed startem pominięty
    assert persist_calls == [1]  # jedna runda utrwalona
    assert client.refresh_calls >= 2  # przed pętlą + w pętli


def test_run_isolates_single_channel_failure_from_the_rest(monkeypatch):
    """Błąd jednego kanału nie kładzie pozostałych ani całej pętli."""
    good_root = _raw(msg_id="g", created="2024-01-01T11:30:00Z")

    class _TwoChannelClient:
        def __init__(self) -> None:
            self.posted: list[tuple[str, str, str, str]] = []
            self.refresh_calls = 0

        async def refresh_auth(self) -> None:
            self.refresh_calls += 1

        async def get_me_id(self) -> str:
            return _ME

        async def list_root_messages(
            self, team_id: str, channel_id: str, *, top: int
        ) -> list[dict[str, Any]]:
            if team_id == "bad":
                raise RuntimeError("Graph niedostępny")
            return [good_root]

        async def list_replies(
            self, team_id: str, channel_id: str, root_id: str, *, top: int
        ) -> list[dict[str, Any]]:
            return []

        async def post_reply(self, team_id: str, channel_id: str, root_id: str, text: str) -> None:
            self.posted.append((team_id, channel_id, root_id, text))

    client = _TwoChannelClient()
    handler = RecordingHandler("odp")
    clock = lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    poller, _ = _make_poller(client, handler, watch=(("bad", "c1"), ("good", "c2")), clock=clock)
    _break_after(monkeypatch, calls=3)  # 2× sleep kanału + sleep rundy

    with pytest.raises(_StopLoop):
        asyncio.run(poller.run())

    # Dobry kanał odpowiedział mimo wyjątku ze złego.
    assert client.posted == [("good", "c2", "g", "odp")]


def test_run_finishes_current_round_then_exits_on_stop(monkeypatch):
    """Graceful shutdown (R1): stop kończy pętlę PO utrwaleniu rundy (dokończ → zapisz → wróć)."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(poller_module.asyncio, "sleep", _no_sleep)  # drzemki kanału natychmiastowe

    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z", text="x")
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler("odp")
    clock = lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)  # noqa: E731
    stop = asyncio.Event()
    poller, persist_calls = _make_poller(client, handler, clock=clock, stop=stop)

    original_persist = poller._persist

    def _persist_then_stop(s: Any) -> None:
        original_persist(s)  # utrwal rundę…
        stop.set()  # …i dopiero potem zasygnalizuj stop

    poller._persist = _persist_then_stop  # type: ignore[method-assign]

    asyncio.run(asyncio.wait_for(poller.run(), timeout=5))

    assert persist_calls == [1]  # dokładnie jedna runda utrwalona, potem wyjście
    assert client.posted == [("team", "chan", "root-1", "odp")]  # runda dokończona przed stopem
