"""Testy serwisu rozmów (``ConversationService``, ADR 0010/0012, Design 2).

Logika bez I/O: atrapa ``ConversationStore`` w pamięci. Rozliczenie tokenów jest REALNE
(z ``usage``), a rollover bramkuje realny kontekst OSTATNIEJ tury (``last_context_tokens``).
Atrapa liczy agregaty jak prawdziwy magazyn: sumę usage, kontekst ostatniej tury asystenta
oraz liczbę tur (sygnał „niepusty" dla bramek idle/``/nowa``).
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sufler.core.application.conversations import ConversationService
from sufler.core.domain.conversation import (
    Conversation,
    ConversationMessage,
    ConversationSearchHit,
    ConversationSummary,
)
from sufler.core.domain.pricing import TokenUsage
from sufler.core.ports.llm import (
    AssistantTurn,
    Attachment,
    RawTurn,
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
        self.summaries: dict[str, list[ConversationSummary]] = {}
        self._conv_seq = 0
        self._msg_seq = 0
        self._sum_seq = 0

    def _enrich(self, conv: Conversation) -> Conversation:
        """Dolicz realne agregaty (jak prawdziwy magazyn): usage, kontekst ost. tury, count."""
        msgs = self.msgs[conv.id]
        usage = TokenUsage()
        for m in msgs:
            if m.usage is not None:
                usage = usage + m.usage
        last_ctx = 0
        last_in = 0
        for m in reversed(msgs):
            if m.role == "assistant" and m.usage is not None:
                # kontekst ost. tury = input + cache + output (jak realny _last_context_tokens);
                # wejście ost. tury = input + cache, BEZ output (jak _last_input_tokens, ADR 0014).
                last_ctx = m.usage.total_tokens
                last_in = (
                    m.usage.input_tokens
                    + m.usage.cache_read_input_tokens
                    + m.usage.cache_creation_input_tokens
                )
                break
        return conv.model_copy(
            update={
                "usage": usage,
                "last_context_tokens": last_ctx,
                "last_input_tokens": last_in,
                "message_count": len(msgs),
            }
        )

    def active_conversation(self, channel: str, external_id: str) -> Conversation | None:
        actives = [
            c
            for c in self.conversations.values()
            if c.channel == channel and c.external_id == external_id and c.status == "active"
        ]
        return self._enrich(actives[-1]) if actives else None

    def open_conversation(self, channel: str, external_id: str) -> Conversation:
        self._conv_seq += 1
        cid = f"c{self._conv_seq}"
        conv = Conversation(
            id=cid,
            channel=channel,
            external_id=external_id,
            status="active",
            created_at=_TS,
            updated_at=_TS,
        )
        self.conversations[cid] = conv
        self.msgs[cid] = []
        self.summaries[cid] = []
        return conv

    def close_conversation(self, conversation_id: str) -> None:
        conv = self.conversations[conversation_id]
        self.conversations[conversation_id] = conv.model_copy(update={"status": "closed"})

    def append_message(
        self,
        conversation_id: str,
        role: str,
        text: str,
        *,
        blocks: list[dict] | None = None,
        stop_reason: str | None = None,
        usage: TokenUsage | None = None,
        trust: str | None = None,
    ) -> ConversationMessage:
        self._msg_seq += 1
        msg = ConversationMessage(
            id=self._msg_seq,
            conversation_id=conversation_id,
            role=role,
            text=text,
            created_at=_TS,
            blocks=blocks,
            stop_reason=stop_reason,
            usage=usage,
            # Atrapa MUSI utrwalać klasę pochodzenia (ADR 0066) jak realny magazyn. Dopóki ją
            # połykała, ``record_run`` mógł przestać przekazywać ``trust`` i żadna sonda by tego
            # nie zauważyła — atrapa, która gubi argument, zamienia test w potwierdzenie samej
            # siebie.
            trust=trust,
        )
        self.msgs[conversation_id].append(msg)
        return msg

    def mark_tainted(self, conversation_id: str, source: str) -> None:
        """Lepka skaza (ADR 0066): zapala się raz, źródło zostaje z PIERWSZEGO zapłonu."""
        conv = self.conversations[conversation_id]
        if conv.tainted:
            return
        self.conversations[conversation_id] = conv.model_copy(
            update={"tainted": True, "taint_source": source}
        )

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        return list(self.msgs[conversation_id])

    def get(self, conversation_id: str) -> Conversation | None:
        conv = self.conversations.get(conversation_id)
        return self._enrich(conv) if conv is not None else None

    def replay_messages(self, conversation_id: str) -> list[ConversationMessage]:
        return [m for m in self.msgs[conversation_id] if not m.archived]

    def archive_through(self, conversation_id: str, message_id: int) -> None:
        for m in self.msgs[conversation_id]:
            if m.id <= message_id:
                m.archived = True

    def save_summary(
        self,
        conversation_id: str,
        summary: str,
        covers_through_message_id: int,
        *,
        usage: TokenUsage | None = None,
    ) -> ConversationSummary:
        # Zastąp poprzednie aktywne (jak w prawdziwym magazynie — aktywne co najwyżej jedno).
        self.summaries[conversation_id] = []
        self._sum_seq += 1
        rec = ConversationSummary(
            id=self._sum_seq,
            conversation_id=conversation_id,
            summary=summary,
            covers_through_message_id=covers_through_message_id,
            created_at=_TS,
            usage=usage,
        )
        self.summaries[conversation_id].append(rec)
        return rec

    def active_summary(self, conversation_id: str) -> ConversationSummary | None:
        recs = self.summaries.get(conversation_id, [])
        return recs[-1] if recs else None

    def list_conversations(
        self, *, channel: str | None = None, external_id: str | None = None, limit: int = 50
    ) -> list[Conversation]:
        # Najnowsze pierwsze: dict trzyma kolejność wstawiania, reversed → od końca.
        # ``limit`` OBOWIĄZUJE PO filtrach — jak w SQL-u magazynu. Atrapa tnąca przed filtrem
        # ukryłaby dokładnie ten defekt, który parametr ``external_id`` usuwa.
        picked = [
            c
            for c in reversed(self.conversations.values())
            if (channel is None or c.channel == channel)
            and (external_id is None or c.external_id == external_id)
        ][:limit]
        return [self._enrich(c) for c in picked]

    def search(
        self,
        query: str,
        *,
        channel: str | None = None,
        external_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationSearchHit]:
        """Prymitywne szukanie po podłańcuchu — atrapa ma ODPOWIADAĆ na filtry, nie udawać.

        Poprzednia wersja zwracała zawsze ``[]``, więc każda sonda delegacji przechodziła także
        wtedy, gdy serwis gubił filtry po drodze.
        """
        hits: list[ConversationSearchHit] = []
        for conv in self.conversations.values():
            if channel is not None and conv.channel != channel:
                continue
            if external_id is not None and conv.external_id != external_id:
                continue
            for m in self.msgs[conv.id]:
                if query.lower() in m.text.lower():
                    hits.append(
                        ConversationSearchHit(
                            conversation_id=conv.id,
                            channel=conv.channel,
                            external_id=conv.external_id,
                            role=m.role,
                            text=m.text,
                            snippet=m.text,
                            created_at=m.created_at,
                        )
                    )
        return hits[:limit]


def _assistant_turn(text: str, usage: TokenUsage) -> AssistantTurn:
    """Tura asystenta niosąca realne ``usage`` (jak z runtime po odpowiedzi API)."""
    return AssistantTurn(text, (), ({"type": "text", "text": text},), usage=usage)


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


def test_rollover_on_real_context_limit():
    """Design 2: rollover, gdy REALNY kontekst ostatniej tury (usage) osiągnął próg."""
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=100)

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "q")
    # Tura z realnym usage: kontekst = input(200)+output(10) = 210 ≥ próg 100.
    service.record_run(
        cid1,
        (UserText("q"), _assistant_turn("a", TokenUsage(input_tokens=200, output_tokens=10))),
    )
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "q2")

    assert rolled_over is True
    assert cid2 != cid1
    assert history == []  # świeży kontekst nowej rozmowy
    assert store.conversations[cid1].status == "closed"


def test_no_rollover_below_real_context_limit():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "q")
    service.record_run(
        cid1,
        (UserText("q"), _assistant_turn("a", TokenUsage(input_tokens=50, output_tokens=5))),
    )
    cid2, _, rolled_over = service.prepare_turn("telegram", "chat1", "q2")

    assert rolled_over is False  # kontekst 55 < próg 1000
    assert cid2 == cid1


# --- Rollover po bezczynności (ADR 0012) — na message_count, niezależnie od usage -----
# _FakeStore.open_conversation nadaje updated_at=_TS i append go nie zmienia, więc
# „bezczynność" symulujemy przez ``now`` odległe od _TS — deterministycznie, bez zegara.


def test_rollover_starts_new_thread_after_idle_gap():
    store = _FakeStore()
    service = ConversationService(
        store, max_context_tokens=1000, idle_timeout=timedelta(minutes=30)
    )

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    service.record_turn(cid1, "czesc", "hej")  # message_count > 0 (wątek niepusty)
    later = _TS + timedelta(minutes=31)  # 31 min bezczynności > próg 30 min
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "wracam", now=later)

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
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "dalej", now=soon)

    assert rolled_over is False
    assert cid2 == cid1
    assert [(m.role, m.text) for m in history] == [("user", "czesc"), ("assistant", "hej")]


def test_idle_disabled_keeps_single_thread_across_long_gap():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)  # idle_timeout=None

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    service.record_turn(cid1, "czesc", "hej")
    much_later = _TS + timedelta(days=7)
    cid2, _, rolled_over = service.prepare_turn("telegram", "chat1", "po tygodniu", now=much_later)

    assert rolled_over is False  # wyłączone kryterium → tylko limit kontekstu
    assert cid2 == cid1


def test_empty_thread_not_rolled_over_on_idle():
    store = _FakeStore()
    service = ConversationService(
        store, max_context_tokens=1000, idle_timeout=timedelta(minutes=30)
    )

    # Pierwsza tura otwiera PUSTY wątek (message_count=0) — brak tur do odcięcia.
    cid1, _, _ = service.prepare_turn("telegram", "chat1", "czesc", now=_TS)
    later = _TS + timedelta(minutes=31)
    cid2, _, rolled_over = service.prepare_turn("telegram", "chat1", "kolejna", now=later)

    assert rolled_over is False  # pustego wątku nie rollujemy mimo przekroczonej przerwy
    assert cid2 == cid1


# --- Jawny start wątku na żądanie: start_new_thread (ADR 0012 / komenda) --------


def test_start_new_thread_closes_active_nonempty_and_next_turn_opens_fresh():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("telegram", "chat1", "czesc")
    service.record_turn(cid, "czesc", "hej")  # wątek niepusty (message_count > 0)

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


def test_list_conversations_delegates_with_channel_filter_and_real_usage_sum():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    tg = store.open_conversation("telegram", "chat1")
    store.open_conversation("teams", "conv1")
    store.append_message(
        tg.id, "assistant", "odp", usage=TokenUsage(input_tokens=5, output_tokens=7)
    )

    result = service.list_conversations(channel="telegram")

    assert [c.id for c in result] == [tg.id]  # filtr kanału przekazany do magazynu
    assert result[0].usage.total_tokens == 12  # realna suma usage rozmowy (5 + 7)


def test_list_conversations_narrows_to_ONE_thread_when_asked():
    """``/historia`` ma pokazywać wyłącznie wątek pytającego — zawężenie należy do magazynu.

    Filtr po stronie wołającego działał nad oknem ``limit``, więc wątek, którego rozmowy z tego
    okna wypadły, dostawał wynik nieodróżnialny od „nie ma historii". Metadane cudzych rozmów
    (kiedy, ile tur, ile tokenów) i tak nie miały prawa opuścić swojego wątku.
    """
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    moja = store.open_conversation("teams_graph", "moj-watek")
    store.open_conversation("teams_graph", "cudzy-watek")

    result = service.list_conversations(channel="teams_graph", external_id="moj-watek")

    assert [c.id for c in result] == [moja.id]


def test_list_conversations_applies_the_limit_AFTER_narrowing_to_the_thread():
    """Sedno kosztu i sedno pomyłki naraz: sufit ma dotyczyć już ZAWĘŻONEGO zbioru.

    Gdy ``limit`` obowiązywał przed filtrem, wątek z jedną starą rozmową ginął za oknem cudzych
    (wynik pusty = „brak historii"), a magazyn i tak policzył koszt każdego wiersza okna —
    trzy zapytania na rozmowę, którą zaraz odrzucał.
    """
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    moja = store.open_conversation("teams_graph", "moj-watek")
    for i in range(50):
        store.open_conversation("teams_graph", f"cudzy-{i}")  # nowsze, wypchnęłyby moją z okna

    result = service.list_conversations(channel="teams_graph", external_id="moj-watek", limit=10)

    assert [c.id for c in result] == [moja.id]


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
    store.append_message(conv.id, "user", "pytanie")
    store.append_message(conv.id, "assistant", "odpowiedz")

    got = service.messages(conv.id)
    assert [(m.role, m.text) for m in got] == [
        ("user", "pytanie"),
        ("assistant", "odpowiedz"),
    ]


# --- record_run: bezstratne mapowanie wpisów na wiersze (ADR 0011 + usage) ------


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


def test_record_run_stores_real_usage_only_on_assistant_row():
    """Design 2: realne ``usage`` trafia na wiersz asystenta; user/tool → None."""
    usage = TokenUsage(input_tokens=100, output_tokens=20, cache_read_input_tokens=5)
    entries = (
        UserText("q"),
        AssistantTurn("ok", (), ({"type": "text", "text": "ok"},), usage=usage),
    )

    rows = _record(entries)

    assert rows[0].usage is None  # user — bez rozliczenia
    assert rows[1].usage == usage  # assistant — realne usage zapisane


def test_record_run_drops_empty_usage_to_none():
    """Tura asystenta bez realnego usage (atrapa/legacy) → wiersz bez rozliczenia (None)."""
    entries = (AssistantTurn("ok", (), ({"type": "text", "text": "ok"},)),)  # usage domyślne 0

    rows = _record(entries)

    assert rows[0].usage is None


# --- record_run: załączniki użytkownika w blocks, base64 poza projekcją FTS (ADR 0016) ---


def test_record_run_serializes_user_attachments_into_blocks_not_text():
    """UserText z załącznikami: caption idzie do ``text`` (FTS/podgląd), a NEUTRALNA forma
    załączników do ``blocks``. base64 NIGDY nie wchodzi do projekcji tekstu."""
    img = Attachment("image", "image/png", "zrzut.png", data_base64="QUJDUE5H")
    entries = (UserText("opis obrazu", (img,)),)

    rows = _record(entries)

    assert rows[0].role == "user"
    assert rows[0].text == "opis obrazu"  # tylko caption w projekcji tekstu
    assert "QUJDUE5H" not in rows[0].text  # base64 poza FTS
    assert rows[0].blocks == [
        {
            "kind": "image",
            "media_type": "image/png",
            "name": "zrzut.png",
            "data_base64": "QUJDUE5H",
            "text": "",
        }
    ]


def test_record_run_user_without_attachments_has_none_blocks():
    """Brak załączników → ``blocks`` None (wiersz text-only, jak dotąd)."""
    rows = _record((UserText("czysty tekst"),))

    assert rows[0].blocks is None
    assert rows[0].text == "czysty tekst"


def test_record_run_attachment_only_message_keeps_blocks_with_empty_text():
    """Wiadomość z SAMYM załącznikiem (pusty caption) — blocks obecne, text pusty."""
    pdf = Attachment("document", "application/pdf", "umowa.pdf", data_base64="UERG")
    rows = _record((UserText("", (pdf,)),))

    assert rows[0].text == ""
    assert rows[0].blocks is not None
    assert rows[0].blocks[0]["kind"] == "document"


# --- record_run: plik podany przez ``File`` wraca do pamięci wraz z turą (ADR 0064) ---


def test_record_run_stores_tool_materialized_files_next_to_the_tool_results():
    """Bez tego replay z pamięci oddałby model bez materiału, o którym mówi jego własna tura.

    ``File(action='read')`` nie wkłada bajtów do ``ToolOutput.content`` (``tool_result`` nie
    unosi bloku ``document`` i bywa czyszczony przez edycję kontekstu, ADR 0058) — jedzie
    osobną kolejką i ląduje w TYCH SAMYCH ``blocks`` co wyniki narzędzi.
    """
    pdf = Attachment("document", "application/pdf", "umowa.pdf", data_base64="UERG")
    entries = (ToolResults((ToolOutput("t1", '{"materialized": true}'),), (pdf,)),)

    rows = _record(entries)

    assert rows[0].role == "tool"
    assert rows[0].blocks is not None
    # Wynik narzędzia (``call_id``) i materiał (``kind``) rozróżnia OBECNOŚĆ klucza, nie kolejność.
    wyniki = [b for b in rows[0].blocks if "call_id" in b]
    materialy = [b for b in rows[0].blocks if "kind" in b]
    assert [b["call_id"] for b in wyniki] == ["t1"]
    assert [b["name"] for b in materialy] == ["umowa.pdf"]


def test_record_run_tool_results_without_files_carry_only_the_outputs():
    rows = _record((ToolResults((ToolOutput("t1", "{}"),)),))

    assert rows[0].blocks == [{"call_id": "t1", "content": "{}", "is_error": False}]


def test_record_run_replays_a_raw_turn_without_interpreting_its_blocks():
    """``RawTurn`` to tura ODTWORZONA z pamięci — bloki mają wrócić bajt w bajt, tekst pusty."""
    bloki = ({"type": "thinking", "thinking": "", "signature": "SIG=="},)

    rows = _record((RawTurn("assistant", bloki),))

    assert (rows[0].role, rows[0].text) == ("assistant", "")
    assert rows[0].blocks == [dict(bloki[0])]


def test_record_run_rejects_an_unknown_transcript_entry_instead_of_dropping_it():
    """Nieznany wpis to DEFEKT kodu, nie dana wejściowa — cichy ``continue`` gubiłby turę."""

    class _Obcy:
        trust = None

    with pytest.raises(TypeError, match="_Obcy"):
        _record((_Obcy(),))


# --- ADR 0066: klasa pochodzenia tury i lepka skaza rozmowy --------------------


def test_record_run_persists_the_trust_class_of_the_user_turn():
    """Klasa pochodzenia jest nadawana przy DRZWIACH i musi dojechać do wiersza magazynu.

    Bez utrwalenia audyt mutacji (ADR 0065) nie ma jak powiedzieć, czy zmianę zlecił nadawca
    rozpoznany (T1), czy gość, którego słowa są danymi (T2).
    """
    rows = _record((UserText("skasuj notatkę", trust="T2"),))

    assert rows[0].trust == "T2"


def test_default_trust_is_the_instruction_class_for_doors_without_a_sender():
    """CLI z jednym operatorem nie zna nadawcy — domyślne ``T1`` zachowuje dawne działanie."""
    rows = _record((UserText("czesc"),))

    assert rows[0].trust == "T1"


def test_only_the_user_turn_carries_a_trust_class():
    """Model i narzędzia nie mają nadawcy do rozwiązania — ich wiersze zostają bez klasy."""
    entries = (
        UserText("q", trust="T2"),
        AssistantTurn("a", (), ({"type": "text", "text": "a"},)),
        ToolResults((ToolOutput("t1", "{}"),)),
    )

    rows = _record(entries)

    assert [r.trust for r in rows] == ["T2", None, None]


def test_fresh_conversation_is_not_tainted():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("teams", "chat1", "czesc")

    assert service.is_tainted(cid) is False


def test_marking_taint_is_visible_through_the_service():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("teams", "chat1", "czesc")

    service.mark_tainted(cid, "plik: umowa.pdf")

    assert service.is_tainted(cid) is True
    assert store.conversations[cid].taint_source == "plik: umowa.pdf"


def test_taint_is_sticky_and_keeps_the_first_source():
    """Raz zapalona skaza nie gaśnie, a źródło zostaje z PIERWSZEGO zapłonu.

    Nadpisywanie źródła każdym kolejnym plikiem zamieniłoby ślad „od kiedy i przez co" w
    migawkę ostatniej tury — czyli skasowałoby informację o początku eskalacji.
    """
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("teams", "chat1", "czesc")

    service.mark_tainted(cid, "plik: umowa.pdf")
    service.mark_tainted(cid, "narzędzie: github")

    assert service.is_tainted(cid) is True
    assert store.conversations[cid].taint_source == "plik: umowa.pdf"


def test_rollover_opens_a_clean_conversation_after_a_tainted_one():
    """Skaza żyje w WIERSZU rozmowy, więc nowy wątek startuje czysty — i to jest zamierzone:
    skażona treść wypadła z kontekstu razem z domkniętą rozmową."""
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=100)

    cid1, _, _ = service.prepare_turn("teams", "chat1", "q")
    service.mark_tainted(cid1, "plik: umowa.pdf")
    service.record_run(
        cid1,
        (UserText("q"), _assistant_turn("a", TokenUsage(input_tokens=200, output_tokens=10))),
    )
    cid2, _, rolled_over = service.prepare_turn("teams", "chat1", "q2")

    assert rolled_over is True
    assert service.is_tainted(cid1) is True  # domknięta rozmowa zachowuje ślad
    assert service.is_tainted(cid2) is False


def test_taint_of_an_unknown_conversation_reads_as_clean_not_as_a_crash():
    """Odczyt ma degradować, nie wywracać tury — ale sonda pilnuje, że to ŚWIADOMY domysł."""
    service = ConversationService(_FakeStore(), max_context_tokens=1000)

    assert service.is_tainted("nie-ma-takiej") is False


# --- Konfiguracja serwisu ------------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1, -1000])
def test_service_refuses_a_context_limit_below_one_turn(bad: int):
    """Limit < 1 rollowałby KAŻDĄ turę: rozmowa nigdy nie zebrałaby historii, a koszt rósłby
    o pusty wątek na wiadomość. Lepiej odmówić przy budowie niż działać bezsensownie."""
    with pytest.raises(ValueError, match="max_context_tokens"):
        ConversationService(_FakeStore(), max_context_tokens=bad)


# --- Delegacje odczytu: search / active_conversation / replay / summary --------


def test_search_passes_the_channel_and_conversation_filters_to_the_store():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    tg = store.open_conversation("telegram", "chat1")
    teams = store.open_conversation("teams", "chat2")
    store.append_message(tg.id, "user", "termin odbioru SCADA")
    store.append_message(teams.id, "user", "termin odbioru SCADA")

    hits = service.search("termin", channel="teams", external_id="chat2")

    assert [h.conversation_id for h in hits] == [teams.id]


def test_search_respects_the_limit():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    conv = store.open_conversation("cli", "a")
    for i in range(5):
        store.append_message(conv.id, "user", f"termin {i}")

    assert len(service.search("termin", limit=2)) == 2


def test_active_conversation_returns_none_after_the_thread_is_closed():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    cid, _, _ = service.prepare_turn("cli", "a", "czesc")
    service.record_turn(cid, "czesc", "hej")

    assert service.active_conversation("cli", "a") is not None
    service.start_new_thread("cli", "a")
    assert service.active_conversation("cli", "a") is None


def test_replay_skips_archived_turns_while_messages_keeps_them():
    """Rozjazd obu odczytów jest CELEM (ADR 0014): podgląd widzi całość, API — skrót."""
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    conv = store.open_conversation("cli", "a")
    stara = store.append_message(conv.id, "user", "stara tura")
    store.append_message(conv.id, "user", "świeża tura")
    store.archive_through(conv.id, stara.id)

    assert [m.text for m in service.messages(conv.id)] == ["stara tura", "świeża tura"]
    assert [m.text for m in service.replay_messages(conv.id)] == ["świeża tura"]


def test_active_summary_is_none_before_the_first_compaction():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)
    conv = store.open_conversation("cli", "a")

    assert service.active_summary(conv.id) is None

    store.save_summary(conv.id, "streszczenie", 1)
    got = service.active_summary(conv.id)
    assert got is not None and got.summary == "streszczenie"
