from __future__ import annotations

from pathlib import Path

import pytest

from ceidg_tool.apiprofile import ApiProfile, load_profile
from ceidg_tool.errors import ConfigError


@pytest.mark.parametrize("environment", ["test", "prod"])
def test_packaged_profiles_load_and_reflect_probe_findings(environment: str) -> None:
    profile = load_profile(environment)
    assert profile.base_url.endswith("/api/ceidg/v3")
    assert profile.page_start == 0
    assert profile.max_limit_firmy == 25
    assert profile.ids_batch_size >= 1
    assert profile.pkd_format == "compact"
    assert 204 in profile.empty_result_statuses
    # 3,75 s, a nie 3,6 s z dokumentacji: oba okna wymuszają tyle same, a niższa wartość
    # pozwalała limiterowi robić serie (patrz `test_ratelimit.py`, sekcja o seriach)
    assert profile.rate.min_spacing_s == pytest.approx(3.75)
    assert profile.rate.cooldown_s >= 180


def test_profile_hash_changes_with_dialect() -> None:
    a = ApiProfile(base_url="https://test-dane.biznes.gov.pl/api/ceidg/v3")
    b = a.model_copy(update={"ids_batch_size": 25})
    assert a.profile_hash() != b.profile_hash()
    assert a.profile_hash() == ApiProfile.model_validate(a.model_dump()).profile_hash()


def test_profile_rejects_http_and_unknown_fields(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        ApiProfile(base_url="http://dane.biznes.gov.pl/api/ceidg/v3")
    bad = tmp_path / "bad.yaml"
    bad.write_text("base_url: https://x.test/api\nnieznane_pole: 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="Niepoprawny profil"):
        load_profile("test", bad)
    with pytest.raises(ConfigError, match="Brak wbudowanego profilu"):
        load_profile("staging")


def test_user_profile_file_overrides_packaged(tmp_path: Path) -> None:
    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "base_url: https://test-dane.biznes.gov.pl/api/ceidg/v3\nids_batch_size: 25\n",
        encoding="utf-8",
    )
    assert load_profile("test", custom).ids_batch_size == 25


def test_pacing_changes_do_not_strand_runs_in_progress() -> None:
    """Skrót profilu pilnuje dialektu, nie tempa.

    Dopóki `rate` wchodziło do skrótu, każda korekta limitera unieważniała wznowienie —
    a tempo koryguje się po 429, czyli dokładnie wtedy, gdy jest co wznawiać."""
    profile = load_profile("prod")
    wolniej = profile.model_copy(
        update={"rate": profile.rate.model_copy(update={"min_spacing_s": 9.0})}
    )
    assert wolniej.rate.min_spacing_s != profile.rate.min_spacing_s
    assert wolniej.profile_hash() == profile.profile_hash()
    assert wolniej.canonical_json() != profile.canonical_json()  # pełny profil widzi różnicę


@pytest.mark.parametrize(
    ("pole", "wartosc"),
    [
        ("page_start", 1),
        ("max_limit_firmy", 10),
        ("list_root_key", "podmioty"),
        ("empty_result_statuses", (200,)),
        ("pkd_format", "dotted"),
        ("paging_mode", "numeric"),
    ],
)
def test_every_dialect_field_still_changes_the_hash(pole: str, wartosc: object) -> None:
    """Zawężenie skrótu nie może przypadkiem wypuścić pola, które zmienia odczyt odpowiedzi."""
    profile = load_profile("prod")
    assert profile.model_copy(update={pole: wartosc}).profile_hash() != profile.profile_hash()
