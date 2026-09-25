"""Testy runtime'u agenta (Faza 2, M1) — atrapa ``LLMClient``, bez sieci.

Runtime zależy tylko od portu LLM i katalogu narzędzi, więc scenariusze pętli
tool-use sprawdzamy atrapą zwracającą zaplanowane odpowiedzi (jak atrapy repo w
``conftest.py``).
"""

from __future__ import annotations

from datetime import date

import pytest

from sufler.core.agent.runtime import AgentRuntime
from sufler.core.application.tools import ToolSpec
from sufler.core.domain.pricing import TokenUsage
from sufler.core.ports.llm import (
    AssistantTurn,
    Attachment,
    AttachmentQueue,
    LLMResponse,
    ToolCall,
    ToolResults,
    UserText,
)


def _spec(name: str, fn) -> ToolSpec:
    return ToolSpec(name, name, fn)


class _ScriptedLLM:
    """Atrapa ``LLMClient``: oddaje kolejne zaplanowane odpowiedzi, zapamiętuje transkrypty."""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.transcripts: list[list] = []
        self.tools_seen: list[list[str]] = []
        self.nonces_seen: list[str] = []

    def complete(self, *, system, transcript, tools, trust_nonce=""):
        self.transcripts.append(list(transcript))
        self.tools_seen.append([t.name for t in tools])
        self.nonces_seen.append(trust_nonce)
        return self._responses.pop(0)


def test_runtime_returns_text_when_model_stops():
    llm = _ScriptedLLM([LLMResponse(text="gotowe")])

    result = AgentRuntime(llm, []).run("pytanie")

    assert result == "gotowe"


def test_runtime_dispatches_tool_and_feeds_result_back():
    seen: list[str] = []

    def search(query: str) -> dict:
        seen.append(query)
        return {"count": 1, "results": ["x"]}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search_notes", {"query": "mpwik"}),)),
            LLMResponse(text="Znalazłem 1 notatkę."),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", search)]).run("co z mpwik?")

    assert result == "Znalazłem 1 notatkę."
    assert seen == ["mpwik"]  # narzędzie dostało argumenty od modelu
    # druga tura widzi wynik narzędzia w transkrypcie
    second = llm.transcripts[1]
    tool_results = [e for e in second if isinstance(e, ToolResults)]
    assert tool_results and '"count": 1' in tool_results[0].outputs[0].content


def test_runtime_marks_unknown_tool_as_error():
    def real(query: str) -> dict:
        return {"ok": True}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "nieistnieje", {}),)),
            LLMResponse(text="ok"),
        ]
    )

    AgentRuntime(llm, [_spec("real", real)]).run("x")

    outputs = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)][0].outputs
    assert outputs[0].is_error is True


def test_runtime_merges_extra_tools_for_the_turn():
    """``extra_tools`` (katalog roboczy, ADR 0018) — widoczne modelowi i dispatchowane w turze."""
    created: list[str] = []

    def create_file(name: str, content: str) -> dict:
        created.append(name)
        return {"created": True, "name": name}

    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=(ToolCall("t1", "create_file", {"name": "r.md", "content": "x"}),)
            ),
            LLMResponse(text="Zapisałem plik."),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", lambda query: {})]).run(
        "zapisz plik", extra_tools=[_spec("create_file", create_file)]
    )

    assert result == "Zapisałem plik."
    assert created == ["r.md"]  # narzędzie dodane per turę zostało wykonane
    # Model widział ZARÓWNO bazowe, jak i dodane narzędzie w tej turze.
    assert "create_file" in llm.tools_seen[0] and "search_notes" in llm.tools_seen[0]


def test_runtime_returns_recoverable_error_for_bad_tool_arguments():
    """Model podał złą nazwę argumentu → błąd narzędzia, pętla się NIE wywraca."""

    def only_query(query: str) -> dict:
        return {"ok": True}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search", {"nieznany_arg": 1}),)),
            LLMResponse(text="poprawiłem"),
        ]
    )

    result = AgentRuntime(llm, [_spec("search", only_query)]).run("x")

    assert result == "poprawiłem"  # zapytanie się nie wywróciło
    outputs = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)][0].outputs
    assert outputs[0].is_error is True
    assert "search" in outputs[0].content


def test_runtime_coerces_json_shaped_arguments_to_annotated_types():
    """Model przysyła datę jako NAPIS (bo taki widzi schemat) → narzędzie dostaje ``date``.

    Regresja blokera: JSON nie zna typów Pythona, więc ``date`` jedzie do modelu jako
    ``{"type": "string", "format": "date"}``. Bez koercji napis trafiał wprost do
    arytmetyki dat, a ``TypeError`` nie jest łapany przez kopertę narzędzi — wyjątek
    wychodził PRZED oznaczeniem wiadomości jako obsłużonej i blokował kanał.
    """
    seen: list[object] = []

    def propose(since: date, until: date) -> dict:
        seen.append((since, until))
        return {"dni": (until - since).days}

    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=(
                    ToolCall("t1", "propose", {"since": "2026-07-13", "until": "2026-07-19"}),
                )
            ),
            LLMResponse(text="gotowe"),
        ]
    )

    result = AgentRuntime(llm, [_spec("propose", propose)]).run("x")

    assert result == "gotowe"
    assert seen == [(date(2026, 7, 13), date(2026, 7, 19))]
    outputs = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)][0].outputs
    assert outputs[0].is_error is False
    assert '"dni": 6' in outputs[0].content


def test_runtime_returns_recoverable_error_for_unparsable_typed_argument():
    """Nazwa argumentu poprawna, ale wartości nie da się zrzutować → błąd, nie wyjątek."""

    def propose(since: date) -> dict:
        return {"ok": True}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "propose", {"since": "zeszły wtorek"}),)),
            LLMResponse(text="poprawiłem"),
        ]
    )

    result = AgentRuntime(llm, [_spec("propose", propose)]).run("x")

    assert result == "poprawiłem"  # pętla przeżyła
    outputs = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)][0].outputs
    assert outputs[0].is_error is True
    assert "propose" in outputs[0].content


def test_runtime_respects_iteration_budget():
    """Model zawsze chce narzędzia → pętla zatrzymuje się po budżecie iteracji."""

    def tool(**_kwargs) -> dict:
        return {"ok": True}

    class _AlwaysTool:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, *, system, transcript, tools, trust_nonce=""):
            self.calls += 1
            return LLMResponse(text="myślę", tool_calls=(ToolCall("t", "tool", {}),))

    llm = _AlwaysTool()
    result = AgentRuntime(llm, [_spec("tool", tool)], max_tool_iterations=3).run("x")

    assert llm.calls == 3
    assert result == "myślę"  # ostatni tekst zwrócony po wyczerpaniu budżetu


# --- run_turn: wpisy DO ZAPISU + stop_reason (ADR 0011) ------------------------


def test_run_turn_truncated_turn_persists_nothing():
    """``max_tokens`` = tura niedomknięta: NIC nie zapisujemy (inwariant zapisu, ADR 0011).

    Pamięć trzyma tylko pełne wymiany kończące się turą asystenta — dzięki temu historia
    nie kończy się turą user (brak dwóch tur user z rzędu na kolejnej wiadomości).
    """
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="czę", blocks=({"type": "text", "text": "czę"},), stop_reason="max_tokens"
            )
        ]
    )

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.stop_reason == "max_tokens"
    assert result.reply == "czę"  # zwracamy to, co model zdążył napisać
    assert result.entries == ()  # niedomknięta tura → pusto (bez sieroty)


def test_run_turn_final_turn_carries_blocks_verbatim():
    """Zwykła odpowiedź końcowa: entries = user + assistant z blokami VERBATIM."""
    blocks = ({"type": "thinking", "signature": "S"}, {"type": "text", "text": "gotowe"})
    llm = _ScriptedLLM([LLMResponse(text="gotowe", blocks=blocks, stop_reason="end_turn")])

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.stop_reason == "end_turn"
    assert result.entries == (UserText("pytanie"), AssistantTurn("gotowe", (), blocks))


def test_run_turn_surfaces_thinking_summary_on_final_answer():
    """``AgentResult.thinking`` niesie podsumowanie rozumowania końcowej tury (summarized)."""
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="gotowe",
                thinking_text="Rozważam notatki mpwik.",
                blocks=({"type": "text", "text": "gotowe"},),
                stop_reason="end_turn",
            )
        ]
    )

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.thinking == "Rozważam notatki mpwik."
    assert result.reply == "gotowe"


def test_run_turn_accumulates_real_usage_across_tool_loop():
    """Design 2: ``AgentResult.usage`` = suma usage wszystkich wywołań; każda tura niesie swoje."""

    def search(query: str) -> dict:
        return {"count": 1}

    llm = _ScriptedLLM(
        [
            LLMResponse(
                tool_calls=(ToolCall("t1", "search_notes", {"query": "x"}),),
                usage=TokenUsage(input_tokens=100, output_tokens=10),
            ),
            LLMResponse(
                text="ok",
                stop_reason="end_turn",
                usage=TokenUsage(input_tokens=150, output_tokens=20),
            ),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", search)]).run_turn("q")

    assert result.usage == TokenUsage(input_tokens=250, output_tokens=30)  # suma dwóch wywołań
    assistants = [e for e in result.entries if isinstance(e, AssistantTurn)]
    assert assistants[0].usage == TokenUsage(input_tokens=100, output_tokens=10)
    assert assistants[1].usage == TokenUsage(input_tokens=150, output_tokens=20)


def test_run_turn_wraps_attachments_into_user_text_in_transcript_and_entries():
    """``attachments`` trafiają do ``UserText(query, attachments)`` — w transkrypcie do
    modelu ORAZ w zapisanych ``entries`` (bezstratna pamięć multimodalna, ADR 0016)."""
    att = Attachment("image", "image/png", "z.png", data_base64="QUJD")
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="widzę obraz",
                blocks=({"type": "text", "text": "widzę obraz"},),
                stop_reason="end_turn",
            )
        ]
    )
    runtime = AgentRuntime(llm, [])

    result = runtime.run_turn("co tu jest?", attachments=(att,))

    # Wiadomość użytkownika w transkrypcie wysłanym do modelu niesie załącznik.
    user_turn = llm.transcripts[0][-1]
    assert user_turn == UserText("co tu jest?", (att,))
    # ...i ta sama forma trafia do entries do zapisu (pierwszy wpis).
    assert result.entries[0] == UserText("co tu jest?", (att,))


def test_run_defaults_to_no_attachments():
    """``run``/``run_turn`` bez ``attachments`` → pusta krotka (zgodność wsteczna)."""
    llm = _ScriptedLLM([LLMResponse(text="ok", stop_reason="end_turn")])

    result = AgentRuntime(llm, []).run_turn("pytanie")

    assert result.entries[0] == UserText("pytanie", ())


def test_run_turn_persists_paired_tool_cycle_entries():
    """Cykl tool_use utrwala SPAROWANE wpisy: user, assistant(+tool), tool, assistant."""

    def search(query: str) -> dict:
        return {"count": 1}

    assistant_blocks = ({"type": "text", "text": "szukam"},)
    llm = _ScriptedLLM(
        [
            LLMResponse(
                text="szukam",
                tool_calls=(ToolCall("t1", "search_notes", {"query": "mpwik"}),),
                blocks=assistant_blocks,
                stop_reason="tool_use",
            ),
            LLMResponse(text="Znalazłem 1.", stop_reason="end_turn"),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", search)]).run_turn("co z mpwik?")

    assert result.stop_reason == "end_turn"
    kinds = [type(e).__name__ for e in result.entries]
    assert kinds == ["UserText", "AssistantTurn", "ToolResults", "AssistantTurn"]
    # Tura z tool_use niesie bloki VERBATIM i żądanie narzędzia.
    first_assistant = result.entries[1]
    assert isinstance(first_assistant, AssistantTurn)
    assert first_assistant.blocks == assistant_blocks
    assert first_assistant.tool_calls[0].name == "search_notes"
    # Wynik narzędzia sparowany po turze asystenta.
    tool_entry = result.entries[2]
    assert isinstance(tool_entry, ToolResults)
    assert '"count": 1' in tool_entry.outputs[0].content


# --- Audyt per wywołanie narzędzia (ADR 0067) ---------------------------------------------------


def _one_tool_then_text(tool: str, args: dict) -> _ScriptedLLM:
    return _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", tool, args),)),
            LLMResponse(text="ok"),
        ]
    )


def test_runtime_audit_records_each_tool_call_with_status():
    calls: list[tuple[str, dict, str]] = []

    def rec(name, arguments, status):
        calls.append((name, dict(arguments), status))

    llm = _one_tool_then_text("search_notes", {"query": "x"})
    AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 1})]).run_turn("q", audit=rec)

    assert calls == [("search_notes", {"query": "x"}, "ok")]


def test_runtime_audit_survives_a_turn_cut_on_max_tokens():
    """Wpis audytu wychodzi z tury, której NIC nie zapisujemy — a to jedyna wtedy droga.

    Ucięcie na ``max_tokens`` daje ``entries=()`` (inwariant zapisu ADR 0011). Narzędzia
    wcześniejszych rund JUŻ pobiegły i ich skutki uboczne zostają, więc każdy fakt o turze
    czytany z jej WYNIKU przepada po cichu — tak przepadała lepka skaza rozmowy (ADR 0066),
    dopóki wisiała na ``result.entries``. Ten test pilnuje samej drogi, niezależnie od tego,
    kto się na niej wiesza.
    """
    calls: list[tuple[str, str]] = []

    def rec(name, arguments, status):
        calls.append((name, status))

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search_notes", {"query": "x"}),)),
            LLMResponse(text="czę", stop_reason="max_tokens"),
        ]
    )

    result = AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 1})]).run_turn(
        "q", audit=rec
    )

    assert result.entries == ()  # nic do zapisu
    assert calls == [("search_notes", "ok")]  # a mimo to fakt o wywołaniu wyszedł


def test_runtime_audit_survives_exhausted_tool_iterations():
    """Druga ścieżka wyjścia bez zapisu — i ta, w której wywołania narzędzi są PEWNE.

    Limit rund wyczerpuje się wyłącznie przez wywołania narzędzi, więc „tura bez śladu"
    znaczyła tu „tura o największej liczbie skutków ubocznych bez śladu".
    """
    calls: list[str] = []

    def rec(name, arguments, status):
        calls.append(name)

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall(f"t{i}", "search_notes", {"query": "x"}),))
            for i in range(2)
        ]
    )

    result = AgentRuntime(
        llm, [_spec("search_notes", lambda query: {"count": 1})], max_tool_iterations=2
    ).run_turn("q", audit=rec)

    assert result.stop_reason == "max_tool_iterations"
    assert result.entries == ()
    assert calls == ["search_notes", "search_notes"]


def test_runtime_audit_marks_error_result_status():
    statuses: list[str] = []

    def rec(name, arguments, status):
        statuses.append(status)

    llm = _one_tool_then_text("search_notes", {"query": "x"})
    AgentRuntime(llm, [_spec("search_notes", lambda query: {"error": "brak"})]).run_turn(
        "q", audit=rec
    )

    assert statuses == ["error"]


def test_runtime_audit_records_before_raising_defect():
    calls: list[tuple[str, str]] = []

    def rec(name, arguments, status):
        calls.append((name, status))

    def boom(query: str) -> dict:
        raise ValueError("defekt kodu narzędzia")

    llm = _one_tool_then_text("search_notes", {"query": "x"})
    with pytest.raises(ValueError):
        AgentRuntime(llm, [_spec("search_notes", boom)]).run_turn("q", audit=rec)

    # Wpis powstaje w ``finally`` (status "error"), a defekt propaguje się dalej (kontrakt rdzenia).
    assert calls == [("search_notes", "error")]


def test_a_FAILING_recorder_does_not_replace_the_tool_result():
    """Rejestrator jest best-effort, ale to własność WOŁANIA, nie obietnica implementacji.

    Wpis powstaje w ``finally``, więc wyjątek stamtąd ZASTĘPUJE wynik narzędzia swoim własnym:
    udana operacja wracała do modelu jako awaria dziennika. Blokada SQLite na wolumenie audytu
    wystarczała, żeby zabić turę, która się powiodła.
    """
    llm = _one_tool_then_text("search_notes", {"query": "x"})

    def rec(name, arguments, status):
        raise RuntimeError("baza audytu zablokowana")

    result = AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 1})]).run_turn(
        "q", audit=rec
    )

    assert result.reply == "ok"


def test_a_FAILING_recorder_does_not_mask_the_tools_own_defect():
    """Druga strona: gdy narzędzie rzuca, w górę ma lecieć JEGO wyjątek, nie awaria dziennika.

    Inaczej prawdziwa przyczyna znikała z logu, a operator dostawał trop prowadzący donikąd.
    """

    def boom(query: str) -> dict:
        raise ValueError("defekt kodu narzędzia")

    def rec(name, arguments, status):
        raise RuntimeError("baza audytu zablokowana")

    llm = _one_tool_then_text("search_notes", {"query": "x"})

    with pytest.raises(ValueError, match="defekt kodu narzędzia"):
        AgentRuntime(llm, [_spec("search_notes", boom)]).run_turn("q", audit=rec)


def test_runtime_without_audit_dispatches_normally():
    seen: list[str] = []

    def search(query: str) -> dict:
        seen.append(query)
        return {"ok": True}

    llm = _one_tool_then_text("search_notes", {"query": "x"})
    result = AgentRuntime(llm, [_spec("search_notes", search)]).run_turn("q")  # audit=None

    assert result.reply == "ok"
    assert seen == ["x"]


def test_runtime_audit_records_rejected_unknown_tool():
    """Wywołanie narzędzia SPOZA katalogu zostawia ślad "rejected" — próba za bramką zdolności."""
    calls: list[tuple[str, dict, str]] = []

    def rec(name, arguments, status):
        calls.append((name, dict(arguments), status))

    llm = _one_tool_then_text("Bash", {"command": "ls"})  # brak w katalogu (np. powłoka OFF)
    AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 1})]).run_turn("q", audit=rec)

    assert calls == [("Bash", {"command": "ls"}, "rejected")]


def test_runtime_audit_records_rejected_bad_arguments():
    """Odrzucenie na walidacji argumentów (nadmiarowa nazwa) też jest audytowane jako "rejected"."""
    calls: list[tuple[str, dict, str]] = []

    def rec(name, arguments, status):
        calls.append((name, dict(arguments), status))

    llm = _one_tool_then_text("search_notes", {"query": "x", "nieznany": 1})
    AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 1})]).run_turn("q", audit=rec)

    # Surowe argumenty (koercja padła); rejestrator aplikacji zredaguje je w ``project_arguments``.
    assert calls == [("search_notes", {"query": "x", "nieznany": 1}, "rejected")]


def test_runtime_attaches_queued_files_to_the_tool_results_of_that_round():
    """Plik odłożony przez ``File`` (ADR 0064) jedzie z wynikami TEJ rundy, nie z następną turą.

    Dzięki temu model widzi go w tej samej turze, w której o niego poprosił — gdyby czekał na
    kolejną wiadomość człowieka, narzędzie byłoby bezużyteczne w rozmowie o jednym pliku.
    """
    queue = AttachmentQueue(budget_bytes=1000)

    def podaj() -> dict:
        queue.offer(Attachment("document", "application/pdf", "umowa.pdf", data_base64="AAAA"), 3)
        return {"materialized": True}

    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "File", {}),)),
            LLMResponse(text="Widzę umowę."),
        ]
    )

    AgentRuntime(llm, [_spec("File", podaj)]).run_turn("pokaż umowę", attachment_queue=queue)

    (results,) = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)]
    assert [a.name for a in results.attachments] == ["umowa.pdf"]
    # Wynik narzędzia niesie samo potwierdzenie — bajty nie wracają jego treścią.
    assert "AAAA" not in results.outputs[0].content


def test_runtime_without_a_queue_behaves_exactly_as_before():
    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search_notes", {"query": "x"}),)),
            LLMResponse(text="ok"),
        ]
    )

    AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 0})]).run_turn("q")

    (results,) = [e for e in llm.transcripts[1] if isinstance(e, ToolResults)]
    assert results.attachments == ()


def test_runtime_carries_the_trust_nonce_to_every_api_call():
    """Bez tego przeniesienia ŻADNA koperta nie dociera do modelu — bramka włączona czy nie.

    To jest szew, który czyni całą funkcję działającą, a jego wycięcie przechodziło przez cały
    pakiet: sondy koperty badały czyste funkcje i mapowanie wołane wprost, więc łańcuch
    drzwi → runtime → adapter nie był zamknięty żadną asercją.
    """
    llm = _ScriptedLLM(
        [
            LLMResponse(tool_calls=(ToolCall("t1", "search_notes", {"query": "x"}),)),
            LLMResponse(text="ok"),
        ]
    )

    AgentRuntime(llm, [_spec("search_notes", lambda query: {"count": 0})]).run_turn(
        "q", trust_nonce="abcd1234"
    )

    # Także w DRUGIEJ rundzie pętli narzędzi — nie tylko w pierwszym wywołaniu.
    assert llm.nonces_seen == ["abcd1234", "abcd1234"]


def test_runtime_without_a_nonce_passes_an_empty_one():
    llm = _ScriptedLLM([LLMResponse(text="ok")])

    AgentRuntime(llm, []).run_turn("q")

    assert llm.nonces_seen == [""]


# --- Sygnal budzetu iteracji w petli (ADR 0068 §6) ------------------------------------


class _ZapisujeSystem:
    """Atrapa notujaca BLOKI SYSTEMOWE kazdej rundy — tam jedzie sygnal budzetu."""

    def __init__(self, rundy: int) -> None:
        self.systems: list[tuple[str, ...]] = []
        self._rundy = rundy

    def complete(self, *, system, transcript, tools, trust_nonce=""):
        self.systems.append(tuple(system))
        self._rundy -= 1
        if self._rundy <= 0:
            return LLMResponse(text="odpowiadam tym, co mam")
        return LLMResponse(text="szukam", tool_calls=(ToolCall("t", "tool", {}),))


def _petla(max_iter: int, rundy: int) -> _ZapisujeSystem:
    llm = _ZapisujeSystem(rundy)
    AgentRuntime(llm, [_spec("tool", lambda **_: {"ok": True})], max_tool_iterations=max_iter).run(
        "x", session_header="Today is 2026-08-17, Monday."
    )
    return llm


def test_model_dostaje_ostrzezenie_zanim_budzet_sie_wyczerpie():
    """Wyczerpanie limitu kasowalo CALA ture razem z wiadomoscia uzytkownika (ADR 0011).

    Inwariant zapisu zostaje; zmienia sie to, ze model widzi zblizajaca sie granice i moze
    przejsc na „odpowiadam tym, co mam" zamiast planowac kolejne wywolania w ciemno.
    """
    llm = _petla(max_iter=4, rundy=4)

    naglowki = [s[-1] for s in llm.systems]
    assert "rounds of tool calls remain" not in naglowki[0]
    assert "rounds of tool calls remain" not in naglowki[1]
    assert "2 rounds of tool calls remain" in naglowki[2]
    assert "last round of tool calls" in naglowki[3]


def test_sygnal_budzetu_nie_rusza_bloku_statycznego():
    """Korpus niesie breakpoint cache'u — sygnal ma zostac w drugim bloku (ADR 0056)."""
    llm = _petla(max_iter=2, rundy=2)

    statyczne = {s[0] for s in llm.systems}
    assert len(statyczne) == 1, "korpus zmienil sie miedzy rundami — cache prefiksu unieważniony"


def test_naglowek_sesji_przezywa_doklejenie_sygnalu():
    """Data i rozmowa maja zostac — sygnal jest DOPISKIEM, nie podmiana naglowka."""
    llm = _petla(max_iter=1, rundy=1)

    assert "Today is 2026-08-17" in llm.systems[0][-1]
    assert "last round of tool calls" in llm.systems[0][-1]
