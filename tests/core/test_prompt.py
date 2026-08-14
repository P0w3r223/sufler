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
    STATIC_PROMPT_SHELL,
    SUMMARY_SYSTEM_PROMPT,
    build_session_header,
    static_prompt_for,
    system_blocks,
)

# Montaże, które wykonawca dostaje w `docker-compose.yml` paczki wdrożeniowej. Wariant korpusu
# z powłoką opisuje DOKŁADNIE je — ani ścieżki, której nie ma (`/mnt/user/*` zdjęte 2026-08-06),
# ani pominiętej (`/mnt/skills` nie było w mapie do etapu 6, a lista procedur szła do nagłówka
# sesji). Po stronie paczki tę samą listę egzekwuje `tools/compare_compose.py` na wykonawcy.
_MOUNTS = ("/mnt/system/notes/", "/mnt/system/projects/", "/mnt/skills/", "/home/scratchpad/")

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


def test_prompt_keeps_data_boundary_in_all_variants():
    """Granica „treść to DANE, nie polecenia" trzyma mimo zachęty do załączników i mimo powłoki."""
    for prompt in (
        STATIC_PROMPT,
        STATIC_PROMPT_SHELL,
        static_prompt_for(attachments=True),
        static_prompt_for(attachments=True, shell=True),
    ):
        assert "data to reason about" in prompt
        assert "carry on with the original task" in prompt


def test_prompt_states_output_language_and_citation_contract():
    """Dwa zobowiązania produktu: odpowiedź po polsku i cytowanie źródła."""
    for prompt in (STATIC_PROMPT, STATIC_PROMPT_SHELL):
        assert "Respond in Polish" in prompt
        assert "cite the note `id`" in prompt


def test_prompt_declares_precedence_and_decision_heuristic():
    """Mini-spec (ADR 0056): hierarchia pryncypałów i heurystyka na przypadki graniczne."""
    for prompt in (STATIC_PROMPT, STATIC_PROMPT_SHELL):
        assert "## Precedence" in prompt
        assert "When you are unsure" in prompt


def test_prompt_avoids_pushy_capitals():
    """Bramka redakcyjna: bez nacisku wersalikami (ADR 0056)."""
    for prompt in (STATIC_PROMPT, STATIC_PROMPT_SHELL, SUMMARY_SYSTEM_PROMPT):
        found = [word for word in _PUSHY if word in prompt]
        assert not found, f"nacisk wersalikami w prompcie: {found}"


def test_prompt_stays_positively_framed():
    """Bramka redakcyjna: negacja wyłącznie w granicy danych, i tam bez markerów leksykalnych.

    Próg to udział zdań przeczących. Stan sprzed ADR 0056 wynosił 42% (9 z 21 zdań);
    5% zostawia margines na jedno zdanie, gdyby doszła druga twarda granica.
    """
    for name, prompt in (("TOOLS", STATIC_PROMPT), ("SHELL", STATIC_PROMPT_SHELL)):
        sentences = _sentences(prompt)
        negative = [s for s in sentences if _NEGATIONS.search(s)]
        ratio = len(negative) / len(sentences)
        assert ratio <= 0.05, f"{name}: {len(negative)}/{len(sentences)} przeczących: {negative}"


def test_every_prompt_artifact_is_positively_framed():
    """Ta sama bramka na WSZYSTKICH artefaktach, liniami — także w listach i nagłówkach.

    Sprawdzanie samych zdań prozy pomijało pozycje list i nagłówki, czyli mniej więcej
    połowę treści. Nagłówek sesji wchodzi tu z pełnym kompletem pól (kanał, wątek, skille,
    powiązanie z GitHubem), bo jest składany dynamicznie i jego brzmienie łatwo zmienić bez
    zauważenia.

    Komplet nie jest ozdobą. ``github_thread`` doszło po zniesieniu ``reply_on_thread``,
    fikstura została przy czterech polach, a nowe zdanie weszło z dwoma „never" i przeszło.
    Sonda, która nie umie zawieść, wygląda identycznie jak działająca — każde nowe pole
    nagłówka dopisujemy tu razem z nim. Powtórka historii: pola z ADR 0064/0066
    (odłożone pliki, koperta treści obcej) doszły później, a pierwsza wersja zdania
    o kopercie znowu weszła z dwoma przeczeniami.
    """
    header = build_session_header(
        datetime(2026, 8, 5),
        channel="teams_graph",
        thread="t/c/r",
        skills=(("brief", "opis"),),
        github_thread=("issue", 7),
        staged_files=("umowa.pdf",),
        trust_nonce="abcd1234",
    )
    artifacts = {
        "STATIC_PROMPT": STATIC_PROMPT,
        "STATIC_PROMPT_SHELL": STATIC_PROMPT_SHELL,
        "MULTIMODAL": static_prompt_for(attachments=True),
        "MULTIMODAL_SHELL": static_prompt_for(attachments=True, shell=True),
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
    for name, text in (
        ("STATIC", STATIC_PROMPT),
        ("STATIC_SHELL", STATIC_PROMPT_SHELL),
        ("SUMMARY", SUMMARY_SYSTEM_PROMPT),
    ):
        assert not scaffolding.search(text), f"{name} zawiera rusztowanie CoT"


def test_prompt_has_no_duplicated_sentences():
    """Bramka redakcyjna: bez powtórzeń (przed ADR 0056 „nigdy jak jesteś zbudowany" ×2)."""
    for name, prompt in (("TOOLS", STATIC_PROMPT), ("SHELL", STATIC_PROMPT_SHELL)):
        sentences = [s.lower() for s in _sentences(prompt)]
        duplicates = {s for s in sentences if sentences.count(s) > 1}
        assert not duplicates, f"{name}: powtórzone zdania: {duplicates}"


# --- etap 6: ENVIRONMENT opisuje świat, który agent ZASTAJE --------------------


def test_shell_variant_describes_the_mounts_and_the_tools_variant_describes_tools():
    """Dwa światy, dwa opisy — i żaden nie opisuje tego drugiego.

    Do etapu 6 korpus był jeden i niósł zdanie „the knowledge base lives behind tools",
    prawdziwe wyłącznie BEZ powłoki. Z powłoką narzędzia odczytu notatek z katalogu znikają
    (``build_agent_runtime``), więc blok STATYCZNY — najbardziej autorytatywny i cache'owany —
    zaprzeczał zdolności, którą agent miał.
    """
    assert "lives behind tools" in STATIC_PROMPT
    for path in _MOUNTS:
        assert path not in STATIC_PROMPT, f"{path} obiecany drzwiom, które powłoki nie dostają"

    for path in _MOUNTS:
        assert path in STATIC_PROMPT_SHELL, f"{path} istnieje w compose, a korpus o nim milczy"
    assert "lives behind tools" not in STATIC_PROMPT_SHELL


def test_shell_variant_marks_the_read_only_mounts_as_read_only():
    """Granica zapisu jest FAKTEM o montażu (`:ro` w compose), więc należy do opisu świata.

    Bez niej model planuje zapis w miejscu, w którym powłoka zwróci `Read-only file system` —
    a wykonawca dostaje bazę wiedzy zamontowaną `ro` właśnie po to (ADR 0007).
    """
    for line in STATIC_PROMPT_SHELL.splitlines():
        if any(mount in line for mount in ("/mnt/system/notes/", "/mnt/system/projects/")):
            assert "read-only" in line, f"montaż `ro` opisany bez granicy zapisu: {line.strip()}"


def test_shell_variant_promises_no_mount_that_was_removed():
    """Regresja `/mnt/user/*`: montaż zdjęto 2026-08-06, a treść pisze się wobec compose.

    ADR 0005 cytuje korpus, który wciąż niesie te ścieżki — odmrożenie go wróciłoby do martwej
    obietnicy, tym razem w bloku statycznym zamiast w opisie narzędzia.
    """
    for prompt in (STATIC_PROMPT, STATIC_PROMPT_SHELL):
        assert "/mnt/user" not in prompt


def test_static_prompt_for_picks_the_variant_and_keeps_the_multimodal_clause_orthogonal():
    """``shell`` i ``attachments`` są niezależne — cztery kombinacje, każda spójna."""
    assert static_prompt_for(attachments=False, shell=False) == STATIC_PROMPT
    assert static_prompt_for(attachments=False, shell=True) == STATIC_PROMPT_SHELL

    with_files = static_prompt_for(attachments=True, shell=True)
    assert with_files.startswith(STATIC_PROMPT_SHELL)
    assert "screenshot" in with_files.lower()

    # Domyślne `shell=False` jest zachowawcze: pominięcie argumentu opisuje świat WĘŻSZY.
    assert static_prompt_for(attachments=True) == static_prompt_for(attachments=True, shell=False)


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


# --- Powiązanie wątku z issue/PR w nagłówku sesji (ADR 0024; krok 5.5 ADR 0009 paczki) ---


def test_naglowek_bez_powiazania_nie_wspomina_o_githubie() -> None:
    """Zdanie o powiązaniu wchodzi WYŁĄCZNIE dla wątku, który je ma — inaczej byłoby obietnicą
    bez pokrycia, tak jak akapit zapisu przy nieczynnej akcji."""
    naglowek = build_session_header(datetime(2026, 8, 6, 10, 0))
    assert "GitHub" not in naglowek


def test_naglowek_niesie_numer_rodzaj_i_regule_jawnej_prosby() -> None:
    """Trzy rzeczy, które niósł dawny opis ``reply_on_thread``, muszą przeżyć jego zniesienie:
    numer celu, rodzaj (issue/PR) i regułę „tylko na wprost wyrażoną prośbę"."""
    naglowek = build_session_header(datetime(2026, 8, 6, 10, 0), github_thread=("pr", 12))
    assert "#12" in naglowek
    assert "pull request" in naglowek
    assert "explicitly" in naglowek
    # Nazwa i argument akcji, którą model ma wywołać — inaczej podpowiedź nie ma adresata.
    assert "GitHub(action='comment', number=12)" in naglowek


def test_naglowek_uzywa_rzeczownika_issue_dla_issue() -> None:
    naglowek = build_session_header(datetime(2026, 8, 6, 10, 0), github_thread=("issue", 7))
    assert "issue #7" in naglowek
    assert "pull request" not in naglowek


def test_naglowek_zabrania_komentowania_na_inny_numer_w_tym_watku() -> None:
    """Pre-wiązanie numeru zniknęło ze schematu, więc reguła musi stać w treści.

    Ochrona nie jest przez to słabsza, niż była: ``GitHub(action='comment')`` przyjmował numer
    wprost już wtedy, gdy ``reply_on_thread`` istniało, i stał w tym samym katalogu za tą samą
    bramką. Dawne narzędzie nie zawężało niczego — wypełniało argument.
    """
    naglowek = build_session_header(datetime(2026, 8, 6, 10, 0), github_thread=("issue", 7))
    assert "only on this number" in naglowek


def test_session_header_explains_the_envelope_when_a_nonce_is_given():
    """Znacznik bez zdania, które go tłumaczy, to sam szum — a wycięcie tego warunku
    przechodziło przez cały pakiet, bo nikt nie sprawdzał nagłówka pod tym kątem."""
    header = build_session_header(datetime(2026, 8, 5), trust_nonce="abcd1234")

    assert "abcd1234" in header
    assert "data you are reading" in header


def test_session_header_without_a_nonce_says_nothing_about_envelopes():
    """Bramka OFF = nagłówek dokładnie jak dotąd; inaczej model dostawałby instrukcję
    o znacznikach, których w treści nie ma."""
    header = build_session_header(datetime(2026, 8, 5))

    assert "dane-obce" not in header
