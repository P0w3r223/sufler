"""Ustawienia pamięci rozmów (SQLite ``conversations.db``)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sufler.config._env import _bool_from_env, _int_from_env, _path_from_env

# Domyślna baza rozmów (SQLite, ADR 0010): POZA repo i poza data/ — to dane
# operacyjne (historia czatu), nie baza wiedzy. Katalog domowy (pisemny bez
# uprawnień administratora — drzwi lokalne). Nadpisywalna przez SUFLER_CONVERSATIONS_DB.
_DEFAULT_CONVERSATIONS_DB = Path.home() / ".sufler" / "conversations.db"


@dataclass(frozen=True)
class ConversationSettings:
    """Konfiguracja pamięci rozmów (wątkowość + limit kontekstu, Faza 2 / ADR 0010, 0012).

    Baza SQLite leży poza ``data/`` (folder indeksowany przez rdzeń) i poza repo —
    to dane operacyjne, nie baza wiedzy. Limit kontekstu bramkuje REALNY rozmiar kontekstu
    ostatniej tury (z pola ``usage`` odpowiedzi, Design 2) — po osiągnięciu rollover startuje
    nowy, tańszy wątek. Domyślnie 128000 (przy 1M oknie): realne ``input_tokens`` (system +
    schematy narzędzi + cała historia wysyłana ponownie co turę) są O RZĘDY większe niż dawna
    estymata, więc próg musi być duży. Nadpisywalny przez ``SUFLER_CONV_MAX_TOKENS``.

    ``idle_timeout_minutes`` (ADR 0012) domyka wątkowość w czasie: po tylu minutach
    bezczynności kolejna wiadomość zaczyna NOWY wątek (osobne rozmowy = osobne wątki,
    zamiast jednej ciągnącej się nici). ``0`` wyłącza to kryterium. Nadpisywalny przez
    ``SUFLER_CONV_IDLE_MINUTES``.

    Kompaktowanie (ADR 0014) ZASTĘPUJE rollover-na-rozmiarze, gdy włączone: przy
    ``last_input_tokens`` > ``compaction_threshold_tokens`` stare tury zastępujemy
    podsumowaniem (osobne wywołanie modelu ``compaction_model``, domyślnie = model
    agenta), zachowując ostatnie ``compaction_keep_turns`` verbatim.

    Próg jest BEZWZGLĘDNY, nie ułamkiem okna (ADR 0058). Wcześniej liczyliśmy go jako
    70% okna modelu, co przy oknie 1M dawało 700k — próg mieszczący się w oknie, ale
    daleko poza zakresem, w którym model wiarygodnie sięga po fakty ze środka kontekstu.
    Rozmiar okna mówi, ile tokenów WOLNO wysłać; próg kompaktowania ma mówić, po ilu
    warto streścić. To dwie różne wielkości i wiązanie ich ułamkiem sprawiało, że
    podbicie okna po cichu pogarszało jakość odpowiedzi.
    """

    db_path: Path
    max_context_tokens: int = 128000
    idle_timeout_minutes: int = 30
    compaction_enabled: bool = True
    compaction_threshold_tokens: int = 150_000
    compaction_keep_turns: int = 4
    compaction_model: str = ""  # "" → użyj modelu agenta (Sonnet 5)

    @classmethod
    def from_env(cls) -> ConversationSettings:
        return cls(
            db_path=_path_from_env("SUFLER_CONVERSATIONS_DB", _DEFAULT_CONVERSATIONS_DB),
            max_context_tokens=_int_from_env("SUFLER_CONV_MAX_TOKENS", 128000),
            idle_timeout_minutes=_int_from_env("SUFLER_CONV_IDLE_MINUTES", 30),
            compaction_enabled=_bool_from_env("SUFLER_COMPACTION_ENABLED", default=True),
            compaction_threshold_tokens=_int_from_env(
                "SUFLER_COMPACTION_THRESHOLD_TOKENS", 150_000
            ),
            compaction_keep_turns=_int_from_env("SUFLER_COMPACTION_KEEP_TURNS", 4),
            compaction_model=os.environ.get("SUFLER_COMPACTION_MODEL", ""),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy limit, próg bezczynności lub kompaktowanie są bez sensu."""
        if self.max_context_tokens < 1:
            raise ValueError(
                f"SUFLER_CONV_MAX_TOKENS musi być >= 1, jest: {self.max_context_tokens}."
            )
        # 0 = kryterium bezczynności wyłączone; ujemne nie ma sensu (fail fast).
        if self.idle_timeout_minutes < 0:
            raise ValueError(
                "SUFLER_CONV_IDLE_MINUTES musi być >= 0 (0 wyłącza), jest: "
                f"{self.idle_timeout_minutes}."
            )
        if self.compaction_threshold_tokens < 1:
            raise ValueError(
                "SUFLER_COMPACTION_THRESHOLD_TOKENS musi być >= 1, jest: "
                f"{self.compaction_threshold_tokens}."
            )
        if self.compaction_keep_turns < 1:
            raise ValueError(
                f"SUFLER_COMPACTION_KEEP_TURNS musi być >= 1, jest: {self.compaction_keep_turns}."
            )

    def idle_timeout(self) -> timedelta | None:
        """Próg bezczynności jako ``timedelta`` do wstrzyknięcia w ``ConversationService``.

        ``0`` (wyłączone) mapujemy na ``None`` — jedno miejsce konwersji dla wszystkich
        drzwi, żeby wiring nie powtarzał warunku ``> 0``.
        """
        return timedelta(minutes=self.idle_timeout_minutes) if self.idle_timeout_minutes else None
