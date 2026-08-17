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
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from workmate.adapters.inbound.teams_graph import poller as poller_module
from workmate.adapters.inbound.teams_graph.poller import ChannelPoller
from workmate.core.ports.llm import Attachment

_ME = "me-bot"
_STARTUP = "2024-01-01T11:00:00Z"
_NOW = datetime(2024, 1, 1, 11, 5, 0, tzinfo=UTC)
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
    dead_letters: Any = None,
    persist: Any = None,
) -> tuple[ChannelPoller, list[int]]:
    persist_calls: list[int] = []

    def _count(_s: Any) -> None:
        persist_calls.append(1)

    poller = ChannelPoller(
        client,
        handle,
        watch=watch,
        state={} if state is None else state,
        persist=_count if persist is None else persist,
        top_roots=5,
        top_replies=5,
        poll_interval=0,
        active_idle=_ACTIVE_IDLE,
        clock=clock,
        stop=stop,
        dead_letters=dead_letters,
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
    """Watermark przesuwamy DOPIERO po wysłaniu — awaria wysyłki nie zapisuje postępu.

    PIERWSZA porażka zostawia wiadomość do ponowienia (``replied`` puste, licznik prób na 1):
    at-least-once dla błędu przejściowego jest zachowane. Granicę stawia dopiero licznik —
    patrz sondy pętli restartów niżej.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    client = _FailingPostClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(client, handler)
    poller._seed(_STARTUP)

    with pytest.raises(RuntimeError, match="Graph 500"):
        asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert poller._state["replied"] == []  # nie odpisana → następna runda ponowi
    assert poller._state["attempts"] == {"root-1": 1}  # …ale wiemy, że to już była próba
    # Stan kanału nietknięty — nowy watermark nie został utrwalony.
    assert poller._state["channels"]["team/chan"] == {
        "since_roots": _STARTUP,
        "threads": {},
    }


class _CrashingHandler:
    """Handler, który ginie tak jak proces przy OOM na bombie w załączniku."""

    def __init__(self, *, boom_times: int = 99) -> None:
        self.calls = 0
        self._boom_times = boom_times

    async def __call__(self, message: Any, conversation_id: str) -> str | None:
        self.calls += 1
        if self.calls <= self._boom_times:
            raise MemoryError("wykonawca wyczerpał pamięć na materializacji załącznika")
        return "odp"


def _restart(state: dict[str, Any], handler: Any, root: dict[str, Any]) -> ChannelPoller:
    """Nowy poller nad TYM SAMYM stanem — odwzorowanie restartu procesu po ubiciu."""
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]), handler, state=state
    )
    return poller


def test_attempt_counter_is_persisted_before_handling_so_a_crash_cannot_loop_forever():
    """Regresja: awaria POJEDYNCZEJ wiadomości nie może stać się TRWAŁĄ pętlą restartów.

    Materializacja załącznika biegła przed jakimkolwiek zapisem, a stan utrwalał się dopiero po
    całej rundzie — proces ubity przez OOM brał po restarcie tę samą wiadomość i ginął ponownie,
    w kółko, blokując wszystkie kanały. Sonda: licznik prób DOCIERA do magazynu przed obsługą,
    więc po ``_MAX_ATTEMPTS`` podejściach wiadomość znika ze strumienia.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    handler = _CrashingHandler()
    utrwalone: list[dict[str, int]] = []
    state: dict[str, Any] = {}
    poller = ChannelPoller(
        FakeGraphClient([{"roots": [root], "replies": {}}]),
        handler,
        watch=(("team", "chan"),),
        state=state,
        persist=lambda s: utrwalone.append(dict(s["attempts"])),
        top_roots=5,
        top_replies=5,
        poll_interval=0,
        active_idle=_ACTIVE_IDLE,
        clock=lambda: _NOW,
    )
    poller._seed(_STARTUP)

    with pytest.raises(MemoryError):
        asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert utrwalone == [{"root-1": 1}]  # próba DOTARŁA do magazynu, zanim padła obsługa

    # Restart 1: licznik na 1 < _MAX_ATTEMPTS → ponawiamy (i znowu giniemy).
    with pytest.raises(MemoryError):
        asyncio.run(_restart(state, handler, root)._poll_channel("team", "chan", _ME))
    assert handler.calls == 2

    # Restart 2: licznik wysycony → odpuszczamy BEZ wołania handlera. Pętla domknięta.
    asyncio.run(_restart(state, handler, root)._poll_channel("team", "chan", _ME))

    assert handler.calls == 2  # bez licznika: 3, i tak w kółko aż do końca świata
    assert state["replied"] == ["root-1"]  # odpuszczona na stałe
    assert state["attempts"] == {}  # sprawa zamknięta — licznik nie puchnie


def test_a_transient_failure_is_retried_instead_of_silently_dropping_the_message():
    """Cena za domknięcie pętli ma obejmować TYLKO awarie powtarzalne.

    Regresja: samo „oznacz odpisane przed obsługą" kasowało wiadomość przy KAŻDYM przejściowym
    błędzie (503 na ``hostedContents``, timeout LLM, 429 przy wysyłce) — rozmówca nie dostawał
    odpowiedzi nigdy, a w logu zostawał traceback bez wskazania, która to wiadomość.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    handler = _CrashingHandler(boom_times=1)  # pierwsza próba pada, druga przechodzi
    state: dict[str, Any] = {}
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]), handler, state=state
    )
    poller._seed(_STARTUP)

    with pytest.raises(MemoryError):
        asyncio.run(poller._poll_channel("team", "chan", _ME))

    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    poller_2, _ = _make_poller(client, handler, state=state)
    asyncio.run(poller_2._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-1", "odp")]  # odpowiedź jednak dotarła
    assert state["attempts"] == {}
    assert state["replied"] == ["root-1"]


class _RecordingDeadLetters:
    """Atrapa kwarantanny wiadomości — nagrywa wpisy albo (``boom``) odmawia zapisu."""

    def __init__(self, *, boom: bool = False) -> None:
        self.records: list[dict[str, Any]] = []
        self._boom = boom

    def record(self, **wpis: Any) -> None:
        if self._boom:
            raise OSError("kwarantanna nie przyjmuje zapisu")
        self.records.append(wpis)


def _disk_full(_state: dict[str, Any]) -> None:
    """Zapis stanu, który przestał przechodzić W TRAKCIE pracy (pełny dysk, remount ``ro``)."""
    raise OSError(28, "No space left on device")


def test_a_failed_state_write_does_not_burn_an_attempt():
    """Regresja (HIGH): awaria zapisu stanu zamieniała głośny crash w CICHĄ utratę wiadomości.

    ``_persist`` licznika prób stał POZA blokiem ``try`` obsługi, więc gdy wolumen stanu przestawał
    przyjmować zapis (``require_writable`` sonduje tylko start), licznik rósł w PAMIĘCI, wyjątek
    łapał ``except`` per kanał, a po dwóch rundach wiadomość dostawała ``_mark_replied`` i znikała
    na stałe — mimo że obsługa nie ruszyła ANI RAZU, a log mówił „po 2 nieudanych próbach obsługi".
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    handler = RecordingHandler("odp")
    state: dict[str, Any] = {}
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]),
        handler,
        state=state,
        persist=_disk_full,
    )
    poller._seed(_STARTUP)

    for _ in range(poller_module._MAX_ATTEMPTS + 1):
        with pytest.raises(OSError):
            asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert handler.calls == []  # obsługa nie ruszyła ani razu…
    assert state["attempts"] == {}  # …więc żadna próba nie została naliczona
    assert state["replied"] == []  # …i wiadomość NIE zniknęła ze strumienia

    # Dysk wraca: ta sama wiadomość jest wciąż do obsłużenia, bo awaria wolumenu jej nie obciążyła.
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    poller_2, _ = _make_poller(client, handler, state=state)
    asyncio.run(poller_2._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-1", "odp")]


def test_abandoned_message_is_quarantined_with_enough_context_to_find_it():
    """Decyzja właściciela (ADR 0069): porzucona treść od człowieka ma zostawić ŚLAD.

    Do tej pory po wyczerpaniu prób wiadomość znikała bez wpisu gdziekolwiek poza logiem —
    podczas gdy ścieżka WYJŚCIOWA (notifier) miała dead-letter od ADR 0067 §2. Wpis niesie to,
    czym wiadomość da się ODNALEŹĆ w Teams (kanał, wątek, id, nadawca) i powód porażki — nie treść.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    kwarantanna = _RecordingDeadLetters()
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]),
        _CrashingHandler(),
        dead_letters=kwarantanna,
    )
    poller._seed(_STARTUP)

    for _ in range(poller_module._MAX_ATTEMPTS):
        with pytest.raises(MemoryError):
            asyncio.run(poller._poll_channel("team", "chan", _ME))
    asyncio.run(poller._poll_channel("team", "chan", _ME))  # runda porzucenia

    (wpis,) = kwarantanna.records
    assert wpis["door"] == "teams_graph"
    assert wpis["message_id"] == "root-1"
    assert wpis["channel"] == "team/chan"
    assert wpis["thread_root_id"] == "root-1"
    assert wpis["sender"] == "u-anna"
    assert wpis["attempts"] == poller_module._MAX_ATTEMPTS
    assert "MemoryError" in wpis["reason"]  # powód OSTATNIEJ porażki, nie „coś padło"
    assert poller._state["replied"] == ["root-1"]  # dopiero PO wpisie znika ze strumienia


def test_quarantine_after_a_crash_says_it_does_not_know_the_reason():
    """Powód żyje w pamięci procesu, licznik na dysku — po restarcie wpis ma to PRZYZNAĆ.

    To jest ścieżka, dla której licznik w ogóle powstał: obsługa ubiła proces, więc żaden
    ``except`` nie zdążył zapisać powodu. Wymyślony powód byłby gorszy niż jawne „nie wiem".
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    handler = _CrashingHandler()
    kwarantanna = _RecordingDeadLetters()
    state: dict[str, Any] = {}
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]), handler, state=state
    )
    poller._seed(_STARTUP)
    for _ in range(poller_module._MAX_ATTEMPTS):
        with pytest.raises(MemoryError):
            asyncio.run(poller._poll_channel("team", "chan", _ME))

    po_restarcie, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]),
        handler,
        state=state,
        dead_letters=kwarantanna,
    )
    asyncio.run(po_restarcie._poll_channel("team", "chan", _ME))

    assert kwarantanna.records[0]["reason"] == poller_module._NO_REASON


def test_quarantine_refusal_does_not_stop_the_channel(caplog):
    """Regresja (HIGH): odmowa magazynu blokowała CAŁY kanał, nie tylko trującą wiadomość.

    Wyjątek z ``record`` wychodził poza pętlę wiadomości, więc przy trwałej awarii magazynu
    (``SQLITE_CORRUPT``, ``disk I/O error``, ``SQLITE_BUSY`` ponad ``busy_timeout``) zdrowa
    wiadomość stojąca za trującą nie wchodziła do handlera ANI RAZU — kanał przestawał odpowiadać
    komukolwiek. Kwarantanna trzyma WSKAŹNIK do wiadomości (treść zostaje w Teams), więc jej brak
    kosztuje ślad; blokada kosztowała wszystkie odpowiedzi kanału.
    """
    trujaca = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    zdrowa = _raw(msg_id="root-2", created="2024-01-01T11:31:00Z")
    client = FakeGraphClient([{"roots": [trujaca, zdrowa], "replies": {}}])
    state: dict[str, Any] = {"attempts": {"root-1": poller_module._MAX_ATTEMPTS}}
    handler = RecordingHandler("odp")
    poller, _ = _make_poller(
        client, handler, state=state, dead_letters=_RecordingDeadLetters(boom=True)
    )
    poller._seed(_STARTUP)

    with caplog.at_level("ERROR"):
        asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-2", "odp")]  # zdrowa obsłużona
    assert state["replied"] == ["root-1", "root-2"]  # trująca porzucona, kanał idzie dalej
    komunikat = " ".join(rec.getMessage() for rec in caplog.records)
    assert "root-1" in komunikat  # log jest wtedy zapasowym rejestrem — niesie komplet wpisu
    assert "team/chan" in komunikat
    assert "u-anna" in komunikat  # nadawca, żeby dało się odnaleźć wiadomość bez tabeli


def test_prune_drops_the_long_forgotten_entry_not_the_one_in_flight(monkeypatch):
    """Docstring ``_prune_attempts`` obiecuje obcinanie NAJSTARSZYCH — kod tego nie robił.

    Wstawienie na istniejący klucz nie przesuwa go na koniec słownika, więc pod sufitem przycięty
    zostawał wpis wiadomości aktualnie w obiegu (najdawniej WSTAWIONY), a nie ten porzucony.
    """
    monkeypatch.setattr(poller_module, "_ATTEMPTS_CAP", 2)
    state: dict[str, Any] = {}
    poller, _ = _make_poller(FakeGraphClient([]), RecordingHandler(), state=state)
    poller._seed(_STARTUP)
    state["attempts"].update({"w-obiegu": 1, "sierota-a": 1, "sierota-b": 1})

    poller._record_attempt("w-obiegu", 1)

    assert set(state["attempts"]) == {"sierota-b", "w-obiegu"}
    assert state["attempts"]["w-obiegu"] == 2


def test_quarantine_reason_carries_the_type_not_the_exception_payload():
    """Powód ma opisywać awarię, nie przemycać treści rozmówcy do ``events.db``.

    ``repr(exc)`` niósł cały ładunek wyjątku — a wyjątki walidacji potrafią wypisać wartość
    wejściową. Zapisujemy typ i pierwszą linię komunikatu; sufit znaków w adapterze ogranicza
    rozmiar, nie rodzaj.
    """

    class _WalidacjaZTrescia(RuntimeError):
        pass

    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")

    async def handler(_message: Any, _conversation_id: str) -> str:
        raise _WalidacjaZTrescia(
            "1 validation error for Turn\nbody\n  input_value='tajna treść rozmówcy'"
        )

    kwarantanna = _RecordingDeadLetters()
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]), handler, dead_letters=kwarantanna
    )
    poller._seed(_STARTUP)
    for _ in range(poller_module._MAX_ATTEMPTS):
        with pytest.raises(_WalidacjaZTrescia):
            asyncio.run(poller._poll_channel("team", "chan", _ME))
    asyncio.run(poller._poll_channel("team", "chan", _ME))

    reason = kwarantanna.records[0]["reason"]
    assert reason == "_WalidacjaZTrescia: 1 validation error for Turn"
    assert "tajna treść" not in reason


def test_seed_drops_a_counter_that_is_not_a_number():
    """``{"attempts": {"root-1": "abc"}}`` wywracało ``int(...)`` w każdej rundzie, trwale."""
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    state: dict[str, Any] = {"attempts": {"root-1": "abc", "root-9": True, "root-8": 1}}
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    poller, _ = _make_poller(client, RecordingHandler("odp"), state=state)

    poller._seed(_STARTUP)
    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-1", "odp")]
    assert state["attempts"] == {"root-8": 1}  # czytelny wpis zostaje, nieczytelne kasujemy


def test_seed_repairs_null_branches_instead_of_looping_on_attribute_error():
    """``setdefault`` łapie BRAK klucza, nie ``null`` pod kluczem — a to drugie było trwałe.

    Plik stanu poprawny składniowo, ale z ``"attempts": null`` (ręczna edycja, starszy zapis),
    wywracał każdą rundę każdego kanału na ``AttributeError``; wyjściem było skasowanie pliku.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    state: dict[str, Any] = {
        "replied": None,
        "attempts": None,
        "channels": {"team/chan": {"since_roots": None, "threads": None}},
    }
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    poller, _ = _make_poller(client, RecordingHandler("odp"), state=state)

    poller._seed(_STARTUP)
    asyncio.run(poller._poll_channel("team", "chan", _ME))

    assert client.posted == [("team", "chan", "root-1", "odp")]
    assert state["replied"] == ["root-1"]
    assert state["attempts"] == {}


def test_failed_handling_names_the_message_in_the_log(caplog):
    """Log ma nazwać wiadomość i powiedzieć, czy będzie ponowienie — inaczej operator zgaduje."""
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    poller, _ = _make_poller(
        FakeGraphClient([{"roots": [root], "replies": {}}]), _CrashingHandler()
    )
    poller._seed(_STARTUP)

    with caplog.at_level("ERROR"), pytest.raises(MemoryError):
        asyncio.run(poller._poll_channel("team", "chan", _ME))

    komunikat = " ".join(rec.getMessage() for rec in caplog.records)
    assert "root-1" in komunikat
    assert "ponowi" in komunikat


def test_heartbeat_beats_per_channel_not_once_per_round(monkeypatch):
    """Regresja: puls raz na rundę WSZYSTKICH kanałów gasł na ścieżce szczęśliwej.

    Runda zawiera pełne tury agenta na każdym kanale, więc jej długość mierzy ruch, nie
    żywotność procesu — przy ``--max-age 180`` kontener bywał ``unhealthy``, mimo że pracował
    poprawnie, a wtedy healthcheck przestaje być czytany.
    """
    root_a = _raw(msg_id="a", created="2024-01-01T11:30:00Z")
    root_b = _raw(msg_id="b", created="2024-01-01T11:30:00Z")

    class _TwoChannelClient(FakeGraphClient):
        async def list_root_messages(
            self, team_id: str, channel_id: str, *, top: int
        ) -> list[dict[str, Any]]:
            return [root_a] if channel_id == "c1" else [root_b]

    beats: list[int] = []
    poller = ChannelPoller(
        _TwoChannelClient([{"roots": [], "replies": {}}]),
        RecordingHandler("odp"),
        watch=(("t", "c1"), ("t", "c2")),
        state={},
        persist=lambda _s: None,
        top_roots=5,
        top_replies=5,
        poll_interval=0,
        active_idle=_ACTIVE_IDLE,
        clock=lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC),
        heartbeat=lambda: beats.append(1),
    )
    _break_after(monkeypatch, calls=3)  # 2× sleep kanału + sleep rundy

    with pytest.raises(_StopLoop):
        asyncio.run(poller.run())

    # 2 wiadomości + 2 kanały + 1 runda; bez poprawki był DOKŁADNIE jeden puls na rundę.
    assert len(beats) >= 4


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
    clock = lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)  # noqa: E731
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
    clock = lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)  # noqa: E731
    poller, _ = _make_poller(client, handler, watch=(("bad", "c1"), ("good", "c2")), clock=clock)
    _break_after(monkeypatch, calls=3)  # 2× sleep kanału + sleep rundy

    with pytest.raises(_StopLoop):
        asyncio.run(poller.run())

    # Dobry kanał odpowiedział mimo wyjątku ze złego.
    assert client.posted == [("good", "c2", "g", "odp")]


def test_run_survives_a_read_only_state_volume_and_stops_the_beat(monkeypatch):
    """Regresja: zapis stanu po rundzie stał POZA ``try``, więc kładł CAŁĄ pętlę.

    Cofanie licznika prób obiecywało „runda kończy się jak każda inna awaria infrastruktury", ale
    ``run()`` padał w tej samej rundzie — a nadzorca wznawiał proces prosto w ``require_writable``.
    Pętla ma przeżyć (nic nie ginie: licznik cofnięty, watermark nieprzesunięty) i PRZESTAĆ bić
    puls, bo proces, który nie utrwala postępu, nie jest zdrowy tylko dlatego, że stoi.
    """
    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z")
    beats: list[int] = []
    poller = ChannelPoller(
        FakeGraphClient([{"roots": [root], "replies": {}}]),
        RecordingHandler("odp"),
        watch=(("team", "chan"),),
        state={},
        persist=_disk_full,
        top_roots=5,
        top_replies=5,
        poll_interval=0,
        active_idle=_ACTIVE_IDLE,
        clock=lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC),
        heartbeat=lambda: beats.append(1),
    )
    _break_after(monkeypatch, calls=5)  # kilka rund: pętla ma je przeżyć, nie paść na pierwszej

    with pytest.raises(_StopLoop):
        asyncio.run(poller.run())

    assert beats == []  # ani jednego pulsu — healthcheck zobaczy wolumen bez zapisu
    assert poller._state["replied"] == []  # i żadna wiadomość nie została odpuszczona


def test_run_finishes_current_round_then_exits_on_stop(monkeypatch):
    """Graceful shutdown (R1): stop kończy pętlę PO utrwaleniu rundy (dokończ → zapisz → wróć)."""

    async def _no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(poller_module.asyncio, "sleep", _no_sleep)  # drzemki kanału natychmiastowe

    root = _raw(msg_id="root-1", created="2024-01-01T11:30:00Z", text="x")
    client = FakeGraphClient([{"roots": [root], "replies": {}}])
    handler = RecordingHandler("odp")
    clock = lambda: datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)  # noqa: E731
    stop = asyncio.Event()
    poller, persist_calls = _make_poller(client, handler, clock=clock, stop=stop)

    original_persist = poller._persist

    def _persist_then_stop(s: Any) -> None:
        original_persist(s)  # utrwal rundę…
        stop.set()  # …i dopiero potem zasygnalizuj stop

    poller._persist = _persist_then_stop  # type: ignore[method-assign]

    asyncio.run(asyncio.wait_for(poller.run(), timeout=5))

    # Trzy utrwalenia w JEDNEJ rundzie: licznik prób przed obsługą, dedup po niej, domknięcie
    # rundy. Istotne jest, że drugiej RUNDY nie ma — stop przerywa pętlę po dokończeniu bieżącej.
    assert len(persist_calls) == 3
    assert len(handler.calls) == 1  # dokładnie jedna runda, jedna wiadomość
    assert client.posted == [("team", "chan", "root-1", "odp")]  # runda dokończona przed stopem
