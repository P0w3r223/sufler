"""Testy katalogu tożsamości (``YamlIdentityDirectory``, ADR 0042/0054) — mapa AAD → Jira.

``GraphIdentityDirectory``/``fetch_team_members`` (wariant z weryfikacją członkostwa przez Graph)
i ``resolve_by_git_email``/``git_email`` zniknęły razem z modułem kart czasu, który był ich
jedynym konsumentem (ADR 0055) — dziś jest tylko wariant plikowy, współdzielony przez autoryzację
notatki ze spotkania i "moje zadania" Jira.

Od ADR 0070 mapa przyjmuje wpis „tylko Teams" — bez ``jira_user``. Wymagane zostaje wyłącznie
``aad_user_id``; testy niżej trzymają OBIE strony tego progu, bo rozluźnienie w złą stronę daje
wpis, który nie autoryzuje niczego, a zaostrzenie z powrotem odcina człowieka od bazy wiedzy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sufler.adapters.outbound.graph_identity_directory import YamlIdentityDirectory

_YAML = """
EMP-042:
  aad_user_id: aad-mikolaj
  jira_user: mikolaj@example.org
  display_name: Mikołaj Anonimowicz
EMP-017:
  aad_user_id: aad-piotr
  jira_user: piotr@example.com
"""


def _identities(tmp_path: Path, payload: str = _YAML) -> Path:
    path = tmp_path / "identities.yaml"
    path.write_text(payload, encoding="utf-8")
    return path


def test_resolve_by_aad_user_id_maps_to_person(tmp_path: Path) -> None:
    directory = YamlIdentityDirectory(_identities(tmp_path))
    person = directory.resolve_by_aad_user_id("aad-mikolaj")
    assert person is not None
    assert person.source_id == "EMP-042"
    assert person.jira_user == "mikolaj@example.org"
    assert person.display_name == "Mikołaj Anonimowicz"


def test_resolve_by_aad_user_id_is_fail_closed_for_unknown_account(tmp_path: Path) -> None:
    """Nigdy dopasowanie po nazwisku — nieznane konto to zawsze ``None``."""
    assert YamlIdentityDirectory(_identities(tmp_path)).resolve_by_aad_user_id("aad-obcy") is None


def test_missing_identity_file_fails_at_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="brak pliku mapy"):
        YamlIdentityDirectory(tmp_path / "nie-ma.yaml")


def test_entry_without_jira_user_is_a_teams_only_member(tmp_path: Path) -> None:
    """Wpis bez ``jira_user`` to osoba BEZ konta Jira — pełny członek pionu (ADR 0070 §1).

    Test ODWRÓCONY 2026-09-04, nie dopisany: do tego dnia nazywał się
    ``test_entry_without_jira_user_fails_at_startup`` i zamrażał regułę „oba pola wymagane".
    Ta reguła trzymała bramkę odczytu bazy wiedzy (ADR 0062) wyłączoną od kiedy powstała, bo
    w pionie jest osoba bez konta Jira, a bramka jest fail-closed — jej włączenie odcięłoby ją
    od notatek. Zostawiam ten akapit, bo inaczej po latach wygląda to na rozluźnienie walidacji.
    """
    directory = YamlIdentityDirectory(_identities(tmp_path, "EMP-1:\n  aad_user_id: a\n"))

    person = directory.resolve_by_aad_user_id("a")
    assert person is not None
    assert person.source_id == "EMP-1"
    assert person.jira_user == ""


def test_entry_without_aad_user_id_fails_at_startup(tmp_path: Path) -> None:
    """``aad_user_id`` zostaje WYMAGANE — i to jest druga połowa ADR 0070 §1.

    Pomyłka do popełnienia brzmi: „skoro jedno pole zrobiliśmy opcjonalnym, to drugie też".
    Wpis bez ``aad_user_id`` nie autoryzuje NICZEGO (``_by_aad`` po prostu go pomija), więc
    zamiast twardego błędu startu dostalibyśmy cichy niebyt: osoba jest w mapie, a każda bramka
    ją odrzuca. To dokładnie ta klasa awarii, przed którą fail-fast tego modułu ma chronić.
    """
    with pytest.raises(ValueError, match="aad_user_id"):
        YamlIdentityDirectory(_identities(tmp_path, "EMP-1:\n  jira_user: a@example.com\n"))


def test_two_people_without_a_jira_account_both_load(tmp_path: Path) -> None:
    """Dwoje ludzi bez konta Jira to NIE jest konto współdzielone (ADR 0070 §2).

    Bez pominięcia pustych wartości w ``_reject_shared_identifiers`` DRUGA taka osoba kładłaby
    start błędem „jira_user='' występuje u dwóch osób" — zatrzymanie fail-closed spowodowane tym,
    że dwoje ludzi poprawnie nie ma niczego. Pion ma dziś jedną taką osobę; ten test broni dnia,
    w którym pojawi się druga, bo wtedy awaria dotknie CAŁEJ mapy, nie tylko jej wpisu.
    """
    bez_jiry = "EMP-1:\n  aad_user_id: aad-1\nEMP-2:\n  aad_user_id: aad-2\n"

    directory = YamlIdentityDirectory(_identities(tmp_path, bez_jiry))

    pierwsza = directory.resolve_by_aad_user_id("aad-1")
    druga = directory.resolve_by_aad_user_id("aad-2")
    assert pierwsza is not None and pierwsza.source_id == "EMP-1"
    assert druga is not None and druga.source_id == "EMP-2"


def test_two_people_sharing_a_jira_account_fail_at_startup(tmp_path: Path) -> None:
    """Skopiowany blok bez podmiany ``jira_user`` pokazałby czyjeś zadania komuś innemu."""
    duplikat = (
        "EMP-1:\n  aad_user_id: aad-1\n  jira_user: mikolaj@example.com\n"
        "EMP-2:\n  aad_user_id: aad-2\n  jira_user: mikolaj@example.com\n"
    )
    with pytest.raises(ValueError, match="jira_user"):
        YamlIdentityDirectory(_identities(tmp_path, duplikat))


def test_two_people_sharing_a_teams_account_fail_at_startup(tmp_path: Path) -> None:
    """Ten sam ``aad_user_id`` u dwóch osób = ktoś autoryzowałby się jako kolega."""
    duplikat = (
        "EMP-1:\n  aad_user_id: aad-mikolaj\n  jira_user: a@example.com\n"
        "EMP-2:\n  aad_user_id: aad-mikolaj\n  jira_user: b@example.com\n"
    )
    with pytest.raises(ValueError, match="aad_user_id"):
        YamlIdentityDirectory(_identities(tmp_path, duplikat))


# --- resolve_by_display_name (ADR 0059, "zadania członka" pionu) -----------------


def test_resolve_by_display_name_matches_known_person(tmp_path: Path) -> None:
    directory = YamlIdentityDirectory(_identities(tmp_path))
    person = directory.resolve_by_display_name("Mikołaj Anonimowicz")
    assert person is not None
    assert person.jira_user == "mikolaj@example.org"


def test_resolve_by_display_name_is_case_and_diacritics_insensitive(tmp_path: Path) -> None:
    directory = YamlIdentityDirectory(_identities(tmp_path))
    person = directory.resolve_by_display_name("mikolaj ANONIMOWICZ")
    assert person is not None
    assert person.source_id == "EMP-042"


def test_resolve_by_display_name_unknown_name_returns_none(tmp_path: Path) -> None:
    directory = YamlIdentityDirectory(_identities(tmp_path))
    assert directory.resolve_by_display_name("Ktoś Inny") is None


def test_resolve_by_display_name_skips_people_without_display_name(tmp_path: Path) -> None:
    """EMP-017 (piotr) w fixture nie ma ``display_name`` — nie wolno go dopasować po pustym polu."""
    directory = YamlIdentityDirectory(_identities(tmp_path))
    assert directory.resolve_by_display_name("") is None


def test_resolve_by_display_name_refuses_when_two_people_share_the_name(tmp_path: Path) -> None:
    """Reguła ADR 0059 („odmawiają przy niejednoznaczności") żyła dotąd wyłącznie w docstringu.

    Imiennicy w pionie to nie hipoteza. Katalog RÓŻNI się tu od duplikatów identyfikatorów wyżej:
    współdzielony ``jira_user`` kładzie start, a współdzielone NAZWISKO jest legalne — więc jedyną
    obroną jest odmowa przy zapytaniu. Zwrócenie „pierwszego z brzegu" pokazałoby zadania jednego
    Kowalskiego pod imieniem drugiego, i to bez śladu, że wybrano.
    """
    imiennicy = (
        "EMP-1:\n  aad_user_id: aad-1\n  jira_user: jan1@example.com\n"
        "  display_name: Jan Kowalski\n"
        "EMP-2:\n  aad_user_id: aad-2\n  jira_user: jan2@example.com\n"
        "  display_name: Jan Kowalski\n"
    )
    directory = YamlIdentityDirectory(_identities(tmp_path, imiennicy))

    assert directory.resolve_by_display_name("Jan Kowalski") is None


def test_resolve_by_display_name_accepts_a_surname_only_while_it_stays_unique(
    tmp_path: Path,
) -> None:
    """Ludzie piszą „zadania Anonimowicza", nie pełne imię i nazwisko — samo nazwisko ma trafić.

    Ale wolno mu trafić TYLKO póki jest jedno: dopisanie drugiego Anonimowicza zamienia to samo
    zapytanie w odmowę. Ta para asercji trzyma OBIE strony progu; sama pierwsza przechodziłaby też
    dla implementacji „bierz pierwszego pasującego".
    """
    directory = YamlIdentityDirectory(_identities(tmp_path))
    assert directory.resolve_by_display_name("Anonimowicz") is not None

    z_imiennikiem = YamlIdentityDirectory(
        _identities(
            tmp_path,
            _YAML + "EMP-099:\n  aad_user_id: aad-x\n  jira_user: x@e.com\n"
            "  display_name: Robert Anonimowicz\n",
        )
    )
    assert z_imiennikiem.resolve_by_display_name("Anonimowicz") is None
