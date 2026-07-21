"""Healthcheck ma odróżnić proces żywy od stojącego — 'Up' samo w sobie niczego nie dowodzi."""
from pathlib import Path

from powiadomienia_teams.healthcheck import zdrowy


def _srodowisko(monkeypatch, tmp_path: Path, sufit: str = "3600") -> Path:
    monkeypatch.setenv("POWIADOMIENIA_CLIENT_ID", "cid")
    monkeypatch.setenv("POWIADOMIENIA_TENANT_ID", "tid")
    monkeypatch.setenv("POWIADOMIENIA_TEAM_ID", "team")
    monkeypatch.setenv("POWIADOMIENIA_STATE_PATH", str(tmp_path / "state.json"))
    monkeypatch.setenv("POWIADOMIENIA_POLL_MAX_INTERVAL_S", sufit)
    return tmp_path / "heartbeat"


def test_brak_pulsu_jest_niezdrowy(monkeypatch, tmp_path: Path):
    _srodowisko(monkeypatch, tmp_path)
    ok, powod = zdrowy()
    assert ok is False
    assert "brak pliku pulsu" in powod


def test_swiezy_puls_jest_zdrowy(monkeypatch, tmp_path: Path):
    puls = _srodowisko(monkeypatch, tmp_path)
    puls.touch()
    ok, _ = zdrowy()
    assert ok is True


def test_stary_puls_jest_niezdrowy(monkeypatch, tmp_path: Path):
    """Proces, który przestał się budzić, MUSI zostać uznany za martwy."""
    puls = _srodowisko(monkeypatch, tmp_path)
    puls.touch()
    # Zegar przesunięty o 3 h, próg to `health_max_age_s` (domyślnie 900 s) — dawno przekroczony.
    ok, powod = zdrowy(now=puls.stat().st_mtime + 3 * 3600)
    assert ok is False
    assert "pętla stoi" in powod


def test_niski_sufit_nasluchu_nie_powoduje_migotania(monkeypatch, tmp_path: Path):
    """Nawet przy skrajnie niskim sufitcie nasłuchu próg zostaje na `health_max_age_s`.

    Gdyby próg wracał do wyprowadzania z sufitu, konfiguracja z sufitem 10 s uznawałaby usługę za
    martwą po kilkudziesięciu sekundach — czyli healthcheck migotałby przy normalnej pracy.
    """
    puls = _srodowisko(monkeypatch, tmp_path, sufit="10")
    puls.touch()
    ok, _ = zdrowy(now=puls.stat().st_mtime + 120)  # 2 min < 900 s progu
    assert ok is True


def test_prog_jest_niezalezny_od_sufitu_nasluchu(monkeypatch, tmp_path: Path):
    """Podniesienie sufitu nasłuchu nie może opóźniać wykrycia stojącej pętli.

    Próg był wcześniej wyprowadzany z `poll_max_interval_s`, więc zmiana sufitu z 5 min na 1 h
    przesunęła wykrywanie martwej pętli z 10 minut na 2 godziny — po cichu.
    """
    puls = _srodowisko(monkeypatch, tmp_path, sufit="3600")
    monkeypatch.setenv("POWIADOMIENIA_HEALTH_MAX_AGE_S", "900")
    puls.touch()

    ok, powod = zdrowy(now=puls.stat().st_mtime + 1000)  # 1000 s > 900 s progu
    assert ok is False, powod
    assert "pętla stoi" in powod


def test_prog_konfigurowalny(monkeypatch, tmp_path: Path):
    puls = _srodowisko(monkeypatch, tmp_path)
    monkeypatch.setenv("POWIADOMIENIA_HEALTH_MAX_AGE_S", "60")
    puls.touch()
    assert zdrowy(now=puls.stat().st_mtime + 30)[0] is True
    assert zdrowy(now=puls.stat().st_mtime + 90)[0] is False
