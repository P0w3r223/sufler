from pathlib import Path

import pytest

from powiadomienia_teams.roster import load_roster


def test_load_roster_parses_entries(tmp_path: Path):
    p = tmp_path / "roster.yaml"
    p.write_text(
        '- user_id: "u1"\n'
        '  display_name: "Ala"\n'
        '  email: "ala@example.com"\n'
        '  roles: ["member", "owner"]\n'
        '- user_id: "u2"\n'
        '  display_name: "Bok"\n',
        encoding="utf-8",
    )
    members = load_roster(p)
    assert len(members) == 2
    assert members[0].user_id == "u1"
    assert members[0].email == "ala@example.com"
    assert members[0].roles == ("member", "owner")
    assert members[1].email is None
    assert members[1].roles == ()


def test_empty_file_gives_empty_roster(tmp_path: Path):
    p = tmp_path / "roster.yaml"
    p.write_text("", encoding="utf-8")
    assert load_roster(p) == ()


def test_missing_required_field_raises(tmp_path: Path):
    p = tmp_path / "roster.yaml"
    p.write_text('- display_name: "brak id"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_roster(p)


def test_non_list_raises(tmp_path: Path):
    p = tmp_path / "roster.yaml"
    p.write_text("user_id: u1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_roster(p)


def test_scalar_roles_raises(tmp_path: Path):
    p = tmp_path / "roster.yaml"
    p.write_text('- user_id: "u1"\n  display_name: "Ala"\n  roles: member\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_roster(p)
