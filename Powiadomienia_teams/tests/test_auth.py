from pathlib import Path

import pytest

from powiadomienia_teams.config import Settings
from powiadomienia_teams.graph.auth import (
    AuthExpiredError,
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
    app = _FakeApp(silent_result={
        "error": "invalid_grant",
        "error_description": "AADSTS50173: The provided grant has expired due to it being revoked",
    })
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
    prawdziwy_zapis = Path.write_text

    def zapis_przerwany(self, data, **kwargs):
        prawdziwy_zapis(self, "", **kwargs)  # obcięcie — tak wygląda zapis ubity w połowie
        raise OSError("proces ubity w trakcie zapisu")

    monkeypatch.setattr(Path, "write_text", zapis_przerwany)
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
