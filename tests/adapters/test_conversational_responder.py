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
from workmate.core.application.conversations import ConversationService, _row_of
from workmate.core.domain.conversation import ConversationMessage, ConversationSummary
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    Attachment,
    LLMResponse,
    RawTurn,
    ToolCall,
    ToolOutput,
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
        session_header: str = "",
        audit: object = None,
        attachment_queue: object = None,
        trust_nonce: str = "",
        trust: str = "T1",
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
        session_header: str = "",
        audit: object = None,
        attachment_queue: object = None,
        trust_nonce: str = "",
        trust: str = "T1",
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
        session_header: str = "",
        audit: object = None,
        attachment_queue: object = None,
        trust_nonce: str = "",
        trust: str = "T1",
    ) -> AgentResult:
        return AgentResult(
            reply="odpowiedz",
            entries=(UserText(query), AssistantTurn("odpowiedz", ())),
            stop_reason="end_turn",
            thinking="Analizuję pytanie.",
        )


class _AuditCapturingRuntime(_FakeRuntime):
    """Runtime notujący rejestrator audytu przekazany do ``run_turn`` (szew ADR 0067)."""

    def __init__(self, reply: str = "ok") -> None:
        super().__init__(reply)
        self.audit_arg: object = "UNSET"

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = "",
        audit: object = None,
        attachment_queue: object = None,
        trust_nonce: str = "",
        trust: str = "T1",
    ) -> AgentResult:
        self.audit_arg = audit
        return super().run_turn(
            query,
            attachments=attachments,
            history=history,
            extra_tools=extra_tools,
            session_header=session_header,
            audit=audit,
        )


class _FakeAudit:
    """Atrapa ``AuditService`` — notuje kontekst tury i zwraca sentinel rejestrator."""

    def __init__(self) -> None:
        self.turn_calls: list[dict[str, str]] = []
        self.recorder = object()

    def turn_recorder(
        self, *, door: str, raw_user: str, conversation_id: str, trust_class: str = "unknown"
    ) -> object:
        self.turn_calls.append(
            {
                "door": door,
                "raw_user": raw_user,
                "conversation_id": conversation_id,
                "trust_class": trust_class,
            }
        )
        return self.recorder


def test_audit_recorder_built_per_turn_and_passed_to_runtime():
    """Szew responder→runtime: rejestrator audytu domknięty na (kanał, nadawca, rozmowa) i wpięty.

    Jedyne miejsce, gdzie audyt się w ogóle włącza — usunięcie ``audit=audit_recorder`` albo pomyłka
    w ``door``/``raw_user``/``conversation_id`` przeszłaby bramkę bez śladu bez tego testu (luka
    pokrycia z review Fazy 0).
    """
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    runtime = _AuditCapturingRuntime()
    audit = _FakeAudit()
    responder = ConversationalResponder(
        runtime,
        service,
        channel="teams",
        audit=audit,  # type: ignore[arg-type]
    )

    asyncio.run(
        responder.respond(
            InboundMessage(text="pytanie", conversation_id="thr-9", sender_id="aad-123")
        )
    )

    assert audit.turn_calls == [
        {
            "door": "teams",
            "raw_user": "aad-123",  # sender_id (AAD id), nie nazwa
            "conversation_id": "thr-9",
            "trust_class": "unknown",  # T0–T3 dowiąże ADR 0066
        }
    ]
    assert runtime.audit_arg is audit.recorder  # dokładnie ten rejestrator wpięty do run_turn


def test_audit_absent_passes_none_recorder():
    """Bez ``audit`` (drzwi bez dziennika) runtime dostaje ``audit=None`` — audyt niewłączony."""
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    runtime = _AuditCapturingRuntime()
    responder = ConversationalResponder(runtime, service, channel="cli")

    asyncio.run(responder.respond(InboundMessage(text="x", conversation_id="c1")))

    assert runtime.audit_arg is None


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


def test_tool_row_round_trips_materialized_files_through_storage():
    """Plik podany przez ``File`` (ADR 0064) musi przeżyć zapis i odtworzenie z pamięci.

    Bez tego replay oddawał modelowi turę, w której MÓWI o pliku, ale samego pliku już nie ma —
    czyli najgorszy wariant: model tłumaczy treść, której nie widzi. Sonda idzie przez OBA
    końce szwu (zapis ``_row_of`` i odczyt ``_to_transcript``), bo rozjazd między nimi jest
    dokładnie tym, co ten test ma łapać.
    """
    original = ToolResults(
        (ToolOutput("t1", '{"materialized": true}'),),
        (Attachment("document", "application/pdf", "umowa.pdf", data_base64="QkFTRTY0"),),
    )

    role, text, blocks, _usage = _row_of(original)
    (restored,) = _to_transcript([_msg(role, text, blocks=blocks)])

    assert isinstance(restored, ToolResults)
    assert restored.outputs == original.outputs  # wyniki nie pomieszały się z plikami
    assert restored.attachments == original.attachments


def test_tool_row_without_files_restores_exactly_as_before():
    role, text, blocks, _usage = _row_of(ToolResults((ToolOutput("t1", "ok"),)))
    (restored,) = _to_transcript([_msg(role, text, blocks=blocks)])

    assert isinstance(restored, ToolResults)
    assert restored.attachments == ()


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

    def complete(self, *, system, transcript, tools, trust_nonce=""):  # noqa: ANN001, ANN201
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
        _FakeRuntime("ok"),
        convs,
        channel="teams",
        metrics=_BoomMetrics(),  # type: ignore[arg-type]
    )

    reply = asyncio.run(responder.respond(InboundMessage(text="czesc", conversation_id="c")))
    assert reply == "ok"


# --- Klasy zaufania i lepka skaza (ADR 0066) -----------------------------------


class _ToolCallingRuntime(_FakeRuntime):
    """Runtime, który udaje turę z wywołaniem WSKAZANEGO narzędzia (do wyzwalaczy skazy)."""

    def __init__(self, tool_name: str) -> None:
        super().__init__("ok")
        self._tool = tool_name
        self.trust_seen: list[str] = []
        self.nonce_seen: list[str] = []

    def run_turn(self, query, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append((query, list(kwargs.get("history", []))))
        self.trust_seen.append(str(kwargs.get("trust")))
        self.nonce_seen.append(str(kwargs.get("trust_nonce")))
        return AgentResult(
            reply="ok",
            entries=(
                UserText(query, (), kwargs.get("trust", "T1")),
                AssistantTurn("ok", (ToolCall("t1", self._tool, {}),)),
                AssistantTurn("ok", ()),
            ),
            stop_reason="end_turn",
        )


def _responder_z_zaufaniem(runtime, **kwargs):
    store = SqliteConversationStore(":memory:")
    service = ConversationService(store, max_context_tokens=1000)
    responder = ConversationalResponder(runtime, service, channel="teams_graph", **kwargs)
    return responder, service, store


def test_attachment_taints_the_conversation():
    """Załącznik to treść obca — od tej chwili rozmowa jest skażona do końca wątku."""
    responder, service, _ = _responder_z_zaufaniem(_FakeRuntime("ok"))
    plik = Attachment("document", "application/pdf", "u.pdf", data_base64="QQ==")

    asyncio.run(
        responder.respond(InboundMessage(text="zobacz", conversation_id="t", attachments=(plik,)))
    )

    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.tainted is True
    assert conv.taint_source == "attachment"


def test_reading_own_notes_does_not_taint():
    """Gdyby skaziło wszystko, sygnał nie znaczyłby nic (ADR 0066 R2).

    Notatki własnego pionu są zza bramek zdolności — czytanie ich to praca, nie kontakt
    z treścią obcą.
    """
    responder, service, _ = _responder_z_zaufaniem(_ToolCallingRuntime("Notes"))

    asyncio.run(responder.respond(InboundMessage(text="co wiemy?", conversation_id="t")))

    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.tainted is False


def test_github_content_taints_the_conversation():
    """Komentarz na GitHubie pisze ktokolwiek, a mapa tożsamości nie zna loginów GitHuba."""
    responder, service, _ = _responder_z_zaufaniem(_ToolCallingRuntime("GitHub"))

    asyncio.run(responder.respond(InboundMessage(text="co w PR?", conversation_id="t")))

    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.tainted is True
    assert conv.taint_source == "tool"


def test_first_taint_source_wins():
    """Skaza mówi „od kiedy i przez co", nie „co ostatnio wpadło" — inaczej ślad audytowy
    zamienia się w migawkę ostatniej tury."""
    responder, service, _ = _responder_z_zaufaniem(_ToolCallingRuntime("GitHub"))
    plik = Attachment("document", "application/pdf", "u.pdf", data_base64="QQ==")

    asyncio.run(
        responder.respond(InboundMessage(text="a", conversation_id="t", attachments=(plik,)))
    )
    asyncio.run(responder.respond(InboundMessage(text="b", conversation_id="t")))

    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.taint_source == "attachment"  # pierwszy zapłon, nie ostatni


def test_a_clean_turn_leaves_the_conversation_clean():
    responder, service, _ = _responder_z_zaufaniem(_FakeRuntime("ok"))

    asyncio.run(responder.respond(InboundMessage(text="dzień dobry", conversation_id="t")))

    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.tainted is False


def test_unmapped_sender_turn_is_data_and_taints():
    """Rozszczepienie T1/T2: gość dostaje odpowiedź, ale jego słowa schodzą do danych."""
    runtime = _ToolCallingRuntime("Notes")
    responder, service, _ = _responder_z_zaufaniem(
        runtime, sender_trust=lambda sender_id: "T1" if sender_id == "aad-znany" else "T2"
    )

    asyncio.run(
        responder.respond(InboundMessage(text="cześć", conversation_id="t", sender_id="obcy"))
    )

    assert runtime.trust_seen == ["T2"]
    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.taint_source == "guest"


def test_mapped_sender_stays_an_instruction_and_does_not_taint():
    runtime = _ToolCallingRuntime("Notes")
    responder, service, _ = _responder_z_zaufaniem(
        runtime, sender_trust=lambda sender_id: "T1" if sender_id == "aad-znany" else "T2"
    )

    asyncio.run(
        responder.respond(InboundMessage(text="cześć", conversation_id="t", sender_id="aad-znany"))
    )

    assert runtime.trust_seen == ["T1"]
    conv = service.active_conversation("teams_graph", "t")
    assert conv is not None and conv.tainted is False


def test_failure_to_resolve_the_sender_is_fail_closed():
    """Nie wiemy, kto pisze → traktujemy słowa jak dane. Odwrotny domysł byłby awansem
    nieznajomego do rangi operatora."""

    def wybuchowy(sender_id: str) -> str:
        raise RuntimeError("mapa tożsamości nieczytelna")

    runtime = _ToolCallingRuntime("Notes")
    responder, _service, _ = _responder_z_zaufaniem(runtime, sender_trust=wybuchowy)

    asyncio.run(responder.respond(InboundMessage(text="cześć", conversation_id="t", sender_id="x")))

    assert runtime.trust_seen == ["T2"]


def test_trust_class_survives_storage_and_replay():
    """Bez trwałości granica trzymałaby JEDNĄ turę: przy następnej wiadomości tekst gościa
    wracałby z pamięci jako zwykła instrukcja."""
    runtime = _ToolCallingRuntime("Notes")
    responder, _service, _ = _responder_z_zaufaniem(runtime, sender_trust=lambda _s: "T2")

    asyncio.run(
        responder.respond(InboundMessage(text="pierwsza", conversation_id="t", sender_id="x"))
    )
    asyncio.run(responder.respond(InboundMessage(text="druga", conversation_id="t", sender_id="x")))

    historia = [e for e in runtime.calls[-1][1] if isinstance(e, UserText)]
    assert historia and all(e.trust == "T2" for e in historia)


def test_nonce_is_absent_when_labels_are_off():
    """Bramka OFF = żądanie bajt w bajt jak dotąd; to warunek porównania zachowania przed/po."""
    runtime = _ToolCallingRuntime("Notes")
    responder, _service, _ = _responder_z_zaufaniem(runtime)

    asyncio.run(responder.respond(InboundMessage(text="a", conversation_id="t")))

    assert runtime.nonce_seen == [""]


def test_nonce_is_stable_within_a_conversation_but_differs_between_them():
    """Stały w rozmowie — inaczej każda tura zmieniałaby bajty CAŁEJ historii w żądaniu i cache
    prefiksu urywałby się na pierwszym opakowanym wyniku narzędzia.

    Różny między rozmowami, bo nonce jednej nie ma prawa nic znaczyć w drugiej.
    """
    runtime = _ToolCallingRuntime("Notes")
    responder, _service, _ = _responder_z_zaufaniem(runtime, trust_labels=True)

    asyncio.run(responder.respond(InboundMessage(text="a", conversation_id="t")))
    asyncio.run(responder.respond(InboundMessage(text="b", conversation_id="t")))
    asyncio.run(responder.respond(InboundMessage(text="c", conversation_id="inny-watek")))

    w_tej_samej = runtime.nonce_seen[:2]
    assert len(set(w_tej_samej)) == 1 and all(w_tej_samej)
    assert runtime.nonce_seen[2] != runtime.nonce_seen[0]


def test_nonce_is_not_derivable_from_the_conversation_id_alone():
    """Nonce wywodzi się z sekretu procesu, nie z samego identyfikatora rozmowy — inaczej
    znałby go każdy, kto zna nazwę wątku (a ta jedzie w nagłówku sesji)."""
    runtime_a = _ToolCallingRuntime("Notes")
    runtime_b = _ToolCallingRuntime("Notes")
    a, _s1, _st1 = _responder_z_zaufaniem(runtime_a, trust_labels=True)
    b, _s2, _st2 = _responder_z_zaufaniem(runtime_b, trust_labels=True)

    asyncio.run(a.respond(InboundMessage(text="x", conversation_id="ten-sam")))
    asyncio.run(b.respond(InboundMessage(text="x", conversation_id="ten-sam")))

    assert runtime_a.nonce_seen[0] != runtime_b.nonce_seen[0]


def test_tainting_tool_names_match_the_real_catalog():
    """Zbiór wyzwalaczy to NAPISY — bez wiązania z rejestrem zmiana nazwy narzędzia gasi
    wyzwalacz po cichu, a objawem jest wyłącznie skaza, która nigdy się nie zapala."""
    from workmate.adapters.inbound.responder import _TAINTING_TOOLS
    from workmate.core.application.tools import build_file_catalog, build_workspace_catalog
    from workmate.core.application.workspace import (
        WorkspaceLimits,
        WorkspaceService,
        WorkspaceWriteService,
    )
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.llm import AttachmentQueue
    from workmate.core.ports.materialization import MaterializationLimits

    class _PustyWorkspace:
        def list(self, scope_dir):
            return []

        def read(self, scope_dir, name):
            return None

        def read_bytes(self, scope_dir, name):
            return None

        def exists(self, relpath):
            return False

        def create(self, relpath, content):
            raise AssertionError

        def create_bytes(self, relpath, data):
            raise AssertionError

    class _PustyMaterializer:
        def materialize(self, name, data):
            return None

    repo = _PustyWorkspace()
    scope = WorkspaceScope("teams_graph", "t/c/r")
    limity = WorkspaceLimits(1, 1, 1, frozenset({"md"}))
    workspace = [
        s.name
        for s in build_workspace_catalog(
            scope, WorkspaceService(repo), WorkspaceWriteService(repo, repo, limity)
        )
    ]
    plikowe = [
        s.name
        for s in build_file_catalog(
            scope,
            WorkspaceService(repo),
            _PustyMaterializer(),
            AttachmentQueue(budget_bytes=1),
            MaterializationLimits(1, 1),
        )
    ]

    # Narzędzia CZYTAJĄCE katalog roboczy muszą być wyzwalaczami — trzymają odłożone załączniki.
    assert {"read_file", "list_files"} <= set(workspace)
    assert {"read_file", "list_files"} <= _TAINTING_TOOLS
    assert set(plikowe) <= _TAINTING_TOOLS
    # ``create_file`` NIE skaża: model zapisuje własną treść, nie wciąga cudzej.
    assert "create_file" in workspace and "create_file" not in _TAINTING_TOOLS
