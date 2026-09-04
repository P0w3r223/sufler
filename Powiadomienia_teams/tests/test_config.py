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
    """Komplet ustawień dla trybu na żywo (dry_run=false) — wszystkie bramki spełnione.

    0.2.19 dołożyło do tej listy KANAŁ ALERTÓW: webhook jest jedyną drogą powiadomienia
    niezależną od Graph i od AAD, a najważniejsze alerty powstają właśnie wtedy, gdy tamte nie
    działają. Bez adresu `send_alert` zwraca `False` po cichu — rezygnacja ma być jawna.
    """
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", "false")
    monkeypatch.setenv("POWIADOMIENIA_SCHEDULING_GROUP_ID", "TAG")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("POWIADOMIENIA_ALERT_WEBHOOK_URL", "https://przyklad.invalid/hook")


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


# --- Godziny ciszy -------------------------------------------------------------
#
# 0.2.19 nie ma `SEND_WINDOW_*` (ADR 0005, status NOT SHIPPED). Ten sam problem — „nie pisz do
# ludzi o nieludzkich porach" — rozwiązuje okno ciszy `CISZA_OD_H`/`CISZA_DO_H`, z jedną istotną
# różnicą polityki: godzina przebiegu wpadająca w ciszę jest OSTRZEŻENIEM, nie błędem startu.


def test_cisza_ma_domyslnie_okno_20_7(monkeypatch):
    _set_required(monkeypatch)
    s = Settings.from_env()
    s.validate()
    assert (s.cisza_od_h, s.cisza_do_h) == (20, 7)
    assert s.okno_ciszy.wylaczone is False


def test_cisza_da_sie_przestawic_zmiennymi(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_CISZA_OD_H", "22")
    monkeypatch.setenv("POWIADOMIENIA_CISZA_DO_H", "6")
    s = Settings.from_env()
    s.validate()
    assert (s.cisza_od_h, s.cisza_do_h) == (22, 6)


def test_rowne_godziny_wylaczaja_cisze(monkeypatch):
    """Jedyny sposób wyłączenia — osobna flaga dawałaby dwa źródła prawdy o tej samej rzeczy."""
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_CISZA_OD_H", "0")
    monkeypatch.setenv("POWIADOMIENIA_CISZA_DO_H", "0")
    s = Settings.from_env()
    s.validate()
    assert s.okno_ciszy.wylaczone is True
    assert s.godzina_przebiegu_w_ciszy is False


def test_godzina_ciszy_poza_zakresem_jest_bledem(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_CISZA_OD_H", "24")
    with pytest.raises(ConfigError, match="cisza_od_h"):
        Settings.from_env().validate()


def test_termin_w_godzinach_ciszy_jest_OSTRZEZENIEM_a_nie_bledem_startu(monkeypatch):
    """Zmiana polityki wobec linii repozytorium — i ma uzasadnienie, nie jest złagodzeniem.

    Taka konfiguracja jest legalna, ale kosztowna: przebieg jest odkładany co tydzień do końca
    ciszy. Tydzień kosztuje wyłącznie w złożeniu z `catchup_grace_hours == 0`, bo okno łaski NIE
    liczy godzin ciszy (`_catchup_due` odejmuje `cisza_pomiedzy`). Dlatego zamiast blokować start
    usługa sygnalizuje to ostrzeżeniem — a `godzina_przebiegu_w_ciszy` jest tym, co je wyzwala.
    """
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_HOUR", "22")  # wewnątrz domyślnego okna 20–7
    s = Settings.from_env()
    s.validate()  # bez wyjątku
    assert s.godzina_przebiegu_w_ciszy is True


def test_godzina_przebiegu_poza_cisza_nie_ostrzega(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_RUN_HOUR", "16")
    assert Settings.from_env().godzina_przebiegu_w_ciszy is False


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


def test_szablony_env_opisuja_godziny_ciszy():
    """Godziny ciszy są decyzją właściciela — muszą być widoczne w szablonie wdrożeniowym."""
    import re
    from pathlib import Path

    korzen = Path(__file__).resolve().parent.parent
    # Listę kluczy utrzymuje WYŁĄCZNIE `deploy/env.example` (CLAUDE.md); `.env.example` niesie
    # tylko RÓŻNICE wobec serwera, więc nie wolno od niego wymagać kompletu.
    tresc = (korzen / "deploy" / "env.example").read_text(encoding="utf-8")
    for zmienna in ("POWIADOMIENIA_CISZA_OD_H", "POWIADOMIENIA_CISZA_DO_H"):
        assert re.search(rf"^{zmienna}=", tresc, re.M), zmienna


def test_pilotaz_normalizuje_guidy_takze_przy_budowie_wprost():
    """Trzeci przypadek tej samej luki co w `incoming_after` — porównanie identyfikatorów AAD.

    GUID wpisany wielkimi literami w `ONLY_USER_IDS` nie pasuje do nikogo, więc pilotaż milczy,
    a podsumowanie mówi „0 próśb" — nieodróżnialnie od spokojnego tygodnia. 0.2.19 łagodzi to
    logiem podającym OBIE liczby (`runtime.nudge`: ile osób bez grafiku przed filtrem i po), więc
    operator MOŻE się zorientować — ale musi czytać logi kontenera, a to nie jest zabezpieczenie.

    `strict=True`, więc dodanie normalizacji zapali CI i wymusi zdjęcie markera.
    """
    s = Settings(
        client_id="c",
        tenant_id="t",
        team_id="team",
        only_user_ids=("AB-CD", "Ef"),
    )

    assert s.only_user_ids == ("ab-cd", "ef")
