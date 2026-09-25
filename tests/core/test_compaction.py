"""Testy serwisu kompaktowania (``CompactionService``, ADR 0014).

Na PRAWDZIWYM magazynie SQLite (``:memory:``) + atrapie ``LLMClient``: sprawdzamy trigger po
progu, zachowanie N ostatnich wymian verbatim, archiwizację (bez usuwania) starych tur,
powtarzalność (nowe podsumowanie obejmuje poprzednie) oraz przypadki brzegowe — poniżej progu,
za mało tur, pusty wynik modelu. Prawdziwy store daje pewność, że ``last_input_tokens``,
``replay_messages`` i ``archive_through`` grają razem tak jak w produkcji.
"""

from __future__ import annotations

from datetime import datetime

from sufler.adapters.outbound.sqlite_conversations import SqliteConversationStore
from sufler.core.application.compaction import CompactionService, _describe_attachment
from sufler.core.domain.conversation import ConversationMessage
from sufler.core.domain.pricing import TokenUsage
from sufler.core.ports.llm import Attachment, LLMResponse, UserText, attachment_to_row

_TS = datetime(2025, 1, 1, 12, 0, 0)


class _FakeLLM:
    """Atrapa ``LLMClient``: zwraca ustalony tekst podsumowania i zapamiętuje wywołania."""

    def __init__(self, text: str = "PODSUMOWANIE", usage: TokenUsage | None = None) -> None:
        self.text = text
        self.usage = usage or TokenUsage(input_tokens=50, output_tokens=20)
        self.calls: list[tuple[str, list, list]] = []
        self.nonces: list[str] = []

    def complete(self, *, system, transcript, tools, trust_nonce=""):  # noqa: ANN001, ANN201
        self.calls.append((system, list(transcript), list(tools)))
        self.nonces.append(trust_nonce)
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


def test_flatten_keeps_a_trace_of_a_file_the_model_pulled_in():
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
    assert "materialized" in flat  # od ADR 0068 §7 wynik narzędzia wchodzi (przycięty)


def test_history_reaches_the_summarizer_as_foreign_content():
    """Streszczacz dostaje historię w KOPERCIE (ADR 0066) — inaczej pierze treść obcą.

    Do streszczania idzie spłaszczony tekst, w którym siedzą wyniki narzędzi i tury nadawców
    spoza mapy. Bez koperty streszczacz czytał to jako instrukcje, a jego wynik wraca potem do
    rozmowy DOKLEJONY DO PIERWSZEJ TURY — czyli zatruta treść awansowała do rangi prozy
    instrukcyjnej dokładnie tam, gdzie nikt by jej nie szukał.
    """
    store = _store()
    llm = _FakeLLM()
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=2)
    conv = store.open_conversation("teams_graph", "chat")
    for i in range(4):
        _seed_exchange(store, conv.id, f"pytanie {i}", f"odpowiedz {i}")
    _seed_exchange(store, conv.id, "ostatnie", "ostatnia", input_tokens=500)

    service.maybe_compact(conv.id, trust_nonce="abcd1234")

    (_system, transcript, _tools) = llm.calls[0]
    assert transcript[0].text.startswith("<dane-obce:historia abcd1234>")
    assert llm.nonces == ["abcd1234"]  # nonce jedzie też do adaptera


def test_without_a_nonce_compaction_behaves_exactly_as_before():
    store = _store()
    llm = _FakeLLM()
    service = CompactionService(store, llm, threshold_tokens=100, keep_turns=2)
    conv = store.open_conversation("teams_graph", "chat")
    for i in range(4):
        _seed_exchange(store, conv.id, f"pytanie {i}", f"odpowiedz {i}")
    _seed_exchange(store, conv.id, "ostatnie", "ostatnia", input_tokens=500)

    service.maybe_compact(conv.id)

    (_system, transcript, _tools) = llm.calls[0]
    assert "dane-obce" not in transcript[0].text


# --- Wyniki narzedzi docieraja do streszczacza, przyciete (ADR 0068 §7) ---------------


def test_identyfikatory_z_wyniku_narzedzia_docieraja_do_streszczacza():
    """Prompt streszczacza prosi o identyfikatory, a materialu do nich NIE dostawal.

    Wiersz tury narzedziowej ma pusty `text` z definicji (`conversations._row_of`), a `_flatten`
    bral z niego wylacznie bloki BEZ `call_id`. Klucz Jiry i identyfikator notatki przezywaly
    kompaktowanie tylko wtedy, gdy model powtorzyl je wlasnymi slowami.
    """
    store = _store()
    service = CompactionService(store, _FakeLLM(), threshold_tokens=100, keep_turns=2)
    wiersz = ConversationMessage(
        id=2,
        conversation_id="c",
        role="tool",
        text="",
        created_at=_TS,
        blocks=[
            {
                "call_id": "t1",
                "content": '{"count": 1, "tasks": [{"key": "WT-42", "summary": "SCADA"}]}',
                "is_error": False,
            }
        ],
    )

    flat = service._flatten(None, [wiersz])

    assert "WT-42" in flat
    assert flat.startswith("Narzędzie:")


def test_dlugi_wynik_narzedzia_wchodzi_przyciety_i_mowi_o_tym():
    """Verbatim wynik potrafi mieć setki kilobajtów — przyciecie ma byc widoczne, nie ciche."""
    store = _store()
    service = CompactionService(store, _FakeLLM(), threshold_tokens=100, keep_turns=2)
    wiersz = ConversationMessage(
        id=2,
        conversation_id="c",
        role="tool",
        text="",
        created_at=_TS,
        blocks=[{"call_id": "t1", "content": "x" * 5000, "is_error": False}],
    )

    flat = service._flatten(None, [wiersz])

    assert len(flat) < 1000
    assert "przycięty" in flat
