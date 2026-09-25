"""Ustawienia rdzenia serwera MCP: transport, log, tryb HTTP, ścieżki danych."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sufler.config._env import (
    _bool_from_env,
    _find_repo_root,
    _int_from_env,
    _list_from_env,
    _optional_path_from_env,
    _path_from_env,
)

# Dozwolone transporty serwera MCP. "stdio" to lokalny tryb dla Claude Code
# (Fazy 1 tyg. 1-3); "streamable-http" to wdrożenie sieciowe (tyg. 4, Bramka 3).
_ALLOWED_TRANSPORTS = ("stdio", "streamable-http")

# Dozwolone poziomy logowania: PEŁNY zestaw nazw znanych ``logging`` — z aliasami (``WARN``,
# ``FATAL``) i ``NOTSET`` włącznie. Kontrola ma zamienić niejasny komunikat biblioteki
# („Unknown level: 'VERBOSE'") na taki, który nazywa zmienną — a NIE zawęzić zbioru wejść, które
# działały. Instalacja z ``SUFLER_LOG_LEVEL=WARN`` jest legalna i musi wstać.
_ALLOWED_LOG_LEVELS = (
    "CRITICAL",
    "FATAL",
    "ERROR",
    "WARNING",
    "WARN",
    "INFO",
    "DEBUG",
    "NOTSET",
)

# ``uvicorn`` (gałąź HTTP) zna WŁASNY, węższy słownik nazw: aliasów ``logging`` w nim nie ma,
# a nieznana nazwa to ``KeyError`` w środku konfiguracji serwera. Tłumaczymy więc na najbliższy
# poziom uvicorna, zamiast odrzucać wejście, które ``logging`` przyjmuje bez zastrzeżeń.
# ``NOTSET`` (0) znaczy „nie filtruj" — po stronie uvicorna odpowiada mu ``trace`` (5),
# najniższy poziom, jaki ten serwer zna.
_UVICORN_LOG_LEVEL_ALIASES = {"WARN": "WARNING", "FATAL": "CRITICAL", "NOTSET": "TRACE"}

# Domyślny magazyn tokenów drzwi HTTP (ADR 0007): POZA repo i poza data/, żeby sekrety były poza
# zasięgiem narzędzi. Domyślna ZALEŻNA OD PLATFORMY (L1): na Windows katalog systemowy ProgramData,
# na POSIX wolumen stanu floty (/var/lib/sufler — spójne z docker-compose). Bez tego windowsowa
# ścieżka "C:/..." na Linuksie stawała się KATALOGIEM WZGLĘDNYM pod CWD (bez sensu). Nadpisywalna
# przez SUFLER_TOKENS_FILE; przy złym/nieobecnym pliku start HTTP jest fail-fast (server.py,
# TokenVerifier.from_file).
_DEFAULT_TOKENS_FILE = (
    Path("C:/ProgramData/Sufler/tokens.json")
    if os.name == "nt"
    else Path("/var/lib/sufler/tokens.json")
)

# Domyślne allowed_hosts trybu HTTP: wyłącznie loopback. Wdrożenie za IIS MUSI
# dołożyć publiczny host (np. "sufler.firma.pl:*") przez SUFLER_ALLOWED_HOSTS —
# inaczej realny nagłówek Host daje 421 (ochrona przed DNS-rebinding).
_DEFAULT_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")


@dataclass(frozen=True)
class Settings:
    """Niemutowalny zestaw ustawień serwera."""

    data_dir: Path
    notes_dir: Path
    projects_registry: Path
    transport: str
    log_level: str
    # Profil uprawnień per drzwi (Bramka 2, ADR 0006): czy narzędzie zapisu
    # (save_note) jest wystawione. Domyślnie WYŁĄCZONE na wszystkich drzwiach — bez wyjątku dla
    # lokalnego dev (amendment ADR 0006, 2026-07-31); operator włącza świadomie
    # (SUFLER_ENABLE_WRITE=true) albo per drzwi przekazuje ``enable_write=True`` w wiringu.
    enable_write: bool
    # Ustawienia trybu HTTP (streamable-http, Bramka 3 / ADR 0007). W trybie stdio
    # nieużywane — mają bezpieczne wartości domyślne i nie wymagają niczego od
    # lokalnego deva. Uwierzytelnianie bearer per osoba wpina się dopiero w gałęzi
    # HTTP w server.py (warstwa drzwi, rdzeń nietknięty).
    tokens_file: Path
    bind_host: str
    bind_port: int
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    tls_certfile: Path | None
    tls_keyfile: Path | None
    # Metryki użycia (Tor A): ścieżka pliku SQLite licznika wywołań. ``None`` (brak
    # SUFLER_METRICS_DB) = metryki wyłączone (drzwi nie zapisują nic). Osobny plik od
    # events.db/conversations.db — dane operacyjne poza bazą wiedzy; pseudonim zamiast tożsamości.
    metrics_db: Path | None
    # Dziennik audytu (Faza 0, ADR 0067): ścieżka pliku SQLite wpisów wywołań narzędzi. ``None``
    # (brak SUFLER_AUDIT_DB) = audyt wyłączony (drzwi nie zapisują nic). Osobny plik, retencja
    # dłuższa niż rozmów (Faza 7); rejestruje akcje/ścieżki i pseudonim, NIGDY treść.
    audit_db: Path | None
    # Migawki notatek przed mutacją (ADR 0065). POZA ``notes_dir`` rozmyślnie: agent czyta
    # katalog notatek zachłannie, więc kopie w środku wracałyby jako wyniki wyszukiwania,
    # a wykonawca montuje bazę wiedzy ``ro`` i stanu nie widzi wcale.
    note_snapshots_dir: Path

    @classmethod
    def from_env(cls) -> Settings:
        repo_root = _find_repo_root(Path(__file__).resolve())
        data_dir = _path_from_env("SUFLER_DATA_DIR", repo_root / "data")
        notes_dir = _path_from_env("SUFLER_NOTES_DIR", data_dir / "notes")
        projects_registry = _path_from_env(
            "SUFLER_PROJECTS_REGISTRY", data_dir / "projects" / "registry.yaml"
        )

        transport = os.environ.get("SUFLER_TRANSPORT", "stdio")
        if transport not in _ALLOWED_TRANSPORTS:
            raise ValueError(
                f"Nieobsługiwany SUFLER_TRANSPORT: {transport!r}. "
                f"Dozwolone: {', '.join(_ALLOWED_TRANSPORTS)}"
            )

        return cls(
            data_dir=data_dir,
            notes_dir=notes_dir,
            note_snapshots_dir=_path_from_env(
                "SUFLER_NOTE_SNAPSHOTS_DIR", data_dir / "snapshots" / "notes"
            ),
            projects_registry=projects_registry,
            transport=transport,
            log_level=os.environ.get("SUFLER_LOG_LEVEL", "INFO"),
            enable_write=_bool_from_env("SUFLER_ENABLE_WRITE", default=False),
            tokens_file=_path_from_env("SUFLER_TOKENS_FILE", _DEFAULT_TOKENS_FILE),
            bind_host=os.environ.get("SUFLER_BIND_HOST", "127.0.0.1"),
            bind_port=_int_from_env("SUFLER_BIND_PORT", 8000),
            allowed_hosts=_list_from_env("SUFLER_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS),
            allowed_origins=_list_from_env("SUFLER_ALLOWED_ORIGINS", ()),
            tls_certfile=_optional_path_from_env("SUFLER_TLS_CERTFILE"),
            tls_keyfile=_optional_path_from_env("SUFLER_TLS_KEYFILE"),
            metrics_db=_optional_path_from_env("SUFLER_METRICS_DB"),
            audit_db=_optional_path_from_env("SUFLER_AUDIT_DB"),
        )

    def validate(self) -> None:
        """Twardy błąd startu przy wartości, której żadne drzwi nie obsłużą (wołaj w ``main``).

        ``Settings`` była JEDYNĄ klasą ustawień bez ``validate`` — a przecież niesie te same
        klasy pomyłek co sąsiedzi. ``SUFLER_LOG_LEVEL=verbose`` wywracał start dopiero
        w ``logging.basicConfig``/``uvicorn``, komunikatem biblioteki („Unknown level: 'VERBOSE'"),
        który nie mówi, KTÓRĄ zmienną poprawić. ``transport`` sprawdza już ``from_env`` (rzuca
        przy budowie); tu domykamy resztę, żeby ``replace(settings, ...)`` w wiringu też przeszedł
        przez kontrolę.
        """
        if self.transport not in _ALLOWED_TRANSPORTS:
            raise ValueError(
                f"Nieobsługiwany SUFLER_TRANSPORT: {self.transport!r}. "
                f"Dozwolone: {', '.join(_ALLOWED_TRANSPORTS)}"
            )
        if self.log_level.strip().upper() not in _ALLOWED_LOG_LEVELS:
            raise ValueError(
                f"Nieobsługiwany SUFLER_LOG_LEVEL: {self.log_level!r}. "
                f"Dozwolone: {', '.join(_ALLOWED_LOG_LEVELS)}"
            )
        if not 1 <= self.bind_port <= 65535:
            raise ValueError(
                f"SUFLER_BIND_PORT musi być w zakresie 1..65535, jest: {self.bind_port}."
            )

    @property
    def uvicorn_log_level(self) -> str:
        """Poziom logowania w nazewnictwie ``uvicorn`` (małymi), z aliasami przetłumaczonymi.

        ``logging`` i ``uvicorn`` mają RÓŻNE słowniki nazw: ``WARN``/``FATAL``/``NOTSET`` są
        legalne dla pierwszego i nieznane drugiemu (``KeyError`` w środku ``uvicorn.Config``).
        Konfiguracja jest jedna, więc tłumaczenie stoi tutaj — drzwi HTTP biorą gotową wartość
        zamiast powtarzać mapowanie.
        """
        poziom = self.log_level.strip().upper()
        return _UVICORN_LOG_LEVEL_ALIASES.get(poziom, poziom).lower()

    def persistent_paths(self) -> tuple[tuple[Path, str, bool], ...]:
        """Trwałe ścieżki (ścieżka, zmienna, ``is_directory``) do sprawdzenia ``require_writable``.

        Jedno miejsce, w którym drzwi pytają „co tu w ogóle jest pisane" — bez tego lista żyła
        rozsypana po ``adapters/inbound/*/app.py`` i trzy ścieżki z niej wypadły: migawki notatek
        (``note_snapshots_dir``, ADR 0065) oraz obie bazy opcjonalne (metryki ADR 0049, audyt
        ADR 0067). Wszystkie trzy domyślnie lądują pod montażem read-only floty, a rejestrator
        audytu łyka błędy per wywołanie — operator miał więc „dziennik" z zerem wierszy zamiast
        twardego błędu startu.

        Ścieżki opcjonalne (``None`` = zdolność wyłączona) nie wchodzą na listę: nie ma czego
        sprawdzać, dopóki operator nie wskaże pliku. Wywołanie ``require_writable`` zostaje po
        stronie DRZWI (efekt uboczny ``mkdir`` nie może wejść do ``validate`` — patrz docstring
        ``require_writable``).
        """
        paths: list[tuple[Path, str, bool]] = [
            (self.note_snapshots_dir, "SUFLER_NOTE_SNAPSHOTS_DIR", True),
        ]
        if self.metrics_db is not None:
            paths.append((self.metrics_db, "SUFLER_METRICS_DB", False))
        if self.audit_db is not None:
            paths.append((self.audit_db, "SUFLER_AUDIT_DB", False))
        return tuple(paths)
