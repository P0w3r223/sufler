from pathlib import Path

import pytest

from powiadomienia_teams.config import Settings
from powiadomienia_teams.graph.auth import (
    AuthExpiredError,
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

    def acquire_token_silent(self, scopes, account):
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


def test_login_interactive_uses_device_flow(tmp_path: Path):
    app = _FakeApp(silent_result=None)
    login_interactive(_settings(tmp_path / "c.bin"), app_factory=_factory(app))
    assert app.device_flow_initiated  # jawne logowanie korzysta z device-code
