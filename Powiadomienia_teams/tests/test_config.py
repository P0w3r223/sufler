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


def test_scheduling_group_required_when_not_dry_run(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", "false")
    monkeypatch.delenv("POWIADOMIENIA_SCHEDULING_GROUP_ID", raising=False)
    with pytest.raises(ConfigError):
        Settings.from_env().validate()


def test_scheduling_group_ok_when_provided(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_DRY_RUN", "false")
    monkeypatch.setenv("POWIADOMIENIA_SCHEDULING_GROUP_ID", "TAG")
    Settings.from_env().validate()  # nie rzuca


def test_only_user_ids_parsed_from_csv(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.setenv("POWIADOMIENIA_ONLY_USER_IDS", "id-a, id-b ,")
    assert Settings.from_env().only_user_ids == ("id-a", "id-b")


def test_only_user_ids_empty_by_default(monkeypatch):
    _set_required(monkeypatch)
    monkeypatch.delenv("POWIADOMIENIA_ONLY_USER_IDS", raising=False)
    assert Settings.from_env().only_user_ids == ()
