import pytest

from powiadomienia_teams.config import ConfigError, Settings


def _set_required(monkeypatch):
    monkeypatch.setenv("POWIADOMIENIA_CLIENT_ID", "cid")
    monkeypatch.setenv("POWIADOMIENIA_TENANT_ID", "tid")
    monkeypatch.setenv("POWIADOMIENIA_TEAM_ID", "team")


def test_from_env_defaults(monkeypatch):
    _set_required(monkeypatch)
    s = Settings.from_env()
    s.validate()
    assert s.dry_run is True
    assert s.run_weekday == 4  # piątek (domyślny termin przypomnienia)
    assert s.run_hour == 16
    assert s.authority.endswith("/tid")
    assert s.tz.key == "Europe/Warsaw"


def test_poll_ceiling_defaults_to_one_hour(monkeypatch):
    """Nieobecny pracownik: ~56 odpytań przez okno 48 h zamiast ~576 przy dawnym suficie 5 min."""
    _set_required(monkeypatch)
    monkeypatch.delenv("POWIADOMIENIA_POLL_MAX_INTERVAL_S", raising=False)
    monkeypatch.delenv("POWIADOMIENIA_POLL_INTERVAL_S", raising=False)
    s = Settings.from_env()
    s.validate()
    assert s.poll_interval_s == 10  # podłoga: rozmowa w toku biegnie w sekundach
    assert s.poll_max_interval_s == 3600


def test_missing_team_id_fails_validation(monkeypatch):
    monkeypatch.setenv("POWIADOMIENIA_CLIENT_ID", "cid")
    monkeypatch.setenv("POWIADOMIENIA_TENANT_ID", "tid")
    monkeypatch.delenv("POWIADOMIENIA_TEAM_ID", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env().validate()


def test_invalid_hour_fails_validation(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_HOUR", "25")
    with pytest.raises(ConfigError):
        Settings.from_env().validate()


def test_dry_run_can_be_disabled(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", "false")
    assert Settings.from_env().dry_run is False


def test_api_key_not_in_repr(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-value")
    s = Settings.from_env()
    assert s.anthropic_api_key == "sk-secret-value"
    assert "sk-secret-value" not in repr(s)


def test_agent_api_key_used_when_anthropic_key_empty(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # ustawione, ale puste
    monkeypatch.setenv("POWIADOMIENIA_AGENT_API_KEY", "sk-fallback")
    assert Settings.from_env().anthropic_api_key == "sk-fallback"


def test_non_numeric_int_raises_config_error(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_HOUR", "abc")
    with pytest.raises(ConfigError):
        Settings.from_env()


def test_invalid_weekday_fails_validation(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_WEEKDAY", "9")
    with pytest.raises(ConfigError):
        Settings.from_env().validate()


def test_invalid_timezone_fails_validation(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_TIMEZONE", "Mars/Phobos")
    with pytest.raises(ConfigError):
        Settings.from_env().validate()


def _set_live(monkeypatch):
    """Komplet ustawień dla trybu na żywo (dry_run=false) — wszystkie bramki spełnione."""
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", "false")
    monkeypatch.setenv("POWIADOMIENIA_SCHEDULING_GROUP_ID", "TAG")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")


def test_scheduling_group_required_when_not_dry_run(monkeypatch):
    _set_live(monkeypatch)
    monkeypatch.delenv("POWIADOMIENIA_SCHEDULING_GROUP_ID", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env().validate()


def test_scheduling_group_ok_when_provided(monkeypatch):
    _set_live(monkeypatch)
    Settings.from_env().validate()  # nie rzuca


def test_api_key_required_when_not_dry_run(monkeypatch):
    """Bez klucza bot wysłałby prośby, na które nigdy by nie odpowiedział — fail-fast na starcie."""
    _set_live(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("POWIADOMIENIA_AGENT_API_KEY", raising=False)
    with pytest.raises(ConfigError, match="anthropic_api_key"):
        Settings.from_env().validate()


def test_api_key_not_required_in_dry_run(monkeypatch):
    """W dry-run listener jest pominięty, więc brak klucza nie jest błędem konfiguracji."""
    _set_required(monkeypatch)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.delenv("POWIADOMIENIA_AGENT_API_KEY", raising=False)
    Settings.from_env().validate()  # nie rzuca


def test_only_user_ids_parsed_from_csv(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_ONLY_USER_IDS", "id-a, id-b ,")
    assert Settings.from_env().only_user_ids == ("id-a", "id-b")


def test_only_user_ids_empty_by_default(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("POWIADOMIENIA_ONLY_USER_IDS", raising=False)
    assert Settings.from_env().only_user_ids == ()


def test_self_fill_check_min_idle_s_defaults_to_one_hour(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("POWIADOMIENIA_SELF_FILL_CHECK_MIN_IDLE_S", raising=False)
    s = Settings.from_env()
    s.validate()
    assert s.self_fill_check_min_idle_s == 3600


def test_self_fill_check_min_idle_s_read_from_env(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SELF_FILL_CHECK_MIN_IDLE_S", "60")
    assert Settings.from_env().self_fill_check_min_idle_s == 60


def test_self_fill_check_min_idle_s_minus_one_disables_and_is_valid(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SELF_FILL_CHECK_MIN_IDLE_S", "-1")
    Settings.from_env().validate()  # nie rzuca — -1 wyłącza sprawdzanie


def test_self_fill_check_min_idle_s_below_minus_one_fails_validation(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SELF_FILL_CHECK_MIN_IDLE_S", "-2")
    with pytest.raises(ConfigError, match="self_fill_check_min_idle_s"):
        Settings.from_env().validate()


# --- Wartości logiczne: fail-closed -------------------------------------------


@pytest.mark.parametrize(
    "wartosc",
    ["fasle", "prawda", "tru", '"true"', "y", "T", "TRUE ", "1 ", "enabled", "-"],
)
def test_nierozpoznana_wartosc_logiczna_zatrzymuje_start(monkeypatch, wartosc):
    """Fail-closed dla NAJWAŻNIEJSZEJ bramki projektu.

    `_bool` traktował „cokolwiek spoza listy prawd" jako fałsz, więc literówka (`fasle`),
    cudzysłowy zostawione przez `-e DRY_RUN="true"`, polskie `prawda` czy ucięte `tru` dawały
    cicho `dry_run=False` — czyli TRYB NA ŻYWO: wysyłkę do całego zespołu i zapis do grafiku
    klienta. Operator nie miał jak tego zauważyć przed pierwszą wiadomością.

    `"TRUE "` i `"1 "` przechodzą (białe znaki obcinamy), więc parametry poniżej dobrane są tak,
    by każdy był naprawdę nierozpoznany po `strip().lower()`.
    """
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", wartosc)
    if wartosc.strip().lower() in {"1", "true", "yes", "on", "tak"}:
        assert Settings.from_env().dry_run is True
        return
    with pytest.raises(ConfigError, match="DRY_RUN"):
        Settings.from_env()


@pytest.mark.parametrize("wartosc", ["0", "false", "FALSE", " no ", "off", "nie"])
def test_rozpoznane_falsze_wylaczaja_tryb_probny(monkeypatch, wartosc):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", wartosc)
    assert Settings.from_env().dry_run is False


@pytest.mark.parametrize("wartosc", ["1", "true", "TAK", " yes ", "on"])
def test_rozpoznane_prawdy_wlaczaja_tryb_probny(monkeypatch, wartosc):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", wartosc)
    assert Settings.from_env().dry_run is True


# --- Okno wysyłki (godziny ciszy) ---------------------------------------------


def test_okno_wysylki_ma_domyslnie_dni_robocze_8_18(monkeypatch):
    _set_required(monkeypatch)
    s = Settings.from_env()
    s.validate()
    assert (s.send_window_start_hour, s.send_window_end_hour) == (8, 18)
    assert s.send_window_weekdays == (0, 1, 2, 3, 4)


def test_okno_wysylki_da_sie_przestawic_zmiennymi(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_START_HOUR", "6")
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_END_HOUR", "22")
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_WEEKDAYS", "0,1,2,3,4,5,6")
    s = Settings.from_env()
    s.validate()
    assert (s.send_window_start_hour, s.send_window_end_hour) == (6, 22)
    assert s.send_window_weekdays == (0, 1, 2, 3, 4, 5, 6)


def test_odwrocone_okno_wysylki_jest_bledem(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_START_HOUR", "18")
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_END_HOUR", "8")
    with pytest.raises(ConfigError, match="send_window_end_hour"):
        Settings.from_env().validate()


def test_puste_dni_okna_wysylki_sa_bledem(monkeypatch):
    """Pusta lista znaczyłaby „nigdy nie wysyłaj" — usługa milczałaby, wyglądając na sprawną."""
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_WEEKDAYS", " , ")
    with pytest.raises(ConfigError, match="send_window_weekdays"):
        Settings.from_env().validate()


def test_dzien_spoza_zakresu_jest_bledem(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_WEEKDAYS", "0,7")
    with pytest.raises(ConfigError, match="send_window_weekdays"):
        Settings.from_env().validate()


def test_termin_poza_godzinami_okna_wysylki_jest_bledem_startu(monkeypatch):
    """Konfiguracja, przy której przebieg NIGDY nie wypadnie w oknie, ma paść na starcie.

    `run_hour=20` przechodził bez słowa: każdy przebieg odbijał się od godzin ciszy i przesuwał
    na najbliższe otwarcie okna, więc usługa nie wysyłała już nic w terminie, a jedynym śladem
    był wpis INFO w logu. Cisza wygląda identycznie jak sprawna praca.
    """
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_HOUR", "20")
    with pytest.raises(ConfigError, match="run_hour"):
        Settings.from_env().validate()


def test_termin_w_dniu_spoza_okna_wysylki_jest_bledem_startu(monkeypatch):
    """Termin w sobotę przy oknie pn–pt to ta sama pułapka, tylko liczona dniami."""
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_WEEKDAY", "5")  # sobota
    with pytest.raises(ConfigError, match="run_weekday"):
        Settings.from_env().validate()


def test_termin_poza_oknem_przechodzi_gdy_okno_zostalo_poszerzone(monkeypatch):
    """Walidacja krzyżowa nie może blokować świadomej konfiguracji — sprawdza SPÓJNOŚĆ, nie gust."""
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_WEEKDAY", "5")
    monkeypatch.setenv("POWIADOMIENIA_RUN_HOUR", "20")
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_WEEKDAYS", "0,1,2,3,4,5,6")
    monkeypatch.setenv("POWIADOMIENIA_SEND_WINDOW_END_HOUR", "22")
    Settings.from_env().validate()  # bez wyjątku


def test_oba_szablony_env_maja_te_same_zmienne():
    """Dwa szablony (`.env.example` i `deploy/env.example`) nie mogą się rozjeżdżać.

    Nowe zmienne trafiały do jednego i nie do drugiego, więc operator wdrażający z paczki
    dockerowej nie wiedział nawet, że coś takiego istnieje. `deploy/` ma prawo mieć NADMIAROWE
    klucze (ścieżki wolumenów), ale nie wolno mu żadnego GUBIĆ.
    """
    import re
    from pathlib import Path

    korzen = Path(__file__).resolve().parent.parent

    def klucze(sciezka: Path) -> set[str]:
        tresc = sciezka.read_text(encoding="utf-8")
        return {m.group(1) for m in re.finditer(r"^(POWIADOMIENIA_[A-Z0-9_]+)=", tresc, re.M)}

    lokalne = klucze(korzen / ".env.example")
    wdrozeniowe = klucze(korzen / "deploy" / "env.example")
    assert lokalne - wdrozeniowe == set(), sorted(lokalne - wdrozeniowe)


def test_szablony_env_opisuja_okno_wysylki():
    """Godziny ciszy są decyzją właściciela — muszą być widoczne w obu szablonach."""
    import re
    from pathlib import Path

    korzen = Path(__file__).resolve().parent.parent
    for sciezka in (korzen / ".env.example", korzen / "deploy" / "env.example"):
        tresc = sciezka.read_text(encoding="utf-8")
        for zmienna in (
            "POWIADOMIENIA_SEND_WINDOW_START_HOUR",
            "POWIADOMIENIA_SEND_WINDOW_END_HOUR",
            "POWIADOMIENIA_SEND_WINDOW_WEEKDAYS",
        ):
            assert re.search(rf"^{zmienna}=", tresc, re.M), (sciezka.name, zmienna)


def test_pilotaz_normalizuje_guidy_takze_przy_budowie_wprost():
    """``from_env`` normalizowało samodzielnie, więc filtr działał dla procesu i przestawał dla
    każdego ``Settings(...)`` złożonego wprost. Awaria była cicha: GUID wielkimi literami nie
    pasował do nikogo, więc podsumowanie mówiło „0 próśb" — jak spokojny tydzień."""
    s = Settings(
        client_id="c",
        tenant_id="t",
        team_id="team",
        only_user_ids=("AB-CD", "Ef"),
    )

    assert s.only_user_ids == ("ab-cd", "ef")
