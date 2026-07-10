"""Testy serwisu rozmów (``ConversationService``, ADR 0010) — wątek, historia, rollover.

Logika bez I/O: atrapa ``ConversationStore`` w pamięci. Sprawdzamy trzy rzeczy:
otwieranie wątku, zwracanie historii SPRZED bieżącej wiadomości oraz rollover do
nowej rozmowy po przekroczeniu limitu kontekstu.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from workmate.core.application.conversations import ConversationService, estimate_tokens
from workmate.core.domain.conversation import (
    Conversation,
    ConversationMessage,
    ConversationSearchHit,
)
from workmate.core.ports.llm import (
    AssistantTurn,
    ToolCall,
    ToolOutput,
    ToolResults,
    UserText,
)

_TS = datetime(2025, 1, 1, 12, 0, 0)


class _FakeStore:
    """Atrapa magazynu rozmów w pamięci (kaczo-typowana wobec ``ConversationStore``)."""

    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.msgs: dict[str, list[ConversationMessage]] = {}
        self._conv_seq = 0
        self._msg_seq = 0

    def active_conversation(
        self, channel: str, external_id: str
    ) -> Conversation | None:
        actives = [
            c
            for c in self.conversations.values()
            if c.channel == channel
            and c.external_id == external_id
            and c.status == "active"
        ]
        if not actives:
            return None
        conv = actives[-1]
        total = sum(m.token_estimate for m in self.msgs[conv.id])
        return conv.model_copy(update={"token_estimate": total})

    def open_conversation(self, channel: str, external_id: str) -> Conversation:
        self._conv_seq += 1
        cid = f"c{self._conv_seq}"
        conv = Conversation(
            id=cid,
            channel=channel,
            external_id=external_id,
            status="active",
            token_estimate=0,
            created_at=_TS,
            updated_at=_TS,
        )
        self.conversations[cid] = conv
        self.msgs[cid] = []
        return conv

    def close_conversation(self, conversation_id: str) -> None:
        conv = self.conversations[conversation_id]
        self.conversations[conversation_id] = conv.model_copy(
            update={"status": "closed"}
        )

    def append_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        token_estimate: int,
        *,
        blocks: list[dict] | None = None,
        stop_reason: str | None = None,
    ) -> ConversationMessage:
        self._msg_seq += 1
        msg = ConversationMessage(
            id=self._msg_seq,
            conversation_id=conversation_id,
            role=role,
            text=text,
            token_estimate=token_estimate,
            created_at=_TS,
            blocks=blocks,
            stop_reason=stop_reason,
        )
        self.msgs[conversation_id].append(msg)
        return msg

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        return list(self.msgs[conversation_id])

    def list_conversations(
        self, *, channel: str | None = None, limit: int = 50
    ) -> list[Conversation]:
        # Najnowsze pierwsze: dict trzyma kolejność wstawiania, reversed → od końca.
        picked = [
            c
            for c in reversed(self.conversations.values())
            if channel is None or c.channel == channel
        ][:limit]
        # Suma tokenów per rozmowa (jak w adapterze) — z utrwalonych tur.
        return [
            c.model_copy(
                update={"token_estimate": sum(m.token_estimate for m in self.msgs[c.id])}
            )
            for c in picked
        ]

    def search(
        self,
        query: str,
        *,
        channel: str | None = None,
        external_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationSearchHit]:
        return []


def test_estimate_tokens_is_deterministic_and_positive():
    assert estimate_tokens("") == 1
    assert estimate_tokens("12345678") == 2  # 8 // 4


def test_first_message_opens_empty_conversation_and_record_turn_persists():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    conv_id, history, rolled_over = service.prepare_turn("telegram", "chat1", "czesc")

    assert history == []
    assert rolled_over is False
    # prepare_turn NIE utrwala tury (brak sieroty przy błędzie runtime) — pusto do record_turn.
    assert store.msgs[conv_id] == []

    service.record_turn(conv_id, "czesc", "hej")
    assert [(m.role, m.text) for m in store.msgs[conv_id]] == [
        ("user", "czesc"),
        ("assistant", "hej"),
    ]


def test_history_returns_prior_turns_not_current_message():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "pierwsza")
    service.record_turn(cid1, "pierwsza", "odpowiedz-1")
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "druga")

    assert cid2 == cid1  # ten sam wątek (limit nieprzekroczony)
    assert rolled_over is False
    assert [(m.role, m.text) for m in history] == [
        ("user", "pierwsza"),
        ("assistant", "odpowiedz-1"),
    ]


def test_rollover_starts_new_conversation_on_limit():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=3)

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "12345678")  # est 2
    service.record_turn(cid1, "12345678", "87654321")  # user est2 + assistant est2 → suma 4
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "1234")

    assert rolled_over is True
    assert cid2 != cid1
    assert history == []  # świeży kontekst nowej rozmowy
    # Stara rozmowa domknięta (nadal w magazynie, wyszukiwalna).
    assert store.conversations[cid1].status == "closed"


# --- Rollover po bezczynności (ADR 0012) ---------------------------------------
# _FakeStore.open_conversation nadaje updated_at=_TS i append go nie zmienia, więc
# „bezczynność" symulujemy przez ``now`` odległe od _TS — deterministycznie, bez zegara.


def test_rollover_starts_new_thread_after_idle_gap():
    store = _FakeStore()
    service = ConversationService(
        store, max_context_tokens=1000, idle_timeout=timedelta(minutes=30)
    )

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    service.record_turn(cid1, "czesc", "hej")  # token_estimate > 0 (wątek niepusty)
    later = _TS + timedelta(minutes=31)  # 31 min bezczynności > próg 30 min
    cid2, history, rolled_over = service.prepare_turn(
        "telegram", "chat1", "wracam", now=later
    )

    assert rolled_over is True
    assert cid2 != cid1
    assert history == []  # nowy wątek — świeży kontekst
    assert store.conversations[cid1].status == "closed"


def test_no_rollover_within_idle_window():
    store = _FakeStore()
    service = ConversationService(
        store, max_context_tokens=1000, idle_timeout=timedelta(minutes=30)
    )

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    service.record_turn(cid1, "czesc", "hej")
    soon = _TS + timedelta(minutes=10)  # w oknie 30 min → ten sam wątek
    cid2, history, rolled_over = service.prepare_turn(
        "telegram", "chat1", "dalej", now=soon
    )

    assert rolled_over is False
    assert cid2 == cid1
    assert [(m.role, m.text) for m in history] == [("user", "czesc"), ("assistant", "hej")]


def test_idle_disabled_keeps_single_thread_across_long_gap():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)  # idle_timeout=None

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    service.record_turn(cid1, "czesc", "hej")
    much_later = _TS + timedelta(days=7)
    cid2, _, rolled_over = service.prepare_turn(
        "telegram", "chat1", "po tygodniu", now=much_later
    )

    assert rolled_over is False  # wyłączone kryterium → tylko limit kontekstu
    assert cid2 == cid1


def test_empty_thread_not_rolled_over_on_idle():
    store = _FakeStore()
    service = ConversationService(
        store, max_context_tokens=1000, idle_timeout=timedelta(minutes=30)
    )

    # Pierwsza tura otwiera PUSTY wątek (prepare_turn nie utrwala) — brak tur do odcięcia.
    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    later = _TS + timedelta(minutes=31)
    cid2, _, rolled_over = service.prepare_turn(
        "telegram", "chat1", "kolejna", now=later
    )

    assert rolled_over is False  # pustego wątku nie rollujemy mimo przekroczonej przerwy
    assert cid2 == cid1


# --- Jawny start wątku na żądanie: start_new_thread (ADR 0012 / komenda) --------


def test_start_new_thread_closes_active_nonempty_and_next_turn_opens_fresh():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("telegram", "chat1", "czesc")
    service.record_turn(cid, "czesc", "hej")  # wątek niepusty

    started = service.start_new_thread("telegram", "chat1")

    assert started is True
    assert store.conversations[cid].status == "closed"  # domknięty (w archiwum)
    # Następna wiadomość otwiera ŚWIEŻY wątek (gałąź active is None → open), bez rollovera.
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "nowa tura")
    assert cid2 != cid
    assert history == []
    assert rolled_over is False


def test_start_new_thread_is_noop_on_empty_thread():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("telegram", "chat1", "czesc")  # otwiera PUSTY wątek

    assert service.start_new_thread("telegram", "chat1") is False
    assert store.conversations[cid].status == "active"  # pustego nie zamykamy


def test_start_new_thread_is_noop_without_active_thread():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    assert service.start_new_thread("telegram", "chat1") is False


# --- Podgląd historii: delegacje list_conversations / messages -----------------


def test_list_conversations_delegates_with_channel_filter_and_token_sum():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    tg = store.open_conversation("telegram", "chat1")
    store.open_conversation("teams", "conv1")
    store.append_message(tg.id, "user", "12345678", 2)  # 8 znaków → est 2

    result = service.list_conversations(channel="telegram")

    assert [c.id for c in result] == [tg.id]  # filtr kanału przekazany do magazynu
    assert result[0].token_estimate == 2  # suma tur rozmowy


def test_list_conversations_newest_first():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    first = store.open_conversation("cli", "a")
    second = store.open_conversation("cli", "b")

    order = [c.id for c in service.list_conversations()]
    assert order == [second.id, first.id]  # najnowsza rozmowa pierwsza


def test_messages_delegates_to_store_in_order():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    conv = store.open_conversation("cli", "a")
    store.append_message(conv.id, "user", "pytanie", 2)
    store.append_message(conv.id, "assistant", "odpowiedz", 3)

    got = service.messages(conv.id)
    assert [(m.role, m.text) for m in got] == [
        ("user", "pytanie"),
        ("assistant", "odpowiedz"),
    ]


# --- record_run: bezstratne mapowanie wpisów na wiersze (ADR 0011) -------------


def _record(entries, *, stop_reason=""):
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    conv = store.open_conversation("telegram", "chat1")
    service.record_run(conv.id, entries, stop_reason=stop_reason)
    return store.msgs[conv.id]


def test_record_run_maps_assistant_blocks_and_tool_domain_form():
    thinking = {"type": "thinking", "thinking": "", "signature": "SIG=="}
    text_block = {"type": "text", "text": "hej"}
    tool_use = {"type": "tool_use", "id": "t1", "name": "search_notes", "input": {}}
    entries = (
        UserText("pytanie"),
        AssistantTurn(
            "hej", (ToolCall("t1", "search_notes", {}),), (thinking, text_block, tool_use)
        ),
        ToolResults((ToolOutput("t1", '{"count": 1}'),)),
        AssistantTurn("Znalazłem 1.", (), ({"type": "text", "text": "Znalazłem 1."},)),
    )

    rows = _record(entries, stop_reason="end_turn")

    assert [r.role for r in rows] == ["user", "assistant", "tool", "assistant"]
    # Assistant: bloki dostawcy VERBATIM (thinking z signature), płaski tekst do FTS.
    assert rows[1].blocks == [thinking, text_block, tool_use]
    assert rows[1].text == "hej"
    # Tool: pusty tekst (poza FTS) + forma DOMENOWA {call_id, content, is_error}.
    assert rows[2].text == ""
    assert rows[2].blocks == [{"call_id": "t1", "content": '{"count": 1}', "is_error": False}]


def test_record_run_attaches_stop_reason_only_to_last_assistant():
    entries = (
        UserText("q"),
        AssistantTurn("a1", (ToolCall("t1", "x", {}),), ({"type": "text", "text": "a1"},)),
        ToolResults((ToolOutput("t1", "{}"),)),
        AssistantTurn("a2", (), ({"type": "text", "text": "a2"},)),
    )

    rows = _record(entries, stop_reason="max_tokens")

    # stop_reason tylko na OSTATNIEJ turze asystenta — wcześniejsze bez niej.
    assert rows[1].stop_reason is None
    assert rows[2].stop_reason is None
    assert rows[3].stop_reason == "max_tokens"


def test_record_run_token_estimate_ignores_thinking_content():
    """``token_estimate`` liczony po płaskim tekście — długi thinking go NIE zawyża."""
    long_thinking = {"type": "thinking", "thinking": "x" * 4000, "signature": "S"}
    short_text = {"type": "text", "text": "ok"}
    entries = (AssistantTurn("ok", (), (long_thinking, short_text)),)

    rows = _record(entries)

    # Estymata z tekstu "ok" (2 znaki → 1 token), nie z 4000 znaków thinking.
    assert rows[0].token_estimate == estimate_tokens("ok")
    assert rows[0].token_estimate == 1
