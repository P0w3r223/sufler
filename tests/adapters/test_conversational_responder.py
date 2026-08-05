"""Testy szwu ``ConversationalResponder`` (ADR 0010) — pamięć rozmowy w drzwiach.

Bez LLM i bez sieci: atrapa runtime (notuje przekazaną historię, zwraca kanned reply)
+ prawdziwy ``ConversationService`` nad SQLite w pamięci. Sprawdzamy, że kolejna tura
dostaje historię poprzednich, odpowiedź jest zapisywana, a rollover dokłada notkę.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from workmate.adapters.inbound.commands import (
    _NEW_THREAD_ACK,
    _NEW_THREAD_ALREADY_FRESH,
    CommandRouter,
)
from workmate.adapters.inbound.responder import (
    ConversationalResponder,
    InboundMessage,
    _to_transcript,
    _to_transcript_with_summary,
    _with_notices,
)
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.compaction import CompactionService
from workmate.core.application.conversations import ConversationService
from workmate.core.domain.conversation import ConversationMessage, ConversationSummary
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    Attachment,
    LLMResponse,
    RawTurn,
    ToolResults,
    UserText,
    attachment_to_row,
)

_TS = datetime(2025, 1, 1, 12, 0, 0)


def _msg(role: str, text: str, *, blocks=None) -> ConversationMessage:
    return ConversationMessage(
        id=1,
        conversation_id="c",
        role=role,
        text=text,
        created_at=_TS,
        blocks=blocks,
    )


class _FakeRuntime:
    """Atrapa runtime — notuje ``(query, history)`` i zwraca stały ``AgentResult``.

    ``usage`` (Design 2) niesie realne tokeny tury — do bramki rolloveru na limicie kontekstu.
    """

    def __init__(self, reply: str, usage: TokenUsage | None = None) -> None:
        self.reply = reply
        self.usage = usage or TokenUsage()
        self.calls: list[tuple[str, list[object]]] = []

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = ""
    ) -> AgentResult:
        self.calls.append((query, list(history)))  # type: ignore[arg-type]
        entries = (UserText(query), AssistantTurn(self.reply, (), (), usage=self.usage))
        return AgentResult(
            reply=self.reply, entries=entries, stop_reason="end_turn", usage=self.usage
        )


class _FailingRuntime:
    """Atrapa runtime, która rzuca — symuluje przejściowy błąd Claude API."""

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = ""
    ) -> AgentResult:
        raise RuntimeError("runtime padł")


class _FakeClock:
    """Zegar testowy: oddaje kolejne z góry zadane chwile (symuluje upływ czasu)."""

    def __init__(self, times: list[datetime]) -> None:
        self._times = times
        self._i = 0

    def __call__(self) -> datetime:
        value = self._times[min(self._i, len(self._times) - 1)]
        self._i += 1
        return value


class _ThinkingRuntime:
    """Atrapa runtime: tura niesie podsumowanie rozumowania (``display=summarized``)."""

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = ""
    ) -> AgentResult:
        return AgentResult(
            reply="odpowiedz",
            entries=(UserText(query), AssistantTurn("odpowiedz", ())),
            stop_reason="end_turn",
            thinking="Analizuję pytanie.",
        )


def test_second_turn_receives_prior_history_and_reply_is_recorded():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    runtime = _FakeRuntime("odpowiedz")
    responder = ConversationalResponder(runtime, service, channel="telegram")

    r1 = asyncio.run(responder.respond(InboundMessage(text="pierwsza", conversation_id="chat1")))
    asyncio.run(responder.respond(InboundMessage(text="druga", conversation_id="chat1")))

    assert r1 == "odpowiedz"
    # Druga tura: query "druga", a historia to poprzednia para (user + assistant).
    query2, history2 = runtime.calls[1]
    assert query2 == "druga"
    assert [type(h).__name__ for h in history2] == ["UserText", "AssistantTurn"]
    # W jednym wątku: 2 tury user + 2 assistant.
    active = store.active_conversation("telegram", "chat1")
    assert active is not None
    assert len(store.messages(active.id)) == 4


def test_rollover_prefixes_notice_on_context_limit():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=5)
    # Realne usage tury: kontekst 10 ≥ próg 5 → kolejna tura startuje nowy wątek (Design 2).
    runtime = _FakeRuntime("ok", usage=TokenUsage(input_tokens=10))
    responder = ConversationalResponder(runtime, service, channel="telegram")

    asyncio.run(responder.respond(InboundMessage(text="pierwsza", conversation_id="c")))
    reply2 = asyncio.run(responder.respond(InboundMessage(text="druga", conversation_id="c")))

    assert reply2.startswith("(Zaczynam nową rozmowę")


def test_idle_gap_starts_new_thread_via_responder():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(
        store, max_context_tokens=1000, idle_timeout=timedelta(minutes=30)
    )
    runtime = _FakeRuntime("ok")
    # Pierwsza tura o _TS, druga 31 min później → przekroczona bezczynność → nowy wątek.
    clock = _FakeClock([_TS, _TS + timedelta(minutes=31)])
    responder = ConversationalResponder(runtime, service, channel="telegram", clock=clock)

    asyncio.run(responder.respond(InboundMessage(text="pierwsza", conversation_id="chat1")))
    # Po zapisie updated_at to realny CURRENT_TIMESTAMP — przypnij do _TS, by bezczynność
    # liczyła się względem testowego zegara (deterministycznie, nie względem zegara systemu).
    active = store.active_conversation("telegram", "chat1")
    assert active is not None
    store._conn.execute(
        "UPDATE conversations SET updated_at=? WHERE id=?",
        (_TS.isoformat(sep=" "), active.id),
    )
    store._conn.commit()

    reply2 = asyncio.run(responder.respond(InboundMessage(text="druga", conversation_id="chat1")))

    assert reply2.startswith("(Zaczynam nową rozmowę")
    # Druga tura poszła do NOWEGO wątku — dostała pustą historię (świeży kontekst).
    _, history2 = runtime.calls[1]
    assert history2 == []


def test_runtime_error_leaves_no_orphan_turn():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    responder = ConversationalResponder(_FailingRuntime(), service, channel="telegram")

    with pytest.raises(RuntimeError):
        asyncio.run(responder.respond(InboundMessage(text="czesc", conversation_id="chat1")))

    # prepare_turn otworzyło rozmowę, ale błąd runtime → NIC nie utrwalono (brak sieroty).
    active = store.active_conversation("telegram", "chat1")
    assert active is not None
    assert store.messages(active.id) == []
    assert active.message_count == 0
    assert active.usage.total_tokens == 0


# --- _to_transcript: odtworzenie historii z wierszy magazynu (ADR 0011) --------


def test_to_transcript_rebuilds_assistant_blocks_as_raw_turn():
    blocks = [
        {"type": "thinking", "thinking": "", "signature": "SIG=="},
        {"type": "text", "text": "cześć"},
    ]
    entries = _to_transcript([_msg("assistant", "cześć", blocks=blocks)])

    assert len(entries) == 1
    raw = entries[0]
    assert isinstance(raw, RawTurn)
    assert raw.role == "assistant"
    assert raw.blocks == tuple(blocks)  # bloki VERBATIM (signature 1:1)


def test_to_transcript_rebuilds_tool_row_as_tool_results():
    blocks = [{"call_id": "t1", "content": '{"count": 1}', "is_error": False}]
    entries = _to_transcript([_msg("tool", "", blocks=blocks)])

    assert len(entries) == 1
    results = entries[0]
    assert isinstance(results, ToolResults)
    out = results.outputs[0]
    assert (out.call_id, out.content, out.is_error) == ("t1", '{"count": 1}', False)


def test_to_transcript_degrades_legacy_row_without_blocks_to_text_only():
    """Wiersz sprzed 0011 (blocks None): assistant → AssistantTurn text-only, user → UserText."""
    entries = _to_transcript(
        [
            _msg("user", "pytanie"),
            _msg("assistant", "stara odpowiedz"),
        ]
    )

    assert entries[0] == UserText("pytanie")
    legacy_assistant = entries[1]
    assert isinstance(legacy_assistant, AssistantTurn)
    assert legacy_assistant.text == "stara odpowiedz"
    assert legacy_assistant.blocks == ()  # brak bloków → składane z tekstu przy odsyłaniu


def test_to_transcript_preserves_turn_order():
    blocks = [{"type": "text", "text": "a"}]
    tool_blocks = [{"call_id": "t1", "content": "{}", "is_error": False}]
    entries = _to_transcript(
        [
            _msg("user", "q"),
            _msg("assistant", "a", blocks=blocks),
            _msg("tool", "", blocks=tool_blocks),
            _msg("assistant", "b", blocks=[{"type": "text", "text": "b"}]),
        ]
    )

    assert [type(e).__name__ for e in entries] == [
        "UserText",
        "RawTurn",
        "ToolResults",
        "RawTurn",
    ]


# --- _to_transcript: round-trip załączników użytkownika (ADR 0016) --------------


def _summary(text: str) -> ConversationSummary:
    return ConversationSummary(
        id=1,
        conversation_id="c",
        summary=text,
        covers_through_message_id=1,
        created_at=_TS,
    )


def test_to_transcript_rebuilds_user_attachments_from_blocks():
    """Round-trip pamięci: wiersz user z NEUTRALNYMI blokami załączników → identyczny
    ``UserText`` z załącznikami (jak zapisał ``_row_of`` przez ``attachment_to_row``)."""
    img = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")
    pdf = Attachment("document", "application/pdf", "umowa.pdf", data_base64="UERG")
    blocks = [attachment_to_row(img), attachment_to_row(pdf)]

    entries = _to_transcript([_msg("user", "zobacz", blocks=blocks)])

    assert entries == [UserText("zobacz", (img, pdf))]


def test_to_transcript_keeps_attachment_only_user_message():
    """Wiadomość z SAMYM załącznikiem (pusty caption) NIE wypada z transkryptu
    (warunek ``msg.text or msg.blocks``)."""
    img = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")

    entries = _to_transcript([_msg("user", "", blocks=[attachment_to_row(img)])])

    assert entries == [UserText("", (img,))]


def test_to_transcript_with_summary_preserves_first_turn_attachments():
    """Doklejenie podsumowania do PIERWSZEJ tury user ZACHOWUJE jej załączniki."""
    img = Attachment("image", "image/png", "zrzut.png", data_base64="QUJD")
    messages = [_msg("user", "pierwsza", blocks=[attachment_to_row(img)])]

    entries = _to_transcript_with_summary(_summary("STRESZCZENIE"), messages)

    first = entries[0]
    assert isinstance(first, UserText)
    assert first.text.startswith("[Podsumowanie wcześniejszej rozmowy]")
    assert "pierwsza" in first.text
    assert first.attachments == (img,)  # załączniki pierwszej tury nietknięte


def test_to_transcript_with_summary_without_summary_is_plain_transcript():
    img = Attachment("image", "image/png", "z.png", data_base64="QUJD")
    messages = [_msg("user", "q", blocks=[attachment_to_row(img)])]

    assert _to_transcript_with_summary(None, messages) == [UserText("q", (img,))]


# --- Pokazywanie rozumowania modelu (display=summarized): show_thinking ---------


def test_show_thinking_prepends_reasoning_summary_on_trusted_door():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    responder = ConversationalResponder(
        _ThinkingRuntime(), service, channel="cli", show_thinking=True
    )

    reply = asyncio.run(responder.respond(InboundMessage(text="q", conversation_id="c")))

    assert reply.startswith("[rozumowanie modelu]")
    assert "Analizuję pytanie." in reply
    assert reply.endswith("odpowiedz")  # rozumowanie NAD odpowiedzią


def test_thinking_hidden_by_default_on_async_doors():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    # Bez show_thinking (domyślnie False) — async drzwi nie wysyłają rozumowania userowi.
    responder = ConversationalResponder(_ThinkingRuntime(), service, channel="telegram")

    reply = asyncio.run(responder.respond(InboundMessage(text="q", conversation_id="c")))

    assert reply == "odpowiedz"
    assert "rozumowanie" not in reply


# --- Komenda jawnego startu wątku /nowa przez router komend (ADR 0012) ---------
# (Parsowanie tokenów komend testuje osobno tests/adapters/inbound/test_commands.py.)


def test_new_thread_command_closes_thread_without_calling_runtime():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    runtime = _FakeRuntime("odpowiedz")
    responder = ConversationalResponder(
        runtime, service, channel="telegram", commands=CommandRouter(service, {})
    )

    # Zbuduj niepusty wątek (jedna realna tura).
    asyncio.run(responder.respond(InboundMessage(text="pierwsza", conversation_id="chat1")))

    ack = asyncio.run(responder.respond(InboundMessage(text="/nowa", conversation_id="chat1")))

    assert ack == _NEW_THREAD_ACK
    assert len(runtime.calls) == 1  # komenda NIE poszła do runtime (wciąż 1 wywołanie)
    assert store.active_conversation("telegram", "chat1") is None  # wątek domknięty

    # Kolejna wiadomość otwiera nowy wątek — model dostaje świeżą (pustą) historię.
    asyncio.run(responder.respond(InboundMessage(text="druga", conversation_id="chat1")))
    _, history = runtime.calls[1]
    assert history == []


def test_new_thread_command_on_empty_conversation_reports_already_fresh():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    runtime = _FakeRuntime("x")
    responder = ConversationalResponder(
        runtime, service, channel="telegram", commands=CommandRouter(service, {})
    )

    ack = asyncio.run(responder.respond(InboundMessage(text="/nowa", conversation_id="chat1")))

    assert ack == _NEW_THREAD_ALREADY_FRESH
    assert runtime.calls == []  # nic nie trafiło do runtime


# --- _with_notices: notki systemowe przed odpowiedzią (ADR 0011) ---------------


def test_with_notices_returns_reply_unchanged_when_no_signals():
    assert _with_notices("odp", rolled_over=False, stop_reason="end_turn") == "odp"


def test_with_notices_prefixes_truncation_on_max_tokens():
    out = _with_notices("czesciowa", rolled_over=False, stop_reason="max_tokens")
    assert out.startswith("(Odpowiedź została ucięta")
    assert out.endswith("czesciowa")


def test_with_notices_combines_rollover_and_truncation():
    out = _with_notices("odp", rolled_over=True, stop_reason="max_tokens")
    assert "nową rozmowę" in out
    assert "ucięta" in out
    assert out.endswith("odp")


# --- Kompaktowanie wpięte w szew (ADR 0014) ------------------------------------


class _FakeSummarizer:
    """Atrapa ``LLMClient`` modelu podsumowującego — zwraca stały skrót."""

    def __init__(self, text: str = "SKRÓT") -> None:
        self.text = text

    def complete(self, *, system, transcript, tools):  # noqa: ANN001, ANN201
        return LLMResponse(text=self.text, usage=TokenUsage(input_tokens=10, output_tokens=5))


def test_compaction_archives_old_turns_and_injects_summary():
    store = SqliteConversationStore(":memory:")
    # Kompaktowanie zastępuje rollover rozmiaru: size_rollover=False, duży limit.
    service = ConversationService(store, max_context_tokens=1_000_000, size_rollover=False)
    # Każda tura raportuje input 500 > próg 100 → po zebraniu dość wymian kompaktuje.
    runtime = _FakeRuntime("odp", usage=TokenUsage(input_tokens=500))
    compaction = CompactionService(store, _FakeSummarizer(), threshold_tokens=100, keep_turns=2)
    responder = ConversationalResponder(runtime, service, channel="cli", compaction=compaction)

    for i in range(4):
        asyncio.run(responder.respond(InboundMessage(text=f"q{i}", conversation_id="chat")))

    active = store.active_conversation("cli", "chat")
    assert active is not None
    # Powstało podsumowanie, a najstarsze tury są zarchiwizowane (nie usunięte).
    summary = store.active_summary(active.id)
    assert summary is not None and summary.summary == "SKRÓT"
    assert any(m.archived for m in store.messages(active.id))
    # Runtime OSTATNIEJ tury dostał historię z doklejonym podsumowaniem w pierwszej turze user.
    _query, history = runtime.calls[-1]
    assert isinstance(history[0], UserText)
    assert history[0].text.startswith("[Podsumowanie wcześniejszej rozmowy]")
    assert "SKRÓT" in history[0].text


def test_no_compaction_keeps_full_history_in_replay():
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1_000_000, size_rollover=False)
    # Wejście poniżej progu — kompaktowanie nie odpala, replay = pełna historia.
    runtime = _FakeRuntime("odp", usage=TokenUsage(input_tokens=10))
    compaction = CompactionService(store, _FakeSummarizer(), threshold_tokens=10_000, keep_turns=2)
    responder = ConversationalResponder(runtime, service, channel="cli", compaction=compaction)

    for i in range(3):
        asyncio.run(responder.respond(InboundMessage(text=f"q{i}", conversation_id="chat")))

    active = store.active_conversation("cli", "chat")
    assert active is not None
    assert store.active_summary(active.id) is None
    assert all(not m.archived for m in store.messages(active.id))


def test_metrics_records_call_per_turn():
    """Metryka (Tor A): tura na drzwiach zapisuje wywołanie z pseudonimem nadawcy."""
    from workmate.adapters.outbound.sqlite_metrics import SqliteMetricsStore
    from workmate.core.application.metrics import MetricsService

    convs = ConversationService(SqliteConversationStore(":memory:"), max_context_tokens=1000)
    metrics_store = SqliteMetricsStore(":memory:")
    responder = ConversationalResponder(
        _FakeRuntime("ok"), convs, channel="teams", metrics=MetricsService(metrics_store)
    )

    asyncio.run(
        responder.respond(InboundMessage(text="czesc", conversation_id="c", sender_id="aad-1"))
    )

    (door,) = metrics_store.summary().by_door
    assert door.door == "teams"
    assert door.calls == 1
    assert door.unique_users == 1


def test_metrics_failure_does_not_break_turn():
    """Błąd licznika (best-effort) nie może wywrócić tury — odpowiedź nadal wraca."""

    class _BoomMetrics:
        def record(self, *_a: object, **_k: object) -> None:
            raise RuntimeError("licznik padł")

    convs = ConversationService(SqliteConversationStore(":memory:"), max_context_tokens=1000)
    responder = ConversationalResponder(
        _FakeRuntime("ok"), convs, channel="teams", metrics=_BoomMetrics()  # type: ignore[arg-type]
    )

    reply = asyncio.run(responder.respond(InboundMessage(text="czesc", conversation_id="c")))
    assert reply == "ok"
