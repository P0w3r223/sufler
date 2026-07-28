"""Testy pulsu żywotności pollerów (R5) — ścieżka, zapis, świeżość i entrypoint healthchecku."""

from __future__ import annotations

from pathlib import Path

from workmate.adapters.inbound.heartbeat import (
    heartbeat_path,
    is_fresh,
    main,
    write_heartbeat,
)


def test_heartbeat_path_is_state_sibling():
    """Puls to siostra pliku stanu (ten sam wolumen) z rozszerzeniem .heartbeat."""
    assert heartbeat_path(Path("/var/lib/workmate/github_state.json")) == Path(
        "/var/lib/workmate/github_state.heartbeat"
    )


def test_write_then_is_fresh_true(tmp_path: Path):
    """Świeżo zapisany puls jest 'świeży' w oknie max_age."""
    hb = tmp_path / "d.heartbeat"
    write_heartbeat(hb, now=1000.0)
    # mtime = teraz zapisu; z zegarem tuż po zapisie wiek < 100 s.
    assert is_fresh(hb, 100, now=hb.stat().st_mtime + 10)


def test_is_fresh_false_when_stale(tmp_path: Path):
    """Puls starszy niż max_age ⇒ niezdrowy (cofnięty mtime = ten sam efekt co przesunięcie now)."""
    hb = tmp_path / "d.heartbeat"
    write_heartbeat(hb)
    assert is_fresh(hb, 60, now=hb.stat().st_mtime + 10_000) is False


def test_is_fresh_false_when_missing(tmp_path: Path):
    """Brak pliku ⇒ niezdrowy — poller nigdy nie domknął cyklu albo skasowano stan."""
    assert is_fresh(tmp_path / "nie-ma.heartbeat", 60) is False


def test_write_is_atomic_no_tmp_left(tmp_path: Path):
    """Po zapisie nie zostaje plik tymczasowy — healthcheck nie trafi na obcięty plik."""
    hb = tmp_path / "d.heartbeat"
    write_heartbeat(hb)
    assert hb.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_main_returns_0_for_fresh(tmp_path: Path):
    """Entrypoint healthchecku: świeży puls ⇒ kod 0 (healthy)."""
    hb = tmp_path / "d.heartbeat"
    write_heartbeat(hb)
    assert main(["--file", str(hb), "--max-age", "3600"]) == 0


def test_main_returns_1_for_missing(tmp_path: Path):
    """Entrypoint healthchecku: brak pulsu ⇒ kod 1 (unhealthy)."""
    assert main(["--file", str(tmp_path / "nie-ma.heartbeat"), "--max-age", "60"]) == 1
