"""Testy serwisu kompaktowania (``CompactionService``, ADR 0014).

Na PRAWDZIWYM magazynie SQLite (``:memory:``) + atrapie ``LLMClient``: sprawdzamy trigger po
progu, zachowanie N ostatnich wymian verbatim, archiwizację (bez usuwania) starych tur,
powtarzalność (nowe podsumowanie obejmuje poprzednie) oraz przypadki brzegowe — poniżej progu,
za mało tur, pusty wynik modelu. Prawdziwy store daje pewność, że ``last_input_tokens``,
``replay_messages`` i ``archive_through`` grają razem tak jak w produkcji.
"""

from __future__ import annotations

from datetime import datetime

from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.compaction import CompactionService, _describe_attachment
from workmate.core.domain.conversation import ConversationMessage
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import Attachment, LLMResponse, UserText, attachment_to_row

_TS = datetime(2025, 1, 1, 12, 0, 0)


class _FakeLLM:
    """Atrapa ``LLMClient``: zwraca ustalony tekst podsumowania i zapamiętuje wywołania."""

    def __init__(self, text: str = "PODSUMOWANIE", usage: TokenUsage | None = None) -> None:
        self.text = text
        self.usage = usage or TokenUsage(input_tokens=50, output_tokens=20)
        self.calls: list[tuple[str, list, list]] = []

    def complete(self, *, system, transcript, tools, trust_nonce=""):  # noqa: ANN001, ANN201
        self.calls.append((system, list(transcript), list(tools)))
        return LLMResponse(text=self.text, usage=self.usage)


def _store() -> SqliteConversationStore:
    return SqliteConversationStore(":memory:")


def _seed_exchange(
    store: SqliteConversationStore,
    conv_id: str,
    user_text: str,
    reply_text: str,
    *,
    input_tokens: int = 1,
) -> None:
    """Dołóż jedną wymianę (user + assistant z realnym ``usage``) — jak runtime po odpowiedzi."""
    store.append_message(conv_id, "user", user_text)
    store.append_message(
        conv_id,
        "assistant",
        reply_text,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=5),
    )


def test_compacts_over_threshold_keeps_last_n_and_archives_rest():
    store = _store()
    llm = _FakeLLM()
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=2)
    conv = store.open_conversation("cli", "chat")
    # 5 wymian; OSTATNIA niesie duże wejście (500 > próg 100) — to ono odpala kompaktowanie.
    for i in range(4):
        _seed_exchange(store, conv.id, f"pytanie {i}", f"odpowiedz {i}")
    _seed_exchange(store, conv.id, "ostatnie pytanie", "ostatnia odpowiedz", input_tokens=500)

    summary = service.maybe_compact(conv.id)

    assert summary is not None
    assert summary.summary == "PODSUMOWANIE"
    assert summary.usage == TokenUsage(input_tokens=50, output_tokens=20)
    # Zostają verbatim 2 ostatnie wymiany (4 tury), reszta (6 tur) zarchiwizowana.
    assert len(store.replay_messages(conv.id)) == 4
    assert sum(1 for m in store.messages(conv.id) if m.archived) == 6
    # Oryginały NIE usunięte — pełna historia (10 tur) nadal w bazie.
    assert len(store.messages(conv.id)) == 10
    assert store.active_summary(conv.id).summary == "PODSUMOWANIE"


def test_summary_covers_only_old_turns_not_kept_ones():
    store = _store()
    llm = _FakeLLM()
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=2)
    conv = store.open_conversation("cli", "chat")
    for i in range(3):
        _seed_exchange(store, conv.id, f"stare {i}", f"odp {i}")
    _seed_exchange(store, conv.id, "nowe pytanie", "nowa odp", input_tokens=500)

    service.maybe_compact(conv.id)

    # Transkrypt podany modelowi to STARE tury (do streszczenia), bez 2 ostatnich wymian.
    (_system, transcript, _tools) = llm.calls[0]
    flat = transcript[0].text
    assert isinstance(transcript[0], UserText)
    assert "stare 0" in flat and "stare 1" in flat
    assert "nowe pytanie" not in flat  # zachowana verbatim, nie trafia do streszczenia


def test_no_compaction_below_threshold():
    store = _store()
    llm = _FakeLLM()
    service = CompactionService(store, llm, threshold_tokens=1000, keep_turns=2)
    conv = store.open_conversation("cli", "chat")
    for i in range(5):
        _seed_exchange(store, conv.id, f"q{i}", f"a{i}", input_tokens=10)

    assert service.maybe_compact(conv.id) is None
    assert llm.calls == []  # model NIE wołany
    assert all(not m.archived for m in store.messages(conv.id))
    assert store.active_summary(conv.id) is None


def test_no_compaction_when_not_enough_turns():
    store = _store()
    llm = _FakeLLM()
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=4)
    conv = store.open_conversation("cli", "chat")
    # Tylko 2 wymiany (< keep_turns=4) — mimo przekroczenia progu nie ma czego streszczać.
    _seed_exchange(store, conv.id, "q0", "a0")
    _seed_exchange(store, conv.id, "q1", "a1", input_tokens=500)

    assert service.maybe_compact(conv.id) is None
    assert llm.calls == []
    assert all(not m.archived for m in store.messages(conv.id))


def test_empty_model_output_does_not_archive():
    store = _store()
    llm = _FakeLLM(text="   ")  # pusty/białoznakowy wynik modelu
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=2)
    conv = store.open_conversation("cli", "chat")
    for i in range(4):
        _seed_exchange(store, conv.id, f"q{i}", f"a{i}")
    _seed_exchange(store, conv.id, "q4", "a4", input_tokens=500)

    assert service.maybe_compact(conv.id) is None
    # Model wołany, ale pusty wynik → NIC nie archiwizujemy (bez utraty kontekstu).
    assert len(llm.calls) == 1
    assert all(not m.archived for m in store.messages(conv.id))
    assert store.active_summary(conv.id) is None


def test_repeatable_second_summary_includes_previous():
    store = _store()
    llm = _FakeLLM(text="PIERWSZE")
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=2)
    conv = store.open_conversation("cli", "chat")
    for i in range(4):
        _seed_exchange(store, conv.id, f"q{i}", f"a{i}")
    _seed_exchange(store, conv.id, "q4", "a4", input_tokens=500)

    first = service.maybe_compact(conv.id)
    assert first is not None and first.summary == "PIERWSZE"

    # Dokładamy nowe wymiany; ostatnia znów przekracza próg → drugie kompaktowanie.
    llm.text = "DRUGIE"
    _seed_exchange(store, conv.id, "q5", "a5")
    _seed_exchange(store, conv.id, "q6", "a6", input_tokens=500)

    second = service.maybe_compact(conv.id)

    assert second is not None and second.summary == "DRUGIE"
    # Drugie wywołanie modelu dostaje POPRZEDNIE podsumowanie w treści do streszczenia.
    (_system, transcript, _tools) = llm.calls[1]
    flat = transcript[0].text
    assert "Dotychczasowe podsumowanie" in flat
    assert "PIERWSZE" in flat
    # Aktywne pozostaje JEDNO — nowe zastępuje stare.
    assert store.active_summary(conv.id).summary == "DRUGIE"


def test_missing_conversation_returns_none():
    store = _store()
    service = CompactionService(store, _FakeLLM(), threshold_tokens=100, keep_turns=2)
    assert service.maybe_compact("nie-ma-takiej") is None


# --- Załączniki w streszczaczu: opis tekstowy, base64 NIGDY nie wchodzi (ADR 0016) ---


def _user_msg(text: str, blocks: list[dict]) -> ConversationMessage:
    return ConversationMessage(
        id=1, conversation_id="c", role="user", text=text, created_at=_TS, blocks=blocks
    )


def test_describe_attachment_image_is_label_only_without_base64():
    row = attachment_to_row(Attachment("image", "image/png", "zrzut.png", data_base64="QUJDUE5H"))

    desc = _describe_attachment(row)

    assert desc == "[Załącznik zrzut.png (image/png)]"
    assert "QUJDUE5H" not in desc  # base64 obrazu NIGDY do streszczacza


def test_describe_attachment_docx_includes_extracted_text():
    row = attachment_to_row(
        Attachment("text", "text/plain", "notatka.docx", text="Ustalenia ze spotkania")
    )

    desc = _describe_attachment(row)

    assert desc == "[Załącznik notatka.docx (text/plain)]\nUstalenia ze spotkania"


def test_flatten_describes_user_attachments_and_excludes_base64():
    """Spłaszczenie do modelu podsumowującego: opis tekstowy załącznika, bez base64."""
    store = _store()
    service = CompactionService(store, _FakeLLM(), threshold_tokens=100, keep_turns=2)
    img = attachment_to_row(Attachment("image", "image/png", "z.png", data_base64="TEEJBUE5H"))
    messages = [_user_msg("zobacz zrzut", [img])]

    flat = service._flatten(None, messages)

    assert "Użytkownik: zobacz zrzut" in flat
    assert "[Załącznik z.png (image/png)]" in flat
    assert "TEEJBUE5H" not in flat  # base64 poza streszczaczem


def test_flatten_keeps_a_trace_of_a_file_the_model_pulled_in(monkeypatch):
    """Plik podany przez ``File`` (ADR 0064) musi zostawić ślad w streszczeniu.

    Po kompaktowaniu streszczenie jest JEDYNYM, co z tury zostaje. Załącznik użytkownika
    dostawał choćby wiersz „[Załącznik …]", a plik z wiersza narzędziowego znikał bez śladu —
    model tracił wtedy nawet informację, że jakiś dokument w tej rozmowie w ogóle był.
    """
    store = _store()
    service = CompactionService(store, _FakeLLM(), threshold_tokens=100, keep_turns=2)
    plik = attachment_to_row(
        Attachment("document", "application/pdf", "umowa.pdf", data_base64="QkFTRTY0")
    )
    wiersz = ConversationMessage(
        id=2,
        conversation_id="c",
        role="tool",
        text="",
        created_at=_TS,
        blocks=[{"call_id": "t1", "content": '{"materialized": true}', "is_error": False}, plik],
    )

    flat = service._flatten(None, [wiersz])

    assert "[Załącznik umowa.pdf (application/pdf)]" in flat
    assert "QkFTRTY0" not in flat  # base64 poza streszczaczem, jak przy załączniku użytkownika
    assert "materialized" not in flat  # treść wyniku narzędzia dalej nie wchodzi
