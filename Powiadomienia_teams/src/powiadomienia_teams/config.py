"""Ustawienia projektu — frozen dataclass + from_env() + validate().

Wzorzec jak w WorkMate (`src/workmate/config.py`). Prefiks zmiennych: `POWIADOMIENIA_`.
Sekrety (klucz Claude) mają `repr=False`. Domyślnie `dry_run=True` — nic nie wysyła ani
nie zapisuje, dopóki nie zostanie jawnie wyłączone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

_PREFIX = "POWIADOMIENIA_"

# Delegowane scope Graph — wszystkie nadane i potwierdzone na żywo (Smoke #1, 2026-07-14).
# MSAL sam dokłada openid/profile/offline_access — nie wpisywać ich tutaj.
_DEFAULT_SCOPES: tuple[str, ...] = (
    "User.Read",
    "User.ReadBasic.All",
    "TeamMember.Read.All",
    "Schedule.Read.All",
    "Schedule.ReadWrite.All",
    "Chat.Create",
    "Chat.ReadWrite",
    "ChatMessage.Send",
)

_DEFAULT_TOKEN_CACHE = Path.home() / ".workmate" / "teams_token_cache.bin"
_DEFAULT_STATE = Path.home() / ".workmate" / "powiadomienia_state.json"


class ConfigError(ValueError):
    """Brak lub niepoprawna wartość wymaganego ustawienia."""


@dataclass(frozen=True)
class TeamContext:
    """Dane JEDNEGO zespołu potrzebne obiegowi do odczytu/zapisu (team_id + grupa grafiku).

    Wydzielone ze skalarnej konfiguracji, żeby orkiestracja brała zespół z PARAMETRU, nie z
    globalnych ustawień. Dziś jest zawsze jeden (z konfiguracji jednozespołowej) — to szew, w który
    wielozespołowość ([ADR 0001](../../docs/adr/0001-multi-team-shifts-support.md)) wepnie iterację
    po wielu kontekstach bez zmiany logiki obiegu.
    """

    team_id: str
    scheduling_group_id: str | None = None


def _get(name: str, default: str = "") -> str:
    return os.environ.get(_PREFIX + name, default)


_PRAWDA = frozenset({"1", "true", "yes", "on", "tak"})
_FALSZ = frozenset({"0", "false", "no", "off", "nie"})


def _bool(name: str, default: bool) -> bool:
    """Wartość logiczna z otoczenia; nierozpoznana treść to BŁĄD KONFIGURACJI, nie „domyślnie".

    Wcześniejsze „cokolwiek spoza listy prawd znaczy fałsz" było fail-open dla NAJWAŻNIEJSZEJ
    bramki w tym projekcie: `POWIADOMIENIA_DRY_RUN`. Literówka (`fasle`), cudzysłowy zostawione
    przez `docker run -e DRY_RUN="true"`, polskie `prawda` albo ucięte `tru` dawały cicho
    `dry_run=False`, czyli tryb NA ŻYWO — wysyłkę do całego zespołu i zapis do grafiku klienta.
    Operator nie miał jak tego zauważyć przed pierwszą wiadomością. Zachowanie jak w `_int`:
    nieznana wartość zatrzymuje start z jednym czytelnym zdaniem.
    """
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    wartosc = raw.strip().lower()
    if wartosc in _PRAWDA:
        return True
    if wartosc in _FALSZ:
        return False
    raise ConfigError(
        f"{_PREFIX}{name} musi być wartością logiczną "
        f"({'/'.join(sorted(_PRAWDA))} albo {'/'.join(sorted(_FALSZ))}): {raw!r}"
    )


def _int(name: str, default: int) -> int:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{_PREFIX}{name} musi być liczbą całkowitą: {raw!r}") from exc


def _path(name: str, default: Path) -> Path:
    raw = os.environ.get(_PREFIX + name)
    return Path(raw).expanduser() if raw else default


def _list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(_PREFIX + name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _int_list(name: str, default: tuple[int, ...]) -> tuple[int, ...]:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        return tuple(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise ConfigError(
            f"{_PREFIX}{name} musi być listą liczb całkowitych po przecinku: {raw!r}"
        ) from exc


@dataclass(frozen=True)
class Settings:
    client_id: str
    tenant_id: str
    team_id: str
    scheduling_group_id: str | None = None
    scopes: tuple[str, ...] = _DEFAULT_SCOPES
    token_cache_path: Path = field(default_factory=lambda: _DEFAULT_TOKEN_CACHE)
    state_path: Path = field(default_factory=lambda: _DEFAULT_STATE)
    run_weekday: int = 4  # piątek (0=poniedziałek … 6=niedziela)
    run_hour: int = 16
    run_minute: int = 0
    timezone: str = "Europe/Warsaw"
    reply_window_hours: int = 48  # po tylu h ciszy zamknij okno odpowiedzi (status EXPIRED)
    send_expiry_message: bool = True  # przy wygaśnięciu wyślij uprzejme domknięcie do pracownika
    poll_interval_s: int = 10  # bazowy (minimalny) odstęp odpytywania; backoff go wydłuża
    poll_max_interval_s: int = 3600  # górny limit odstępu przy długiej ciszy (1 h)
    # Po ilu sekundach CISZY pracownika (ta sama kotwica co wygaśnięcie) wolno zajrzeć do Shifts,
    # by wykryć samodzielne uzupełnienie grafiku. -1 wyłącza funkcję; 0 = sprawdzaj co cichy cykl.
    self_fill_check_min_idle_s: int = 3600
    catchup_grace_hours: int = 6  # jak długo po minionym terminie wolno nadrobić przebieg (0=off)
    # --- Okno wysyłki wiadomości INICJOWANYCH przez bota (godziny ciszy) ---
    # Dotyczy cotygodniowej prośby, domknięcia po wygaśnięciu i podziękowania za samodzielne
    # uzupełnienie grafiku. NIE dotyczy odpowiedzi na wiadomość pracownika — rozmowę zaczął on.
    # Godziny lokalne zespołu (`timezone`), przedział [start, end): 8..18 = 8:00–17:59.
    send_window_start_hour: int = 8
    send_window_end_hour: int = 18
    send_window_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # 0=poniedziałek … 6=niedziela
    dry_run: bool = True
    only_user_ids: tuple[str, ...] = ()  # pusty = wszyscy; ustawiony = tryb pilotażowy
    llm_model: str = "claude-haiku-4-5"
    anthropic_api_key: str = field(default="", repr=False)
    # --- Praca bezobsługowa ---
    admin_user_id: str = ""  # AAD id administratora — odbiorca cotygodniowego podsumowania
    # URL webhooka bywa sekretem (potrafi zawierać token w ścieżce) → repr=False jak klucz API.
    alert_webhook_url: str = field(default="", repr=False)
    heartbeat_interval_h: int = 24  # co ile godzin sprawdzać sesję poza przebiegiem tygodniowym
    auth_failure_exit_delay_s: int = 600  # ile czekać przed wyjściem po utracie sesji
    # Po jakim czasie bez pulsu healthcheck uznaje pętlę za martwą. NIEZALEŻNE od sufitu nasłuchu:
    # puls bije co minutę (`app._spij_z_pulsem`), więc próg nie musi rosnąć razem z odstępem
    # odpytywania. Wcześniejsze wyprowadzanie progu z `poll_max_interval_s` dawało 2 h.
    health_max_age_s: int = 900

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def team_context(self) -> TeamContext:
        """Kontekst zespołu z obecnej (jednozespołowej) konfiguracji — na razie zawsze jeden."""
        return TeamContext(self.team_id, self.scheduling_group_id)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def heartbeat_path(self) -> Path:
        """Plik pulsu obok stanu — czyta go HEALTHCHECK obrazu.

        Wyprowadzony ze `state_path`, a nie osobną zmienną: ma leżeć na tym samym wolumenie co stan
        (inaczej byłby zapisywany do systemu plików tylko-do-odczytu), a jedna ścieżka mniej
        w konfiguracji to jedna okazja mniej, żeby rozjechała się z punktem montowania.
        """
        return self.state_path.with_name("heartbeat")

    def validate(self) -> None:
        missing = [n for n in ("client_id", "tenant_id", "team_id") if not getattr(self, n)]
        if missing:
            raise ConfigError(f"Brak wymaganych ustawień: {', '.join(missing)}")
        if not 0 <= self.run_weekday <= 6:
            raise ConfigError(f"run_weekday poza zakresem 0..6: {self.run_weekday}")
        if not 0 <= self.run_hour <= 23:
            raise ConfigError(f"run_hour poza zakresem 0..23: {self.run_hour}")
        if not 0 <= self.run_minute <= 59:
            raise ConfigError(f"run_minute poza zakresem 0..59: {self.run_minute}")
        if self.poll_interval_s < 5:
            raise ConfigError(f"poll_interval_s musi być ≥ 5 s: {self.poll_interval_s}")
        if self.poll_max_interval_s < self.poll_interval_s:
            raise ConfigError(
                f"poll_max_interval_s ({self.poll_max_interval_s}) musi być ≥ poll_interval_s "
                f"({self.poll_interval_s})"
            )
        if self.reply_window_hours <= 0:
            raise ConfigError(f"reply_window_hours musi być > 0: {self.reply_window_hours}")
        if self.self_fill_check_min_idle_s < -1:
            raise ConfigError(
                f"self_fill_check_min_idle_s musi być ≥ -1 (-1 wyłącza): "
                f"{self.self_fill_check_min_idle_s}"
            )
        if self.catchup_grace_hours < 0:
            raise ConfigError(f"catchup_grace_hours < 0 niedozwolone: {self.catchup_grace_hours}")
        if not 0 <= self.send_window_start_hour <= 23:
            raise ConfigError(
                f"send_window_start_hour poza zakresem 0..23: {self.send_window_start_hour}"
            )
        if not self.send_window_start_hour < self.send_window_end_hour <= 24:
            raise ConfigError(
                f"send_window_end_hour ({self.send_window_end_hour}) musi być > "
                f"send_window_start_hour ({self.send_window_start_hour}) i ≤ 24"
            )
        if not self.send_window_weekdays:
            # Pusta lista znaczyłaby „nigdy nie wolno wysłać" — usługa milczałaby, wyglądając
            # na sprawną. Wyłączenie okna to pełny tydzień (0,1,2,3,4,5,6), nie brak dni.
            raise ConfigError("send_window_weekdays nie może być puste (0..6 po przecinku)")
        poza = [d for d in self.send_window_weekdays if not 0 <= d <= 6]
        if poza:
            raise ConfigError(f"send_window_weekdays poza zakresem 0..6: {poza}")
        # Walidacja KRZYŻOWA: termin przebiegu musi mieścić się w oknie wysyłki. Bez niej
        # `run_hour=20` przechodził bez słowa, a usługa nie wysyłała już nigdy nic w terminie —
        # każdy przebieg odbijał się od godzin ciszy i przesuwał na następny dzień roboczy, więc
        # jedynym śladem był wpis INFO w logu. Cisza wygląda identycznie jak sprawna praca.
        if self.run_weekday not in self.send_window_weekdays:
            raise ConfigError(
                f"run_weekday ({self.run_weekday}) jest poza dniami okna wysyłki "
                f"{list(self.send_window_weekdays)} — przebieg nigdy nie wypadłby w oknie"
            )
        if not self.send_window_start_hour <= self.run_hour < self.send_window_end_hour:
            raise ConfigError(
                f"run_hour ({self.run_hour}) jest poza oknem wysyłki "
                f"[{self.send_window_start_hour}, {self.send_window_end_hour}) — przebieg "
                f"tygodniowy byłby odkładany do najbliższego otwarcia okna zamiast biec w terminie"
            )
        if self.heartbeat_interval_h <= 0:
            raise ConfigError(f"heartbeat_interval_h musi być > 0: {self.heartbeat_interval_h}")
        if self.health_max_age_s <= 0:
            raise ConfigError(f"health_max_age_s musi być > 0: {self.health_max_age_s}")
        if self.auth_failure_exit_delay_s < 0:
            raise ConfigError(
                f"auth_failure_exit_delay_s < 0 niedozwolone: {self.auth_failure_exit_delay_s}"
            )
        if not self.dry_run and not self.scheduling_group_id:
            raise ConfigError(
                "scheduling_group_id jest wymagane, gdy dry_run=false (zapis zmian do Shifts)"
            )
        # Bez klucza SDK i tak wyśle żądanie (pusty string ≠ None, więc nie ma fallbacku na profil
        # OAuth) i dostanie 401 — dla KAŻDEJ odpowiedzi, po cichu, bo wyjątek łapie izolacja
        # per-osoba. Bot wysyłałby prośby, na które nigdy nie odpowiada. Fail-fast na starcie.
        if not self.dry_run and not self.anthropic_api_key:
            raise ConfigError(
                "anthropic_api_key jest wymagane, gdy dry_run=false (interpretacja odpowiedzi)"
            )
        try:
            _ = self.tz  # walidacja nazwy strefy
        except Exception as exc:
            raise ConfigError(f"Nieznana strefa czasowa: {self.timezone!r}") from exc

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            client_id=_get("CLIENT_ID"),
            tenant_id=_get("TENANT_ID"),
            team_id=_get("TEAM_ID"),
            scheduling_group_id=(_get("SCHEDULING_GROUP_ID") or None),
            token_cache_path=_path("TOKEN_CACHE", _DEFAULT_TOKEN_CACHE),
            state_path=_path("STATE_PATH", _DEFAULT_STATE),
            run_weekday=_int("RUN_WEEKDAY", 4),
            run_hour=_int("RUN_HOUR", 16),
            run_minute=_int("RUN_MINUTE", 0),
            timezone=_get("TIMEZONE", "Europe/Warsaw"),
            reply_window_hours=_int("REPLY_WINDOW_HOURS", 48),
            send_expiry_message=_bool("SEND_EXPIRY_MESSAGE", True),
            poll_interval_s=_int("POLL_INTERVAL_S", 10),
            poll_max_interval_s=_int("POLL_MAX_INTERVAL_S", 3600),
            self_fill_check_min_idle_s=_int("SELF_FILL_CHECK_MIN_IDLE_S", 3600),
            catchup_grace_hours=_int("CATCHUP_GRACE_HOURS", 6),
            send_window_start_hour=_int("SEND_WINDOW_START_HOUR", 8),
            send_window_end_hour=_int("SEND_WINDOW_END_HOUR", 18),
            send_window_weekdays=_int_list("SEND_WINDOW_WEEKDAYS", (0, 1, 2, 3, 4)),
            dry_run=_bool("DRY_RUN", True),
            # GUID-y normalizujemy NA WEJŚCIU — ta sama ostrożność, którą `reminders/replies.py`
            # stosuje po obu stronach porównania i tam ją uzasadnia. Bez niej GUID wklejony
            # WIELKIMI literami nie pasował do niczego, `missing` schodziło do zera, a podsumowanie
            # dla administratora mówiło „0 próśb" — awaria konfiguracji wyglądała wtedy dokładnie
            # jak spokojny tydzień.
            only_user_ids=tuple(v.casefold() for v in _list("ONLY_USER_IDS")),
            llm_model=_get("LLM_MODEL", "claude-haiku-4-5"),
            anthropic_api_key=(os.environ.get("ANTHROPIC_API_KEY") or _get("AGENT_API_KEY")),
            admin_user_id=_get("ADMIN_USER_ID"),
            alert_webhook_url=_get("ALERT_WEBHOOK_URL"),
            heartbeat_interval_h=_int("HEARTBEAT_INTERVAL_H", 24),
            auth_failure_exit_delay_s=_int("AUTH_FAILURE_EXIT_DELAY_S", 600),
            health_max_age_s=_int("HEALTH_MAX_AGE_S", 900),
        )
