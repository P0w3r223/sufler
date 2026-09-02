"""Ustawienia runtime'u agenta: model, budżety tur, streszczanie, sędzia."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from workmate.config._env import _bool_from_env, _int_from_env


@dataclass(frozen=True)
class AgentSettings:
    """Konfiguracja runtime'u agenta (Faza 2, M1 / ADR 0008; ADR 0011, 0058).

    Klucz Claude API to sekret — czytany z env, nigdy z repo ani z folderu
    indeksowanego przez rdzeń (``data/``). Domyślny model to ``claude-sonnet-5``
    (większe wymagania projektu wobec syntezy), nadpisywalny przez ``WORKMATE_AGENT_MODEL``.
    ``thinking_type`` steruje rozszerzonym myśleniem (ADR 0011): ``adaptive`` (domyślnie,
    model sam decyduje ile myśleć) albo ``disabled`` (np. dla ograniczenia kosztu).
    ``max_tokens`` (128000 — pełny sufit wyjścia modelu) dzieli budżet między myślenie
    i odpowiedź; adapter woła Claude API STREAMINGIEM (``messages.stream``), więc duży
    sufit nie odpala limitu czasu SDK, który odrzuca duże żądania non-streaming.

    Pola ``context_editing_*`` (ADR 0058) sterują czyszczeniem starych wyników narzędzi
    przez API. Progi tworzą kaskadę z kompaktowaniem (``ConversationSettings``): najpierw
    tanie czyszczenie wyników (100k), dopiero potem streszczanie rozmowy osobnym
    wywołaniem modelu (150k). Odwrotna kolejność płaciłaby za streszczanie bajtów,
    które i tak miały wypaść.
    """

    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback go nie ujawniło.
    api_key: str = field(default="", repr=False)
    model: str = "claude-sonnet-5"
    max_tokens: int = 128000
    max_tool_iterations: int = 8
    thinking_type: str = "adaptive"
    # Druga przelotka-krytyk notatki M3 (ADR 0047): domyślnie OFF (zachowuje jednoprzelotowe 0041),
    # ~2× koszt gdy ON. To toggle jakości, NIE bramka zapisu — flip nie wymaga zgody zespołu.
    verify_meeting_note: bool = False
    # Czyszczenie starych wyników narzędzi po stronie API (ADR 0058). Wynik narzędzia wraca
    # do kontekstu i jest odsyłany w KAŻDEJ kolejnej turze, więc bez czyszczenia jedna
    # rozmowa z intensywnym użyciem narzędzi rośnie liniowo w bajtach, których model już
    # nie potrzebuje. Ślad wywołania zostaje — znika tylko treść wyniku, więc model wie,
    # że pytał, i nie powtarza pytania.
    context_editing_enabled: bool = True
    context_editing_trigger_tokens: int = 100_000
    # MUSI być >= ``max_tool_iterations`` (bramka w ``validate``). Próg czyszczenia mierzy
    # rozmiar promptu, a ten rośnie TAKŻE w środku tury — pętla dokłada wynik za wynikiem.
    # Gdyby chronionych par było mniej niż iteracji, czyszczenie sięgnęłoby wyników, o które
    # model poprosił przed chwilą w TEJ SAMEJ turze: dostałby pustkę zamiast danych do
    # syntezy, powtórzył wywołania i wyczerpał limit iteracji (tura bez zapisu, ADR 0011).
    context_editing_keep_tool_uses: int = 8
    # Czyszczenie UNIEWAŻNIA cache prefiksu — i to nie fragmentu, tylko wszystkiego od
    # miejsca cięcia w dół, bo usuwane wyniki leżą na POCZĄTKU historii. Jedno czyszczenie
    # kosztuje więc zapis cache'u całej rozmowy, a oszczędza tyle, ile zdjęło — próg
    # minimalnej porcji zamienia częste drobne cięcia w rzadkie, które się zwracają.
    context_editing_clear_at_least_tokens: int = 40_000

    @classmethod
    def from_env(cls) -> AgentSettings:
        # Priorytet: WORKMATE_AGENT_API_KEY (jawnie dla WorkMate) > ANTHROPIC_API_KEY (nazwa SDK).
        api_key = os.environ.get("WORKMATE_AGENT_API_KEY") or os.environ.get(
            "ANTHROPIC_API_KEY", ""
        )
        return cls(
            api_key=api_key,
            model=os.environ.get("WORKMATE_AGENT_MODEL", "claude-sonnet-5"),
            max_tokens=_int_from_env("WORKMATE_AGENT_MAX_TOKENS", 128000),
            max_tool_iterations=_int_from_env("WORKMATE_AGENT_MAX_TOOL_ITERATIONS", 8),
            thinking_type=os.environ.get("WORKMATE_AGENT_THINKING", "adaptive"),
            verify_meeting_note=_bool_from_env("WORKMATE_AGENT_VERIFY_MEETING_NOTE", False),
            context_editing_enabled=_bool_from_env(
                "WORKMATE_CONTEXT_EDITING_ENABLED", default=True
            ),
            context_editing_trigger_tokens=_int_from_env(
                "WORKMATE_CONTEXT_EDITING_TRIGGER_TOKENS", 100_000
            ),
            context_editing_keep_tool_uses=_int_from_env(
                "WORKMATE_CONTEXT_EDITING_KEEP_TOOL_USES", 8
            ),
            context_editing_clear_at_least_tokens=_int_from_env(
                "WORKMATE_CONTEXT_EDITING_CLEAR_AT_LEAST_TOKENS", 40_000
            ),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy konfiguracja jest niepełna albo bez sensu.

        Lepiej nie ruszać bez uwierzytelniania; a niedodatnie limity dają cichy
        no-op (pętla pomija model), więc też je odrzucamy fail-fast.
        """
        if not self.api_key:
            raise ValueError(
                "Runtime agenta wymaga klucza Claude API: ustaw ANTHROPIC_API_KEY "
                "(lub WORKMATE_AGENT_API_KEY) w środowisku/.env."
            )
        if self.max_tool_iterations < 1:
            raise ValueError(
                "WORKMATE_AGENT_MAX_TOOL_ITERATIONS musi być >= 1, jest: "
                f"{self.max_tool_iterations}."
            )
        if self.max_tokens < 1:
            raise ValueError(f"WORKMATE_AGENT_MAX_TOKENS musi być >= 1, jest: {self.max_tokens}.")
        if self.thinking_type not in ("adaptive", "disabled"):
            raise ValueError(
                "WORKMATE_AGENT_THINKING musi być 'adaptive' albo 'disabled', jest: "
                f"{self.thinking_type!r}."
            )
        if self.context_editing_trigger_tokens < 1:
            raise ValueError(
                "WORKMATE_CONTEXT_EDITING_TRIGGER_TOKENS musi być >= 1, jest: "
                f"{self.context_editing_trigger_tokens}."
            )
        # Czyszczenie potrafi odpalić W ŚRODKU tury (próg mierzy rozmiar promptu, a ten
        # rośnie z każdą iteracją pętli), więc liczba chronionych par musi pokryć całą
        # pętlę — inaczej model traci wyniki, o które sam przed chwilą poprosił. Bramka
        # jest twarda, bo cicha utrata danych z bieżącej tury objawia się dopiero jako
        # „agent w kółko woła to samo", czyli daleko od przyczyny.
        if self.context_editing_keep_tool_uses < self.max_tool_iterations:
            raise ValueError(
                "WORKMATE_CONTEXT_EDITING_KEEP_TOOL_USES musi być >= "
                f"WORKMATE_AGENT_MAX_TOOL_ITERATIONS ({self.max_tool_iterations}), "
                f"jest: {self.context_editing_keep_tool_uses}. Mniejsza wartość pozwala "
                "wyczyścić wyniki narzędzi z bieżącej tury."
            )
        if self.context_editing_clear_at_least_tokens < 1:
            raise ValueError(
                "WORKMATE_CONTEXT_EDITING_CLEAR_AT_LEAST_TOKENS musi być >= 1, jest: "
                f"{self.context_editing_clear_at_least_tokens}."
            )
