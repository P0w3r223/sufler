"""Ustawienia menedżera wykonawców (ADR 0012) — kontener na rozmowę."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from workmate.config._env import _float_from_env, _int_from_env, _path_from_env
from workmate.config.shell import _DEFAULT_MANAGER_SOCKET

# Domyślne punkty montażu i nazwy wolumenów menedżera — spójne z compose (infra 0012). Menedżer
# montuje wolumen brudnopisu i gniazd pod TYMI SAMYMI ścieżkami co aplikacja, żeby ścieżka gniazda
# oddawana w ``ensure`` była ważna po obu stronach bez tłumaczenia układów.
# Te trzy (i ``_DEFAULT_MANAGER_SOCKET`` z ``config/shell.py``) zostają literałami POSIX BEZ
# rozgałęzienia po ``os.name`` — inaczej niż ``_DEFAULT_TOKENS_FILE`` i ``_DEFAULT_SCHEDULE_CACHE``.
# Różnica jest rzeczowa, nie z niedopatrzenia: to gniazda uniksowe i punkty montażu
# kontenera-wykonawcy (ADR 0057 / infra 0012), a ta zdolność na Windows nie działa w ogóle —
# nie ma ani gniazd
# AF_UNIX w tym układzie, ani `docker.sock`. Windowsowy wariant byłby fikcją wskazującą na nic.
# Pilnuje tego jawny rejestr w ``tests/test_config.py`` (ścieżki zwolnione z kontroli
# „absolutna na TEJ platformie" muszą być wymienione z nazwy).
_DEFAULT_SCRATCHPAD_ROOT = Path("/home/scratchpad")
_DEFAULT_EXEC_SOCK_ROOT = Path("/var/run/workmate-exec")
_DEFAULT_DOCKER_SOCKET = Path("/var/run/docker.sock")


@dataclass(frozen=True)
class ExecManagerSettings:
    """Konfiguracja procesu MENEDŻERA wykonawców (``workmate-exec-manager``, ADR infra 0012).

    Menedżer to jedyny komponent floty trzymający ``docker.sock`` (równoważnik roota na hoście),
    więc
    jego konfiguracja jest jawna i wąska: gdzie słucha aplikacji (``control_socket``), gdzie ma
    ``docker.sock``, jakim obrazem i wolumenami stawia wykonawców (stały szablon ``docker run``)
    oraz
    parametry cyklu życia (limit N, TTL bezczynności, okno gotowości, takt reapu). ``image`` i nazwy
    wolumenów NIE mają sensownych wartości domyślnych — podaje je compose przy każdym wydaniu (tag
    obrazu się pod-bija), więc brak któregoś jest twardym błędem startu (``validate``).
    """

    control_socket: Path = _DEFAULT_MANAGER_SOCKET
    docker_socket: Path = _DEFAULT_DOCKER_SOCKET
    scratchpad_root: Path = _DEFAULT_SCRATCHPAD_ROOT
    sock_root: Path = _DEFAULT_EXEC_SOCK_ROOT
    image: str = ""
    scratchpad_volume: str = ""
    sock_volume: str = ""
    data_volume: str = ""
    notes_dir: str = "/mnt/system/notes"
    skills_source: str | None = None
    max_executors: int = 8
    idle_ttl_s: int = 900
    ready_timeout_s: int = 15
    reap_interval_s: int = 60
    # uid/gid, na którym biegnie wykonawca (``user`` w compose). Menedżer (root) nadaje go
    # podkatalogom scope'a i GNIAZDU KONTROLNEMU — bez tego aplikacja (10001) nie sięgnęłaby po
    # gniazdo utworzone przez roota (0660 owner root ≠ 10001), a wykonawca nie zapisałby brudnopisu.
    exec_uid: int = 10001
    exec_gid: int = 10001
    # ── Granice zużycia wykonawcy (ADR infra 0013) ────────────────────────────────────────
    # Wartości domyślne, nie wymagane: wykonawca ma wstać także wtedy, gdy compose ich nie poda —
    # inaczej podbicie paczki bez podbicia zmiennych zostawiałoby rozmowy bez powłoki. Sufit
    # pliku jest CELOWO tą samą liczbą, co kwota katalogu roboczego dla narzędzi
    # (``WORKMATE_WORKSPACE_MAX_FILE_MB``): powłoka i narzędzia piszą w to samo miejsce.
    exec_memory_mb: int = 512
    exec_pids_limit: int = 128
    exec_cpu_limit: float = 1.0
    exec_max_file_mb: int = 5
    exec_max_open_files: int = 256

    @classmethod
    def from_env(cls) -> ExecManagerSettings:
        return cls(
            control_socket=_path_from_env("WORKMATE_EXEC_MANAGER_SOCKET", _DEFAULT_MANAGER_SOCKET),
            docker_socket=_path_from_env("WORKMATE_DOCKER_SOCKET", _DEFAULT_DOCKER_SOCKET),
            scratchpad_root=_path_from_env(
                "WORKMATE_EXEC_SCRATCHPAD_ROOT", _DEFAULT_SCRATCHPAD_ROOT
            ),
            sock_root=_path_from_env("WORKMATE_EXEC_SOCK_ROOT", _DEFAULT_EXEC_SOCK_ROOT),
            image=os.environ.get("WORKMATE_EXEC_IMAGE", "").strip(),
            scratchpad_volume=os.environ.get("WORKMATE_EXEC_SCRATCHPAD_VOLUME", "").strip(),
            sock_volume=os.environ.get("WORKMATE_EXEC_SOCK_VOLUME", "").strip(),
            data_volume=os.environ.get("WORKMATE_EXEC_DATA_VOLUME", "").strip(),
            notes_dir=os.environ.get("WORKMATE_NOTES_DIR", "/mnt/system/notes").strip(),
            skills_source=(os.environ.get("WORKMATE_EXEC_SKILLS_SOURCE", "").strip() or None),
            max_executors=_int_from_env("WORKMATE_EXEC_MAX", 8),
            idle_ttl_s=_int_from_env("WORKMATE_EXEC_IDLE_TTL_S", 900),
            ready_timeout_s=_int_from_env("WORKMATE_EXEC_READY_TIMEOUT_S", 15),
            reap_interval_s=_int_from_env("WORKMATE_EXEC_REAP_INTERVAL_S", 60),
            exec_uid=_int_from_env("WORKMATE_EXEC_UID", 10001),
            exec_gid=_int_from_env("WORKMATE_EXEC_GID", 10001),
            exec_memory_mb=_int_from_env("WORKMATE_EXEC_MEMORY_MB", 512),
            exec_pids_limit=_int_from_env("WORKMATE_EXEC_PIDS_LIMIT", 128),
            exec_cpu_limit=_float_from_env("WORKMATE_EXEC_CPU_LIMIT", 1.0),
            exec_max_file_mb=_int_from_env("WORKMATE_EXEC_MAX_FILE_MB", 5),
            exec_max_open_files=_int_from_env("WORKMATE_EXEC_MAX_OPEN_FILES", 256),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy brak stałego szablonu albo parametr cyklu życia jest bez sensu.

        Menedżer bez obrazu/wolumenów nie ma z czego złożyć ``docker run`` — a cichy start
        „bez szablonu" skończyłby się pierwszym ``ensure`` odbitym błędem Dockera, długo po starcie.
        Lepszy głośny błąd tu, przy składaniu procesu.
        """
        missing = [
            name
            for name, value in (
                ("WORKMATE_EXEC_IMAGE", self.image),
                ("WORKMATE_EXEC_SCRATCHPAD_VOLUME", self.scratchpad_volume),
                ("WORKMATE_EXEC_SOCK_VOLUME", self.sock_volume),
                ("WORKMATE_EXEC_DATA_VOLUME", self.data_volume),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"Menedżer wykonawców wymaga zmiennych: {', '.join(missing)}")
        if self.max_executors < 1:
            raise ValueError(f"WORKMATE_EXEC_MAX musi być >= 1, jest: {self.max_executors}")
        if self.idle_ttl_s < 1:
            raise ValueError(f"WORKMATE_EXEC_IDLE_TTL_S musi być >= 1, jest: {self.idle_ttl_s}")
        if self.ready_timeout_s < 1:
            raise ValueError(
                f"WORKMATE_EXEC_READY_TIMEOUT_S musi być >= 1, jest: {self.ready_timeout_s}"
            )
        if self.reap_interval_s < 1:
            raise ValueError(
                f"WORKMATE_EXEC_REAP_INTERVAL_S musi być >= 1, jest: {self.reap_interval_s}"
            )
        # Granice zużycia (ADR infra 0013) sprawdzamy TU, nie w kontenerze-wykonawcy: zero albo
        # wartość ujemna znaczy dla Docker API „bez limitu", więc literówka w compose zdejmowałaby
        # granicę po cichu, zostawiając wykonawcę wyglądającego na utwardzony. Menedżer jest
        # jedynym miejscem, gdzie ta liczba jest jeszcze konfiguracją, a nie faktem o kontenerze.
        # ZAKRESY, nie same podłogi — jak przy bliźniaczej kwocie katalogu roboczego
        # (``config/workspace.py``).
        # Sama podłoga „>= 1" przepuszcza wartości, które odbije dopiero Docker (minimum 6 MB
        # pamięci, 0.01 CPU) albo które zabiją kontener na starcie — awaria wtedy jest głośna,
        # ale późna i w INNYM PROCESIE niż literówka, więc operator szuka jej nie tam.
        for nazwa, wartosc, dol, gora in (
            ("WORKMATE_EXEC_MEMORY_MB", self.exec_memory_mb, 16, 8192),
            ("WORKMATE_EXEC_PIDS_LIMIT", self.exec_pids_limit, 8, 4096),
            ("WORKMATE_EXEC_MAX_FILE_MB", self.exec_max_file_mb, 1, 1024),
            ("WORKMATE_EXEC_MAX_OPEN_FILES", self.exec_max_open_files, 32, 65536),
        ):
            if not dol <= wartosc <= gora:
                raise ValueError(f"{nazwa} musi być w zakresie {dol}..{gora}, jest: {wartosc}")
        # Sufit rzędu liczby rdzeni realnego hosta, nie „dowolnie dużo": kwota ponad liczbę
        # rdzeni jest fikcją, a uzasadnienie sufitu czasu procesora w wykonawcy (`exec_server`)
        # wisi na założeniu, że kwota nie przekracza rzędu jednego rdzenia.
        if not 0.01 <= self.exec_cpu_limit <= 16:
            raise ValueError(
                f"WORKMATE_EXEC_CPU_LIMIT musi być w zakresie 0.01..16, jest: {self.exec_cpu_limit}"
            )
