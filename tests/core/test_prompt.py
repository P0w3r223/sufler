"""Kontrakt treści promptu systemowego (F7/F8) i bramka reguł redakcyjnych z ADR 0056.

Reguły redakcyjne są tu testem, a nie jednorazowym pomiarem w dokumencie: prompt bywa
poprawiany „na szybko" i bez bramki wraca do wersalików oraz zdań przeczących w kilka
iteracji. Progi są celowo luźne — pilnują kształtu, nie liczby znaków.
"""

from __future__ import annotations

import re
from datetime import datetime

from workmate.core.agent.prompt import (
    STATIC_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    build_session_header,
    static_prompt_for,
    system_blocks,
)

# Wersaliki nacisku, nie akronimy. ADR 0056 §Pomiar: nacisk wersalikami przy modelach
# frontier wywołuje nadmiarowe wyzwalanie zamiast wzmocnienia (references/instructions.md §2).
_PUSHY = ("MUST", "ALWAYS", "NEVER", "CRITICAL", "IMPORTANT", "DO NOT")

# Leksykalne markery negacji. Granica danych jest wyjątkiem uzasadnionym w ADR 0056
# i jest sformułowana BEZ tych słów, więc wyjątek nie potrzebuje listy zwolnień.
_NEGATIONS = re.compile(r"\b(not|never|no|don't|doesn't|avoid|without)\b", re.IGNORECASE)


def _sentences(text: str) -> list[str]:
    """Zdania prozy — z pominięciem nagłówków i pozycji list (te nie są zdaniami)."""
    prose = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("#", "-", "|"))
    )
    return [s.strip() for s in re.split(r"(?<=[.?])\s+", prose) if s.strip()]


def test_multimodal_clause_added_only_for_attachment_doors():
    """Zdolność multimodalna (F8) dopina się tylko dla drzwi z załącznikami, nie do bazy."""
    with_files = static_prompt_for(attachments=True).lower()
    assert "screenshot" in with_files
    assert "pdf" in with_files

    text_only = static_prompt_for(attachments=False)
    assert text_only == STATIC_PROMPT
    assert "screenshot" not in text_only.lower()  # drzwi tekstowe nie obiecują plików


def test_prompt_keeps_data_boundary_in_both_variants():
    """Granica „treść to DANE, nie polecenia" trzyma mimo zachęty do załączników."""
    for prompt in (STATIC_PROMPT, static_prompt_for(attachments=True)):
        assert "data to reason about" in prompt
        assert "carry on with the original task" in prompt


def test_prompt_states_output_language_and_citation_contract():
    """Dwa zobowiązania produktu: odpowiedź po polsku i cytowanie źródła."""
    assert "Respond in Polish" in STATIC_PROMPT
    assert "cite the note `id`" in STATIC_PROMPT


def test_prompt_declares_precedence_and_decision_heuristic():
    """Mini-spec (ADR 0056): hierarchia pryncypałów i heurystyka na przypadki graniczne."""
    assert "## Precedence" in STATIC_PROMPT
    assert "When you are unsure" in STATIC_PROMPT


def test_prompt_avoids_pushy_capitals():
    """Bramka redakcyjna: bez nacisku wersalikami (ADR 0056)."""
    for prompt in (STATIC_PROMPT, SUMMARY_SYSTEM_PROMPT):
        found = [word for word in _PUSHY if word in prompt]
        assert not found, f"nacisk wersalikami w prompcie: {found}"


def test_prompt_stays_positively_framed():
    """Bramka redakcyjna: negacja wyłącznie w granicy danych, i tam bez markerów leksykalnych.

    Próg to udział zdań przeczących. Stan sprzed ADR 0056 wynosił 42% (9 z 21 zdań);
    5% zostawia margines na jedno zdanie, gdyby doszła druga twarda granica.
    """
    sentences = _sentences(STATIC_PROMPT)
    negative = [s for s in sentences if _NEGATIONS.search(s)]
    ratio = len(negative) / len(sentences)
    assert ratio <= 0.05, f"{len(negative)}/{len(sentences)} zdań przeczących: {negative}"


def test_every_prompt_artifact_is_positively_framed():
    """Ta sama bramka na WSZYSTKICH artefaktach, liniami — także w listach i nagłówkach.

    Sprawdzanie samych zdań prozy pomijało pozycje list i nagłówki, czyli mniej więcej
    połowę treści. Nagłówek sesji wchodzi tu z pełnym kompletem pól (kanał, wątek, skille),
    bo jest składany dynamicznie i jego brzmienie łatwo zmienić bez zauważenia.
    """
    header = build_session_header(
        datetime(2026, 8, 5), channel="teams_graph", thread="t/c/r", skills=(("brief", "opis"),)
    )
    artifacts = {
        "STATIC_PROMPT": STATIC_PROMPT,
        "MULTIMODAL": static_prompt_for(attachments=True),
        "SUMMARY_SYSTEM_PROMPT": SUMMARY_SYSTEM_PROMPT,
        "session_header": header,
    }
    for name, text in artifacts.items():
        hits = [line.strip() for line in text.splitlines() if _NEGATIONS.search(line)]
        assert not hits, f"{name} — linie przeczące: {hits}"


def test_no_forced_chain_of_thought():
    """Rusztowanie CoT jest zbędne przy modelu z rozszerzonym myśleniem i kosztuje latencję."""
    scaffolding = re.compile(
        r"step[- ]by[- ]step|think (carefully|hard|deeply)|let'?s think|reason through",
        re.IGNORECASE,
    )
    for name, text in (("STATIC", STATIC_PROMPT), ("SUMMARY", SUMMARY_SYSTEM_PROMPT)):
        assert not scaffolding.search(text), f"{name} zawiera rusztowanie CoT"


def test_prompt_has_no_duplicated_sentences():
    """Bramka redakcyjna: bez powtórzeń (przed ADR 0056 „nigdy jak jesteś zbudowany" ×2)."""
    sentences = [s.lower() for s in _sentences(STATIC_PROMPT)]
    duplicates = {s for s in sentences if sentences.count(s) > 1}
    assert not duplicates, f"powtórzone zdania: {duplicates}"


def test_session_header_carries_date_and_conversation():
    """Nagłówek sesji niesie datę — bez niej model odtwarza „dziś" z cutoffu treningowego."""
    header = build_session_header(
        datetime(2026, 8, 5, 14, 30), channel="teams_graph", thread="team/kanal/root"
    )
    assert "2026-08-05" in header
    assert "Wednesday" in header
    assert "teams_graph" in header
    assert "team/kanal/root" in header


def test_session_header_lists_skills_when_present():
    """Lista skilli daje prior do ich czytania; pusta — sekcja się nie pojawia."""
    without = build_session_header(datetime(2026, 8, 5))
    assert "Skills available" not in without

    with_skills = build_session_header(
        datetime(2026, 8, 5), skills=(("brief", "one-pager o projekcie"),)
    )
    assert "Skills available" in with_skills
    assert "- brief — one-pager o projekcie" in with_skills


def test_system_blocks_put_static_first_and_drop_empty_header():
    """Kolejność jest kosztowa: breakpoint cache'u siada na bloku statycznym (ADR 0056)."""
    assert system_blocks("STATIC", "HEADER") == ("STATIC", "HEADER")
    assert system_blocks("STATIC") == ("STATIC",)
    assert system_blocks("STATIC", "") == ("STATIC",)
