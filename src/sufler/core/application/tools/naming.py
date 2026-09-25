"""Konwencja nazw powierzchni agenta (ADR 0068) — PascalCase i przepisanie odwołań w opisie."""

from __future__ import annotations

import re
from dataclasses import replace

from sufler.core.application.tools.spec import ToolSpec

# Jedna konwencja nazw na powierzchni AGENTA (ADR 0068 §3). Trójka odczytu jest współdzielona
# z drzwiami MCP, gdzie nazwy są ZAMROŻONE golden-testem, więc rozjazd konwencji rozstrzygamy
# przemianowaniem po stronie agenta — a nie w builderze, który obsługuje oba wejścia.
_NAZWY_AGENTA = {
    "search_notes": "SearchNotes",
    "get_note": "GetNote",
    "list_projects": "ListProjects",
}

# Te same nazwy WEWNĄTRZ prozy opisu. ``get_note`` odsyła po identyfikator „z wyników
# search_notes" — czyli do narzędzia, którego na powierzchni agenta nie ma pod tą nazwą.
_ODWOLANIA_AGENTA = re.compile("|".join(sorted(_NAZWY_AGENTA, key=len, reverse=True)))


def przemianuj_na_konwencje_agenta(spec: ToolSpec) -> ToolSpec:
    """Nazwa ORAZ odwołania w opisie w konwencji agenta (ADR 0068 §3, amendment 2026-08-17).

    Dwie zasady tego ADR zderzają się dokładnie tutaj: „jedno źródło opisu" (trójka odczytu jest
    współdzielona z zamrożoną powierzchnią MCP) kontra „opis nie odsyła do narzędzia, którego
    w tej konfiguracji nie ma". Pierwsza wygrywała po cichu, bo bramka odwołań znała wyłącznie
    NOWE nazwy i starego `search_notes` w opisie ``GetNote`` po prostu nie widziała.

    Rozstrzygnięcie: przemianowanie obejmuje też TREŚĆ odwołania — ten sam ruch, którym ``File``
    wybiera ``_FILE_ZRODLO_ID_NARZEDZIA``. Jedno źródło zostaje zachowane: tekst pochodzi
    z jednego docstringa, a mapa nazw jest jedna i jawna. Alternatywą był jawny wyjątek
    w teście zgodności — odrzucony, bo zostawia model z odesłaniem do nieistniejącej nazwy,
    czyli z tym samym defektem, który ADR zamyka w trzech innych miejscach.
    """
    # ``.get`` z nazwą MCP jako zapasem, nie indeksowanie: mapa jest DRUGIM źródłem obok
    # ``build_notes_read_catalog``, a ten builder jest współdzielony z zamrożoną powierzchnią MCP.
    # Czwarte narzędzie odczytu dodane po tamtej stronie wywracałoby tutaj składanie CAŁYCH drzwi
    # agenta na ``KeyError`` — z powodu kosmetycznego (brak wpisu w konwencji nazw). Degradacja
    # do nazwy MCP zostawia model z narzędziem o nazwie spoza konwencji; to widać i da się
    # poprawić, w przeciwieństwie do drzwi, które się nie podniosły.
    # Podstawienie w OPISIE indeksuje bezpiecznie — regex powstaje z kluczy tej samej mapy.
    return replace(
        spec,
        name=_NAZWY_AGENTA.get(spec.name, spec.name),
        description=_ODWOLANIA_AGENTA.sub(lambda m: _NAZWY_AGENTA[m.group()], spec.description),
    )
