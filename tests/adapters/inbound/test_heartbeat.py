"""Testy pulsu żywotności pollerów (R5) — ścieżka, zapis, świeżość i entrypoint healthchecku."""

from __future__ import annotations

from pathlib import Path

from workmate.adapters.inbound.heartbeat import (
    heartbeat_path,
    is_fresh,
    main,
    notifier_heartbeat_path,
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


def test_is_fresh_false_when_the_file_cannot_be_read(tmp_path: Path, monkeypatch):
    """Healthcheck ma wydać WERDYKT, nie traceback — nieczytelny puls to brak dowodu życia."""

    def _odmowa(_self: Path) -> None:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Path, "stat", _odmowa)

    assert is_fresh(tmp_path / "d.heartbeat", 60) is False


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


def test_notifier_heartbeat_path_is_distinct_notify_sibling():
    """Puls notifiera to odrębny plik obok pulsu pollera (ADR 0067 §2)."""
    state = Path("/var/lib/workmate/github_state.json")
    assert notifier_heartbeat_path(state) == Path("/var/lib/workmate/github_state.notify.heartbeat")
    assert notifier_heartbeat_path(state) != heartbeat_path(state)


def test_main_multiple_files_healthy_only_when_all_fresh(tmp_path: Path):
    """Healthcheck z dwoma pulsami (poller + notifier): zdrowy tylko, gdy OBA świeże."""
    poller = tmp_path / "github_state.heartbeat"
    notify = tmp_path / "github_state.notify.heartbeat"
    write_heartbeat(poller)
    write_heartbeat(notify)
    both = ["--file", str(poller), "--file", str(notify), "--max-age", "3600"]
    assert main(both) == 0


def test_main_multiple_files_one_stale_is_unhealthy(tmp_path: Path):
    """Zablokowany notifier (brak jego pulsu) ⇒ kod 1, mimo świeżego pulsu pollera."""
    poller = tmp_path / "github_state.heartbeat"
    write_heartbeat(poller)  # poller żyje
    missing_notify = tmp_path / "github_state.notify.heartbeat"  # notifier zatkany — brak pliku
    args = ["--file", str(poller), "--file", str(missing_notify), "--max-age", "3600"]
    assert main(args) == 1
