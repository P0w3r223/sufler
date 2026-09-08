from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ceidg_tool.config import (
    inspect_token,
    load_settings,
    mask_tokens,
    resolve_environment,
    safe_filename,
    token_fingerprint,
)
from ceidg_tool.errors import ConfigError, ProdWithoutConsentError

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def make_jwt(**claims: object) -> str:
    def seg(obj: object) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{seg({'alg': 'HS256', 'typ': 'JWT'})}.{seg(claims)}.{'x' * 43}"


def test_resolve_environment_requires_consent_for_prod() -> None:
    assert resolve_environment(None, prod_consent=False) == "test"
    assert resolve_environment("PROD", prod_consent=True) == "prod"
    with pytest.raises(ProdWithoutConsentError):
        resolve_environment("prod", prod_consent=False)
    with pytest.raises(ConfigError):
        resolve_environment("staging", prod_consent=True)


def test_token_sources_in_order(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CEIDG_TOKEN=z_pliku\nCEIDG_ENV=test\n", encoding="utf-8")
    from_file = load_settings(env_file=env_file, environ={}, use_keyring=False, now=NOW)
    assert from_file.token == "z_pliku" and from_file.token_source == ".env"

    from_env = load_settings(
        env_file=env_file, environ={"CEIDG_TOKEN": "ze_srodowiska"}, use_keyring=False, now=NOW
    )
    assert from_env.token == "ze_srodowiska" and from_env.token_source == "env"

    explicit = load_settings(
        env_file=env_file, environ={"CEIDG_TOKEN": "x"}, token="jawny", use_keyring=False, now=NOW
    )
    assert explicit.token == "jawny" and explicit.token_source == "argument"


def test_missing_token_gives_clear_message(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="Brak tokenu"):
        load_settings(env_file=tmp_path / "brak", environ={}, use_keyring=False, now=NOW)


def test_settings_repr_hides_token(tmp_path: Path) -> None:
    s = load_settings(
        env_file=None, environ={"CEIDG_TOKEN": "sekret123"}, use_keyring=False, now=NOW
    )
    assert "sekret123" not in repr(s)
    assert s.token_fp == token_fingerprint("sekret123")
    assert s.store_path.name == "store-test.sqlite"
    assert s.output_dir.name == "wyniki" and s.log_dir.name == "logi"


def test_jwt_inspection_reads_iat_and_exp() -> None:
    token = make_jwt(iat=int(NOW.timestamp()), exp=int((NOW + timedelta(days=3)).timestamp()))
    info = inspect_token(token)
    assert info.is_jwt and info.issued_at == NOW
    assert info.days_left(NOW) == pytest.approx(3.0)
    assert info.warning(NOW) is not None and "wygasa" in info.warning(NOW)  # type: ignore[operator]
    assert "ważny do" in info.validity_text(NOW)

    no_exp = inspect_token(make_jwt(iat=int(NOW.timestamp())))
    assert no_exp.expires_at is None and no_exp.warning(NOW) is None
    assert "brak daty wygaśnięcia" in no_exp.validity_text(NOW)

    opaque = inspect_token("nie-jwt")
    assert not opaque.is_jwt and opaque.validity_text(NOW).startswith("brak daty")


def test_expired_token_stops_before_any_request() -> None:
    expired = make_jwt(exp=int((NOW - timedelta(days=1)).timestamp()))
    with pytest.raises(ConfigError, match="wygasł"):
        load_settings(env_file=None, environ={"CEIDG_TOKEN": expired}, use_keyring=False, now=NOW)


def test_expiry_warning_is_attached_to_settings() -> None:
    soon = make_jwt(exp=int((NOW + timedelta(days=2)).timestamp()))
    s = load_settings(env_file=None, environ={"CEIDG_TOKEN": soon}, use_keyring=False, now=NOW)
    assert any("wygasa" in w for w in s.warnings)


def test_mask_tokens_and_safe_filename() -> None:
    token = make_jwt(iat=1)
    assert token not in mask_tokens(f"Authorization: Bearer {token} koniec")
    assert "<token>" in mask_tokens(token)
    assert safe_filename("woj: podlaskie / 2014..2015 ?*") == "woj_podlaskie_2014..2015.xlsx"
    assert safe_filename("../../etc/passwd", ".csv") == "etc_passwd.csv"
    assert len(safe_filename("x" * 500)) == 105


def test_prod_from_env_file_still_needs_consent(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CEIDG_TOKEN=t\nCEIDG_ENV=prod\n", encoding="utf-8")
    with pytest.raises(ProdWithoutConsentError):
        load_settings(env_file=env_file, environ={}, use_keyring=False, now=NOW)
    ok = load_settings(env_file=env_file, environ={}, use_keyring=False, prod_consent=True, now=NOW)
    assert ok.environment == "prod" and ok.store_path.name == "store-prod.sqlite"


# --- drugie poświadczenie: klucz API asystenta (ADR-0011, decyzja 7) ---------------------

KLUCZ = "sk-ant-api03-" + "Aa0_-" * 8


def test_the_assistant_key_is_read_from_the_environment(tmp_path: Path) -> None:
    settings = load_settings(
        env_file=None,
        environ={"CEIDG_TOKEN": "tok", "ANTHROPIC_API_KEY": KLUCZ},
        data_dir=tmp_path,
        use_keyring=False,
    )

    assert settings.anthropic_key == KLUCZ
    assert settings.anthropic_key_source == "env"
    assert settings.assistant_key_fp and settings.assistant_key_fp != KLUCZ


def test_a_missing_assistant_key_is_a_normal_state_not_an_error(tmp_path: Path) -> None:
    """To jest **cała różnica** wobec tokenu CEIDG, którego brak zatrzymuje program.

    Bez klucza asystent jest wyłączony, a kreator i CLI prowadzą do pliku tak samo — instrukcja
    wymaga, żeby były dostępne bez asystenta.
    """
    settings = load_settings(
        env_file=None, environ={"CEIDG_TOKEN": "tok"}, data_dir=tmp_path, use_keyring=False
    )

    assert settings.anthropic_key is None
    assert settings.anthropic_key_source is None
    assert settings.assistant_key_fp is None


def test_the_assistant_key_never_reaches_repr_or_the_screen(tmp_path: Path) -> None:
    """Sekret jest środkiem płatniczym: nie ma go w `repr`, a maskowanie zna jego kształt."""
    from ceidg_tool.config import mask_tokens
    from ceidg_tool.ui.texts import token_summary

    settings = load_settings(
        env_file=None,
        environ={"CEIDG_TOKEN": "tok", "ANTHROPIC_API_KEY": KLUCZ},
        data_dir=tmp_path,
        use_keyring=False,
    )

    assert KLUCZ not in repr(settings)
    assert KLUCZ not in mask_tokens(f"błąd z kluczem {KLUCZ}")
    podsumowanie = token_summary(settings, now=datetime(2026, 9, 7, tzinfo=UTC))
    odcisk = settings.assistant_key_fp
    assert KLUCZ not in podsumowanie
    assert odcisk is not None and odcisk in podsumowanie


def test_the_keyring_wins_over_the_environment_for_the_assistant_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kolejność jak przy tokenie: magazyn haseł przed zmienną, zmienna przed `.env`."""
    import ceidg_tool.config as config

    z_magazynu = "sk-ant-api03-" + "Zz9__" * 8
    monkeypatch.setattr(
        config,
        "read_token_from_keyring",
        lambda username=config.KEYRING_USERNAME: (
            z_magazynu if username == config.KEYRING_ASSISTANT_USERNAME else None
        ),
    )

    settings = load_settings(
        env_file=None,
        environ={"CEIDG_TOKEN": "tok", "ANTHROPIC_API_KEY": KLUCZ},
        data_dir=tmp_path,
        use_keyring=True,
    )

    assert settings.anthropic_key == z_magazynu
    assert settings.anthropic_key_source == "keyring"


def test_the_sdk_credential_chain_is_never_consulted(tmp_path: Path) -> None:
    """`ANTHROPIC_AUTH_TOKEN` i profil `ant auth login` to **nie** są nasze poświadczenia.

    Gdyby narzędzie po nie sięgało, wydawałoby klucz, którego nikt mu nie dał, a `sprawdz-token`
    nie umiałby o nim opowiedzieć. Regułę 12 to zabezpiecza po stronie SDK; ten test pilnuje,
    że nasza własna resolucja też ich nie czyta.
    """
    settings = load_settings(
        env_file=None,
        environ={"CEIDG_TOKEN": "tok", "ANTHROPIC_AUTH_TOKEN": KLUCZ},
        data_dir=tmp_path,
        use_keyring=False,
    )

    assert settings.anthropic_key is None
