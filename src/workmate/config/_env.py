"""Wspólne pomocniki odczytu zmiennych środowiskowych i korzenia repozytorium.

Przez te funkcje idzie KAŻDE ustawienie — bramki przez ``_bool_from_env``, sufity
pollerów przez ``_int_from_env``, listy odbiorców i hostów przez ``_list_from_env``."""

from __future__ import annotations

import os
from pathlib import Path


def _repo_root_or_none(start: Path) -> Path | None:
    """Korzeń repozytorium (katalog z ``pyproject.toml``) albo ``None``, gdy go nie ma.

    ``None`` znaczy „kod nie leży w drzewie repozytorium" — tak jest po instalacji z wheela
    albo w obrazie kontenera. Wołający, który pilnuje ścieżek WZGLĘDEM repo, nie ma wtedy
    czego pilnować i musi kontrolę pominąć, zamiast podstawiać byle katalog.
    """
    for parent in (start, *start.parents):
        if (parent / "pyproject.toml").is_file():
            return parent
    return None


def _find_repo_root(start: Path) -> Path:
    """Korzeń repozytorium do wyznaczania ścieżek domyślnych; awaryjnie katalog roboczy.

    Pozwala uruchamiać serwer niezależnie od bieżącego katalogu roboczego
    (np. przez ``uv run`` z dowolnego miejsca), bez zaszywania ścieżek w kodzie. Degradacja
    do ``cwd`` jest tu w porządku (chodzi o miejsce na dane), ale NIE nadaje się do kontroli
    bezpieczeństwa — te używają ``_repo_root_or_none``.
    """
    return _repo_root_or_none(start) or Path.cwd()


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else default


_PRAWDA = ("1", "true", "yes", "on")
_FALSZ = ("0", "false", "no", "off")


def _bool_from_env(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in _PRAWDA


def _scisly_bool_from_env(name: str, default: bool) -> bool:
    """Jak ``_bool_from_env``, ale nieznana wartość jest BŁĘDEM, nie cichym ``False``.

    ``_bool_from_env`` mapuje wszystko spoza listy prawdy na ``False`` i dla bramek nazwanych
    ``enable_*`` jest to kierunek bezpieczny: literówka zostawia zdolność wyłączoną. Dla flagi
    o ODWRÓCONEJ polaryzacji ten sam mechanizm działa przeciwnie —
    ``WORKMATE_TEAMS_DIGEST_DRY_RUN=ture`` dawało ``False``, czyli zdejmowało tryb próbny
    i uzbrajało realną wysyłkę DM do całej listy odbiorców. Pole nie nazywa się ``enable_*``,
    więc golden-test bramek go z konstrukcji nie obejmuje.

    Stąd osobny parser zamiast odwrócenia pola: nazwa ``dry_run`` jest w tym module i w env
    operatora od początku, a cicha zmiana jej znaczenia byłaby gorsza niż głośny błąd startu.
    """
    value = os.environ.get(name)
    if value is None:
        return default
    znormalizowana = value.strip().lower()
    if znormalizowana in _PRAWDA:
        return True
    if znormalizowana in _FALSZ:
        return False
    raise ValueError(
        f"{name} musi być jedną z wartości {_PRAWDA + _FALSZ}, jest: {value!r}. "
        "Ta flaga ma odwróconą polaryzację, więc nierozpoznana wartość nie może cicho "
        "oznaczać „wyłączone”."
    )


def _int_from_env(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} musi być liczbą całkowitą, jest: {value!r}") from exc


def _float_from_env(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ValueError(f"{name} musi być liczbą, jest: {value!r}") from exc


def _list_from_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    value = os.environ.get(name)
    if value is None:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


def _optional_path_from_env(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def require_writable(path: Path, env_var: str, *, is_directory: bool = False) -> None:
    """Twardy błąd startu, gdy katalog dla TRWAŁEJ ścieżki nie przyjmie zapisu (R/L1, GAPS).

    ``is_directory=True``, gdy ``path`` JEST katalogiem docelowym (baza wiedzy), a nie plikiem
    w nim — inaczej sonda badałaby katalog wyżej i przepuszczała ``data/notes`` zamontowane
    read-only wewnątrz zapisywalnego ``data/``. Dokładnie tak wygląda flota: wolumen bazy wiedzy
    bywa montowany osobno od reszty stanu (ADR 0008).

    Domyślne ścieżki stanu i baz (stałe ``_DEFAULT_*``) celują w ``~/.workmate``, a konto
    kontenera ma ``--no-create-home`` i rootfs ``read_only`` (Dockerfile/compose) — bez nadpisania
    env (``deploy/docker/env.example``) katalog jest niezapisywalny. Bez tej kontroli poller
    odkrywa to dopiero przy pierwszym zapisie, w pętli łapiącej wyjątki: cichy crash-loop bez
    utrwalonego watermarku (stan „w próżnię"). Sprawdzamy WCZEŚNIE i głośno — tworzymy katalog
    docelowy i piszemy plik próbny; ``env_var`` w komunikacie wskazuje, co nadpisać.

    Wołać na starcie drzwi (obok ``settings.validate()``), a NIE w samym ``validate`` — tam efekt
    uboczny ``mkdir`` zaśmiecałby testy konfiguracji, które wołają ``validate`` z domyślnymi
    ścieżkami. Zapis próbny jest szczery (tak samo pisze ``state.save``): łapie też rootfs
    ``read_only``, którego same bity uprawnień nie ujawniają.
    """
    target_dir = path.expanduser() if is_directory else path.expanduser().parent
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        probe = target_dir / f".workmate-writetest-{os.getpid()}"
        probe.write_text("", encoding="ascii")
        probe.unlink()
    except OSError as exc:
        raise ValueError(
            f"Trwała ścieżka {path} nie jest zapisywalna: {exc}. Ustaw {env_var} na katalog "
            "dostępny do zapisu (na flocie wolumen /var/lib/workmate — patrz "
            "deploy/docker/env.example)."
        ) from exc
