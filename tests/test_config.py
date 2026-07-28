"""Testy wspólnych helperów ``config.py`` — walidacja zapisywalności (R/L1) i domyślne ścieżki.

``require_writable`` to fail-fast dla TRWAŁYCH ścieżek: bez niego domyślne ``~/.workmate`` na
koncie kontenera z ``--no-create-home`` (i rootfs ``read_only``) przyjmuje zapis dopiero „w
próżnię" — poller odkrywa problem w pętli łapiącej wyjątki, jako cichy crash-loop bez watermarku.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from workmate.config import _DEFAULT_TOKENS_FILE, require_writable


def test_require_writable_accepts_and_creates_writable_dir(tmp_path):
    """Katalog docelowy nie istnieje ⇒ helper go tworzy i potwierdza zapis; śmieci nie zostają."""
    target = tmp_path / "state" / "github_state.json"

    require_writable(target, "WORKMATE_GITHUB_STATE")

    assert target.parent.is_dir()
    # Plik próbny został sprzątnięty — healthcheck/operator nie znajdzie śmiecia.
    assert list(target.parent.glob(".workmate-writetest-*")) == []


def test_require_writable_raises_when_ancestor_is_a_file(tmp_path):
    """Niezapisywalność wymuszamy PLIKIEM w roli katalogu, nie ``chmod``.

    Etap ``test`` obrazu biegnie z uprawnieniami roota, a root omija bity uprawnień — ``chmod
    000`` nie zablokowałby zapisu. ``mkdir(parents=True)`` pod plikiem rzuca ``NotADirectoryError``
    (``OSError``) niezależnie od uid, więc test jest wiarygodny także w obrazie.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="ascii")
    target = blocker / "sub" / "state.json"

    # Komunikat MUSI wskazywać zmienną do nadpisania — operator ma wiedzieć, co ustawić.
    with pytest.raises(ValueError, match="WORKMATE_GITHUB_STATE"):
        require_writable(target, "WORKMATE_GITHUB_STATE")


def test_default_tokens_file_is_platform_appropriate():
    """(b) Domyślny magazyn tokenów zależy od platformy: POSIX ⇒ wolumen stanu floty."""
    default = _DEFAULT_TOKENS_FILE
    if os.name == "nt":
        assert default == Path("C:/ProgramData/WorkMate/tokens.json")
    else:
        assert default == Path("/var/lib/workmate/tokens.json")
