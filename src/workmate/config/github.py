"""Ustawienia mostu GitHub: poller, bramka zapisu i estymacja kart czasu z commitów."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from workmate.config._env import (
    _bool_from_env,
    _float_from_env,
    _int_from_env,
    _list_from_env,
    _path_from_env,
)

# Domyślny stan pollera GitHub (watermark ``since``): POZA repo i data/ — dane operacyjne.
_DEFAULT_GITHUB_STATE = Path.home() / ".workmate" / "github_state.json"
# Dolny sufit interwału pollingu GitHub (świadomość limitu 5000 żądań/h uwierzytelnionych).
_GITHUB_POLL_FLOOR_S = 30
_MAX_GITHUB_PER_PAGE = 100
# Dozwolone rodzaje zdarzeń nasłuchiwanych w repo (ADR 0024: PR/CI/recenzje wchodzą opcjonalnie).
_ALLOWED_GITHUB_WATCH_KINDS = (
    "issues",
    "comments",
    "pulls",
    "reviews",
    "ci",
    "pull_state",
    "branches",
)
# Domyślny zestaw (wsteczna zgodność): tylko issue i komentarze; nowe rodzaje włącza się jawnie
# przez ``WORKMATE_GITHUB_WATCH_KINDS`` — patrz ADR 0024.
_DEFAULT_GITHUB_WATCH_KINDS = ("issues", "comments")

# Strojenie estymacji czasu z commitów (ADR 0034, część odczytowa). Pokrętła mieszkały w
# ``JiraSettings``, dopóki zdolność miała ścieżkę zapisu do Jiry; po jej wycięciu dotyczą
# WYŁĄCZNIE czytania commitów, więc stoją przy źródle danych. Twarde backstopy chronią przed
# absurdem wpisanym do ``.env``; egzekwuje je ``validate_worklog_limits`` — wołane przez poller
# i przez wpięcie drzwi Teams, czyli wszędzie tam, gdzie te wartości są w ogóle czytane.
MAX_WORKLOG_RANGE_DAYS = 92
_MAX_WORKLOG_SESSION_HOURS = 24.0
_MAX_WORKLOG_IDLE_GAP_MIN = 720
_MAX_WORKLOG_RAMP_UP_MIN = 240
# Ewidencja czasu jest kwantowana — dopuszczamy tylko dzielniki godziny mające sens w praktyce.
_ALLOWED_ROUND_MINUTES = (1, 5, 10, 15, 30, 60)


@dataclass(frozen=True)
class GithubSettings:
    """Konfiguracja drzwi GitHub w trybie DELEGOWANYM (ADR 0020) — polling repo przez PAT.

    Bot odpytuje GitHub REST tokenem osobistym (PAT), bez webhooka i publicznego endpointu.
    ``token`` to SEKRET (``repr=False``, env ``WORKMATE_GITHUB_TOKEN``) — nigdy w repo/``data/``.
    Zapis do GitHub jest OSOBNO bramkowany (``enable_github_write``, Gate 4 / ADR 0021),
    domyślnie wyłączony — drzwi startują read-only (ingest zdarzeń), zgodnie z ADR 0006.
    ``enable_ci_auto_comment`` (ADR 0024, domyślnie OFF) włącza JEDYNY autonomiczny zapis mostu —
    deterministyczny komentarz przy porażce CI na PR; wymaga też ``enable_github_write``.

    Pola ``worklog_*`` stroją ESTYMACJĘ czasu z commitów (ADR 0034) — czysty odczyt, bez bramki
    (odczyt jest domyślny, ADR 0006). ``worklog_tz`` to nazwa strefy IANA, nie offset: doba
    kalendarzowa dzieli sesje pracy, a stały offset mylił się o godzinę przez pół roku.
    """

    token: str = field(default="", repr=False)
    owner: str = ""
    repo: str = ""
    api_base: str = "https://api.github.com"
    poll_interval_s: int = 60
    per_page: int = 50
    watch_kinds: tuple[str, ...] = _DEFAULT_GITHUB_WATCH_KINDS
    enable_github_write: bool = False
    enable_ci_auto_comment: bool = False
    state_path: Path = _DEFAULT_GITHUB_STATE
    worklog_idle_gap_minutes: int = 90
    worklog_ramp_up_minutes: int = 30
    worklog_round_minutes: int = 15
    worklog_max_session_hours: float = 8.0
    worklog_max_range_days: int = 31
    worklog_tz: str = "Europe/Warsaw"

    @classmethod
    def from_env(cls) -> GithubSettings:
        return cls(
            token=os.environ.get("WORKMATE_GITHUB_TOKEN", ""),
            owner=os.environ.get("WORKMATE_GITHUB_OWNER", ""),
            repo=os.environ.get("WORKMATE_GITHUB_REPO", ""),
            api_base=os.environ.get("WORKMATE_GITHUB_API_BASE", "https://api.github.com"),
            poll_interval_s=_int_from_env("WORKMATE_GITHUB_POLL_INTERVAL", 60),
            per_page=_int_from_env("WORKMATE_GITHUB_PER_PAGE", 50),
            watch_kinds=_list_from_env("WORKMATE_GITHUB_WATCH_KINDS", _DEFAULT_GITHUB_WATCH_KINDS),
            enable_github_write=_bool_from_env("WORKMATE_GITHUB_ENABLE_WRITE", default=False),
            enable_ci_auto_comment=_bool_from_env(
                "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT", default=False
            ),
            state_path=_path_from_env("WORKMATE_GITHUB_STATE", _DEFAULT_GITHUB_STATE),
            worklog_idle_gap_minutes=_int_from_env("WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES", 90),
            worklog_ramp_up_minutes=_int_from_env("WORKMATE_GITHUB_WORKLOG_RAMP_UP_MINUTES", 30),
            worklog_round_minutes=_int_from_env("WORKMATE_GITHUB_WORKLOG_ROUND_MINUTES", 15),
            worklog_max_session_hours=_float_from_env(
                "WORKMATE_GITHUB_WORKLOG_MAX_SESSION_HOURS", 8.0
            ),
            worklog_max_range_days=_int_from_env("WORKMATE_GITHUB_WORKLOG_MAX_RANGE_DAYS", 31),
            worklog_tz=os.environ.get("WORKMATE_GITHUB_WORKLOG_TZ", "Europe/Warsaw").strip(),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak tożsamości repo/tokenu albo bezsensowne limity."""
        self.validate_worklog_limits()
        missing = [
            name
            for name, value in (
                ("WORKMATE_GITHUB_TOKEN", self.token),
                ("WORKMATE_GITHUB_OWNER", self.owner),
                ("WORKMATE_GITHUB_REPO", self.repo),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Drzwi GitHub wymagają tokenu i repo: brakuje "
                + ", ".join(missing)
                + " w środowisku/.env."
            )
        if self.poll_interval_s < _GITHUB_POLL_FLOOR_S:
            raise ValueError(
                f"WORKMATE_GITHUB_POLL_INTERVAL musi być >= {_GITHUB_POLL_FLOOR_S} "
                f"(limit API GitHub), jest: {self.poll_interval_s}."
            )
        if not 1 <= self.per_page <= _MAX_GITHUB_PER_PAGE:
            raise ValueError(
                "WORKMATE_GITHUB_PER_PAGE musi być w zakresie "
                f"1..{_MAX_GITHUB_PER_PAGE}, jest: {self.per_page}."
            )
        unknown = [k for k in self.watch_kinds if k not in _ALLOWED_GITHUB_WATCH_KINDS]
        if unknown:
            raise ValueError(
                "WORKMATE_GITHUB_WATCH_KINDS zawiera nieznane rodzaje: "
                f"{unknown}. Dozwolone: {', '.join(_ALLOWED_GITHUB_WATCH_KINDS)}."
            )
        if not self.watch_kinds:
            raise ValueError("WORKMATE_GITHUB_WATCH_KINDS nie może być puste.")
        # Recenzje odpytujemy per-PR, a kandydatów (otwarte PR) odkrywamy z ``/issues`` — bez
        # „issues"/„pulls" nie byłoby skąd; odrzucamy cichą, funkcjonalnie martwą konfigurację.
        if "reviews" in self.watch_kinds and not (
            "issues" in self.watch_kinds or "pulls" in self.watch_kinds
        ):
            raise ValueError(
                "WORKMATE_GITHUB_WATCH_KINDS='reviews' wymaga też 'issues' lub 'pulls' "
                "(otwarte PR do odpytania o recenzje odkrywamy z endpointu /issues)."
            )
        # Auto-komentarz CI to ZAPIS do GitHub — bez ogólnej bramki zapisu byłby martwy (nic nie
        # dopisze), a użytkownik myślałby, że działa; odrzucamy tę cichą, sprzeczną konfigurację.
        if self.enable_ci_auto_comment and not self.enable_github_write:
            raise ValueError(
                "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true wymaga też "
                "WORKMATE_GITHUB_ENABLE_WRITE=true (auto-komentarz dopisuje na GitHub)."
            )
        # …a także musi mieć CO komentować: bez „ci" w WATCH_KINDS poller nie pobiera przebiegów CI,
        # więc auto-komentarz nigdy nie zobaczy porażki do skomentowania (ta sama klasa cichej,
        # funkcjonalnie martwej konfiguracji co powyżej — odrzucamy fail-fast).
        if self.enable_ci_auto_comment and "ci" not in self.watch_kinds:
            raise ValueError(
                "WORKMATE_GITHUB_ENABLE_CI_AUTO_COMMENT=true wymaga też 'ci' w "
                "WORKMATE_GITHUB_WATCH_KINDS (bez zdarzeń CI nie ma czego komentować)."
            )

    def validate_worklog_limits(self) -> None:
        """Strojenie estymacji czasu (ADR 0034) — kontrole NIEZALEŻNE od tokenu i repozytorium.

        Wydzielone z ``validate()`` z tego samego powodu co ``JiraSettings.validate_limits``:
        pełnego ``validate()`` nie da się zawołać z drzwi Teams, bo bezwarunkowo żąda tokenu
        i repo, więc wywróciłoby każde wdrożenie bez GitHuba. Sufity muszą jednak obowiązywać
        po stronie, która faktycznie liczy estymację — inaczej absurd z ``.env`` egzekwowałby
        wyłącznie proces pollera, czyli nie ten, który go używa.
        """
        if not 5 <= self.worklog_idle_gap_minutes <= _MAX_WORKLOG_IDLE_GAP_MIN:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES musi być w zakresie "
                f"5..{_MAX_WORKLOG_IDLE_GAP_MIN}, jest: {self.worklog_idle_gap_minutes}."
            )
        if not 0 <= self.worklog_ramp_up_minutes <= _MAX_WORKLOG_RAMP_UP_MIN:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_RAMP_UP_MINUTES musi być w zakresie "
                f"0..{_MAX_WORKLOG_RAMP_UP_MIN}, jest: {self.worklog_ramp_up_minutes}."
            )
        # Rozbieg dłuższy niż przerwa kończąca sesję dawałby estymacje NACHODZĄCE na siebie
        # (doliczony czas sprzed sesji sięgałby w poprzednią) — cichy bezsens, więc odrzucamy.
        if self.worklog_ramp_up_minutes > self.worklog_idle_gap_minutes:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_RAMP_UP_MINUTES nie może przekraczać "
                "WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES (estymacje sesji zachodziłyby "
                f"na siebie): {self.worklog_ramp_up_minutes} > {self.worklog_idle_gap_minutes}."
            )
        if self.worklog_round_minutes not in _ALLOWED_ROUND_MINUTES:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_ROUND_MINUTES musi być jedną z "
                f"{_ALLOWED_ROUND_MINUTES}, jest: {self.worklog_round_minutes}."
            )
        if not 0 < self.worklog_max_session_hours <= _MAX_WORKLOG_SESSION_HOURS:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_MAX_SESSION_HOURS musi być w zakresie "
                f"0..{_MAX_WORKLOG_SESSION_HOURS}, jest: {self.worklog_max_session_hours}."
            )
        if not 1 <= self.worklog_max_range_days <= MAX_WORKLOG_RANGE_DAYS:
            raise ValueError(
                "WORKMATE_GITHUB_WORKLOG_MAX_RANGE_DAYS musi być w zakresie "
                f"1..{MAX_WORKLOG_RANGE_DAYS}, jest: {self.worklog_max_range_days}."
            )
        # Nazwę strefy sprawdzamy próbą zbudowania ``ZoneInfo``: literówka (``Europe/Warszawa``)
        # inaczej wywróciłaby pierwsze wywołanie narzędzia, a nie start procesu.
        try:
            ZoneInfo(self.worklog_tz)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                f"WORKMATE_GITHUB_WORKLOG_TZ={self.worklog_tz!r} nie jest znaną strefą IANA "
                "(np. 'Europe/Warsaw')."
            ) from exc
