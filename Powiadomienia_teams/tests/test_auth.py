import os
import stat
from pathlib import Path

import pytest

from powiadomienia_teams.config import Settings
from powiadomienia_teams.graph.auth import (
    AmbiguousAccountError,
    AuthExpiredError,
    _jedyne_konto,
    _load_cache,
    _save_cache,
    build_token_provider,
    login_interactive,
)


class _FakeApp:
    """Atrapa msal.PublicClientApplication — bez sieci i bez pakietu msal."""

    def __init__(self, *, silent_result=None, accounts=("acc",)):
        self._silent_result = silent_result
        self._accounts = list(accounts)
        self.silent_calls = 0
        self.device_flow_initiated = False

    def get_accounts(self):
        return self._accounts

    def acquire_token_silent_with_error(self, scopes, account):
        # Prawdziwy MSAL zwraca tu słownik z `error`/`error_description` (kod AADSTS), a nie None
        # — na tym opiera się diagnostyka utraty sesji.
        self.silent_calls += 1
        return self._silent_result

    def initiate_device_flow(self, scopes):
        self.device_flow_initiated = True
        return {"user_code": "X", "message": "otwórz stronę i wpisz kod"}

    def acquire_token_by_device_flow(self, flow):
        return {"access_token": "interactive-token"}


class _FakeCache:
    has_state_changed = False


class _ChangedCache:
    has_state_changed = True

    def serialize(self):
        return "serialized-state"


def _settings(path: Path) -> Settings:
    return Settings(client_id="c", tenant_id="t", team_id="T", token_cache_path=path)


def _factory(app, cache=None):
    return lambda settings: (app, cache or _FakeCache())


def test_silent_success_returns_token(tmp_path: Path):
    app = _FakeApp(silent_result={"access_token": "tok"})
    provider = build_token_provider(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    assert provider() == "tok"
    assert not app.device_flow_initiated  # dostawca NIGDY nie inicjuje device-flow


def test_silent_failure_raises_and_never_starts_device_flow(tmp_path: Path):
    # KLUCZOWE: brak ważnego tokenu → AuthExpiredError, a NIE blokujący device-code w pętli usługi.
    app = _FakeApp(silent_result=None)
    provider = build_token_provider(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    with pytest.raises(AuthExpiredError):
        provider()
    assert not app.device_flow_initiated


def test_aadsts_code_reaches_the_exception_message(tmp_path: Path):
    """Przyczyna od Entra ID musi trafić do komunikatu — to jedyna diagnoza na serwerze.

    Bez niej „utracono sesję" nie odróżnia zmiany hasła od polityki Conditional Access, a te
    wymagają zupełnie różnych działań administratora.
    """
    app = _FakeApp(
        silent_result={
            "error": "invalid_grant",
            "error_description": (
                "AADSTS50173: The provided grant has expired due to it being revoked"
            ),
        }
    )
    provider = build_token_provider(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    with pytest.raises(AuthExpiredError, match="AADSTS50173"):
        provider()


def test_no_accounts_raises_without_silent_call(tmp_path: Path):
    app = _FakeApp(silent_result={"access_token": "x"}, accounts=())
    provider = build_token_provider(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    with pytest.raises(AuthExpiredError):
        provider()
    assert app.silent_calls == 0  # brak konta → nie próbuje cichego odświeżenia


def test_app_and_cache_built_once(tmp_path: Path):
    app = _FakeApp(silent_result={"access_token": "tok"})
    calls = {"n": 0}

    def factory(settings):
        calls["n"] += 1
        return app, _FakeCache()

    provider = build_token_provider(_settings(tmp_path / "c.bin"), app_factory=factory)
    provider()
    provider()
    provider()
    assert calls["n"] == 1  # app/cache budowane RAZ, nie co wywołanie


def test_cache_saved_only_when_changed(tmp_path: Path):
    path = tmp_path / "c.bin"
    app = _FakeApp(silent_result={"access_token": "tok"})
    provider = build_token_provider(_settings(path), app_factory=_factory(app, _ChangedCache()))
    provider()
    assert path.read_text() == "serialized-state"  # zrotowany refresh-token utrwalony


def test_cache_not_written_when_unchanged(tmp_path: Path):
    path = tmp_path / "c.bin"
    app = _FakeApp(silent_result={"access_token": "tok"})
    provider = build_token_provider(_settings(path), app_factory=_factory(app))
    provider()
    assert not path.exists()  # has_state_changed=False → brak zbędnego zapisu


def test_corrupted_cache_does_not_block_startup(tmp_path: Path):
    """Ucięty cache MUSI degradować się do pustego, a nie wywracać procesu.

    `--login`, czyli udokumentowana droga ratunkowa, idzie przez tę samą fabrykę — wyjątek tutaj
    czyniłby usługę nie do odzyskania bez ręcznego kasowania pliku w wolumenie Dockera.
    """
    path = tmp_path / "c.bin"
    path.write_text('{"AccessToken": {"ucię', encoding="utf-8")  # SIGKILL w trakcie zapisu
    cache = _load_cache(path)
    assert cache.serialize() == "{}"  # pusty cache = ponowne logowanie, czyli stan naprawialny


def test_cache_write_leaves_no_temp_file(tmp_path: Path):
    path = tmp_path / "c.bin"
    _save_cache(_ChangedCache(), path)
    assert path.read_text(encoding="utf-8") == "serialized-state"
    assert list(tmp_path.iterdir()) == [path]  # plik tymczasowy sprzątnięty przez os.replace


def test_interrupted_write_does_not_destroy_previous_cache(tmp_path: Path, monkeypatch):
    """Przerwany zapis MUSI zostawić poprzedni cache nienaruszony.

    `write_text` obcina plik przed zapisem, więc SIGKILL po karencji `docker compose down` albo OOM
    w trakcie rotacji refresh-tokenu zostawiał pusty cache — a ten wywracał następny start jeszcze
    przed pierwszym logiem, razem z `--login`, czyli jedyną drogą ratunkową.
    """
    path = tmp_path / "c.bin"
    path.write_text("stary-ale-dzialajacy", encoding="utf-8")

    def zapis_przerwany(_fd):
        raise OSError("proces ubity w trakcie zapisu")

    # Awaria PO zapisaniu pliku tymczasowego, a przed `os.replace` — najgorszy możliwy moment.
    monkeypatch.setattr(os, "fsync", zapis_przerwany)
    with pytest.raises(OSError):
        _save_cache(_ChangedCache(), path)
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == "stary-ale-dzialajacy"


def test_login_interactive_uses_device_flow(tmp_path: Path):
    app = _FakeApp(silent_result=None)
    login_interactive(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    assert app.device_flow_initiated  # jawne logowanie korzysta z device-code


def test_msal_dostaje_jawny_timeout(monkeypatch):
    """Bez `timeout` MSAL wiąże `timeout=None` — żądania do Entra ID nie mają limitu czasu.

    Blackhole sieciowy (firewall DROP zamiast REJECT) zawieszał wtedy proces bezterminowo, a że
    Docker nie restartuje kontenera „unhealthy", healthcheck by tego nie uratował. `refresh_auth()`
    jest na gorącej pętli: w przebiegu, w listenerze i w pulsie.
    """
    import msal

    from powiadomienia_teams.graph import auth as modul

    przekazane = {}

    class _Atrapa:
        def __init__(self, client_id, **kwargs):
            przekazane.update(kwargs)

    monkeypatch.setattr(msal, "PublicClientApplication", _Atrapa)
    modul._default_app_and_cache(_settings(Path("nieistotne.bin")))

    assert przekazane.get("timeout") == modul._MSAL_TIMEOUT_S
    assert przekazane["timeout"] > 0


def test_wiele_kont_w_cache_zatrzymuje_dostawce_zamiast_losowac_tozsamosc(tmp_path: Path):
    """Dwie sesje w cache MUSZĄ zatrzymać usługę, a nie zdecydować za operatora.

    Kolejność `get_accounts()` nie jest kontraktem MSAL, więc „weź accounts[0]" to losowanie
    tożsamości „głosu" bota. Skutek — wiadomości do całego zespołu wysłane z niewłaściwego konta
    — jest widoczny dla ludzi i nieodwracalny, więc lepszy fail-fast z instrukcją niż milcząca
    zgadywanka.
    """
    sciezka = tmp_path / "c.bin"
    app = _FakeApp(
        silent_result={"access_token": "tok"},
        accounts=({"username": "bot@firma.pl"}, {"username": "ala@firma.pl"}),
    )
    provider = build_token_provider(_settings(sciezka), app_factory=_factory(app))

    with pytest.raises(AuthExpiredError) as wyjatek:
        provider()

    komunikat = str(wyjatek.value)
    assert "bot@firma.pl" in komunikat and "ala@firma.pl" in komunikat  # KTÓRE konta kolidują
    assert str(sciezka) in komunikat and "--login" in komunikat  # instrukcja naprawy w komunikacie
    assert app.silent_calls == 0  # żaden token nie został pobrany „na wszelki wypadek"


def test_jedno_konto_w_cache_dziala_bez_zmian(tmp_path: Path):
    """Kontrola granicy: obrona przed wieloma kontami nie może blokować normalnej pracy."""
    app = _FakeApp(silent_result={"access_token": "tok"}, accounts=({"username": "bot@firma.pl"},))
    provider = build_token_provider(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    assert provider() == "tok"


def test_logowanie_odmawia_przy_wielu_kontach_zamiast_dolozyc_trzecie(tmp_path: Path):
    """Device-flow przy dwóch kontach w cache dołożyłby TRZECIE i pogłębił problem.

    Operator zobaczyłby „zalogowano", a usługa padłaby dopiero przy pierwszym odświeżeniu tokenu
    — czyli po wdrożeniu, z komunikatem oderwanym od czynności, która go wywołała.
    """
    app = _FakeApp(
        silent_result={"access_token": "tok"},
        accounts=({"username": "bot@firma.pl"}, {"username": "ala@firma.pl"}),
    )
    with pytest.raises(AmbiguousAccountError, match="--login"):
        login_interactive(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    assert not app.device_flow_initiated  # zatrzymani PRZED rozpoczęciem logowania


def test_cache_tokenu_powstaje_od_razu_z_prawami_600(tmp_path: Path, monkeypatch):
    """Refresh-token nie może istnieć nawet przez chwilę z prawami domyślnymi.

    `write_text` + `chmod` tworzyło plik pod umask (zwykle 0o022, czyli czytelny dla grupy
    i świata), zapisywało do niego token i dopiero POTEM zamykało prawa. Okno było krótkie, ale
    otwierało się przy każdej rotacji refresh-tokenu, a wystarczy jeden odczyt, żeby przejąć
    tożsamość bota. Sprawdzamy TRYB PRZEKAZANY DO `os.open` (nie prawa pliku), bo na Windows
    uprawnienia POSIX są ignorowane, a błąd dotyczy właśnie kolejności operacji.
    """
    path = tmp_path / "c.bin"
    otwarcia: list[tuple[int, int]] = []
    prawdziwy_open = os.open

    def zapamietaj(sciezka, flagi, tryb=0o777, **kwargs):
        otwarcia.append((flagi, tryb))
        return prawdziwy_open(sciezka, flagi, tryb, **kwargs)

    monkeypatch.setattr(os, "open", zapamietaj)
    _save_cache(_ChangedCache(), path)
    monkeypatch.undo()

    assert otwarcia, "cache tokenu nie powstał przez os.open — prawa zależą od umask"
    flagi, tryb = otwarcia[0]
    assert tryb == 0o600
    assert flagi & os.O_CREAT and flagi & os.O_EXCL  # plik z TEGO utworzenia, nie cudza resztka
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_niejednoznaczne_konto_nie_wypuszcza_adresow_na_kanal_zewnetrzny():
    """Adresy kont to dane osobowe — log usługi tak, webhook alertów nie.

    `operator.zglos_utrate_sesji` woła `alert(settings, ..., str(blad))`, a `alerts.send_alert`
    wysyła to na `POWIADOMIENIA_ALERT_WEBHOOK_URL`, który z założenia bywa POZA organizacją
    (Power Automate, Slack, dowolny endpoint operatora). Pełny komunikat `AmbiguousAccountError`
    wymienia adresy e-mail WSZYSTKICH kont z cache tokenu, więc dziś wychodzą one na zewnątrz.

    Linia repozytorium miała na to osobny atrybut `publiczny` (treść zredagowana dla kanału
    zewnętrznego, pełna dla logu). Obraz 0.2.19 go nie ma — to nie cofnięcie, tylko rozwidlenie:
    build powstał z drzewa, w którym ta poprawka nigdy nie istniała.

    Poprawka nie jest kosmetyczna i nie polega na wycięciu treści z logu: log ma zostać
    diagnostyczny, a zredagowana ma być WYŁĄCZNIE ta kopia, która idzie webhookiem.
    """
    with pytest.raises(AmbiguousAccountError) as zlapany:
        _jedyne_konto(
            [{"username": "ala@firma.pl"}, {"username": "bot@firma.pl"}],
            Path("/dane/cache.bin"),
        )
    blad = zlapany.value
    assert "ala@firma.pl" in str(blad)  # pełna treść (log) nadal diagnostyczna
    publiczny = getattr(blad, "publiczny", str(blad))
    assert "ala@firma.pl" not in publiczny
    assert "bot@firma.pl" not in publiczny
    assert "2 kont" in publiczny  # operator wie, CO się stało
    assert "--login" in publiczny


def test_pelna_tresc_niejednoznacznego_konta_zostaje_diagnostyczna():
    """Kontrola pozytywna do testu redakcji wyżej: log ma widzieć wszystko, i widzi."""
    sciezka = Path("/dane/cache.bin")
    with pytest.raises(AmbiguousAccountError) as zlapany:
        _jedyne_konto(
            [{"username": "ala@firma.pl"}, {"username": "bot@firma.pl"}],
            sciezka,
        )
    tresc = str(zlapany.value)
    assert "ala@firma.pl" in tresc and "bot@firma.pl" in tresc
    # Przez ``str(sciezka)``, a nie przez literał: komunikat składa ``str(Path(...))``, więc
    # ``Path`` renderuje się separatorem PLATFORMY. Literał POSIX mierzył system, na którym
    # biegnie test, zamiast intencji „operator wie, KTÓRY plik usunąć" (#147).
    assert str(sciezka) in tresc
    assert "--login" in tresc


def test_resztka_po_ubitym_procesie_nie_blokuje_rotacji_tokenu(tmp_path: Path):
    """`O_EXCL` chroni prawa pliku, ale sam w sobie jest pułapką na gorącej ścieżce.

    Jedna resztka `.tmp` po SIGKILL wywracałaby KAŻDĄ kolejną rotację refresh-tokenu
    (`FileExistsError`), czyli po ~90 dniach usługę nie do odzyskania bez ręcznego kasowania
    pliku w wolumenie.
    """
    path = tmp_path / "c.bin"
    resztka = path.with_suffix(path.suffix + ".tmp")
    resztka.write_text("ucięty zapis sprzed awarii", encoding="utf-8")

    _save_cache(_ChangedCache(), path)

    assert path.read_text(encoding="utf-8")  # cache zapisany mimo resztki
    assert not resztka.exists()  # i posprzątany po sobie
