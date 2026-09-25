"""Sondy czytnika katalogu procedur ``/mnt/skills`` (ADR 0005; ADR 0009 paczki).

Najważniejsza jest sonda na UCIĘCIE listy. Procedura leżąca na dysku, poprawna i nieosiągalna,
daje objaw nie do odróżnienia od „model jej nie użył" — to udokumentowana awaria w dwóch
niezależnych ekosystemach agentowych. Czytnik, który ucina po cichu, i czytnik, który ostrzega,
zwracają identyczną listę; różnią się wyłącznie tym, co widać w logu.
"""

from __future__ import annotations

import logging

import pytest

from sufler.adapters.outbound.filesystem_skills import read_skill_catalog


@pytest.fixture()
def skills(tmp_path):
    def make(name: str, body: str) -> None:
        directory = tmp_path / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "SKILL.md").write_text(body, encoding="utf-8")

    return make


def test_brak_sciezki_to_pusta_lista(tmp_path):
    """Zdolność włącza sama obecność katalogu — bez niej zachowanie jest dokładnie dawne."""
    assert read_skill_catalog(None, limit=20) == ()


def test_nieistniejacy_katalog_ostrzega_zamiast_milczec(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        assert read_skill_catalog(tmp_path / "nie-ma", limit=20) == ()
    assert "nie istnieje" in caplog.text


def test_nazwa_z_katalogu_opis_z_pierwszej_linii_tresci(tmp_path, skills):
    skills("brief-projektu", "# Brief projektu\n\nUżyj, gdy ktoś prosi o jednostronicówkę.\n")

    assert read_skill_catalog(tmp_path, limit=20) == (
        ("brief-projektu", "Użyj, gdy ktoś prosi o jednostronicówkę."),
    )


def test_naglowki_markdown_sa_pomijane_przy_szukaniu_opisu(tmp_path, skills):
    skills("x", "# Tytuł\n## Podtytuł\n\nWłaściwy opis.\n")

    assert read_skill_catalog(tmp_path, limit=20)[0][1] == "Właściwy opis."


def test_opis_dyrektywny_przechodzi_bez_zmian(tmp_path, skills):
    """WYJĄTEK od reguł redakcyjnych promptu (ADR 0056) i jest on zamierzony.

    Pomiar aktywacji procedur pokazuje zależność odwrotną niż w prompcie systemowym: opis
    dyrektywny, z jawnym wyzwalaczem, wybierany jest wielokrotnie częściej niż rzeczowy.
    Gdyby bramka redakcyjna objęła ten artefakt, egzekwowałaby regułę wbrew dowodowi.
    """
    skills("audyt", "# Audyt\n\nZAWSZE uruchom, gdy ktoś pyta o zgodność konfiguracji.\n")

    assert read_skill_catalog(tmp_path, limit=20)[0][1].startswith("ZAWSZE uruchom")


def test_katalog_bez_pliku_SKILL_jest_pomijany_z_ostrzezeniem(tmp_path, caplog):
    (tmp_path / "niedokonczona").mkdir()

    with caplog.at_level(logging.WARNING):
        assert read_skill_catalog(tmp_path, limit=20) == ()
    assert "niedokonczona" in caplog.text


def test_pusty_plik_SKILL_nie_daje_procedury_bez_opisu(tmp_path, skills):
    skills("pusta", "# Sam tytuł\n")

    assert read_skill_catalog(tmp_path, limit=20) == ()


def test_katalogi_ukryte_sa_pomijane(tmp_path, skills):
    skills(".git", "podstępny opis")
    skills("prawdziwa", "opis")

    assert [name for name, _ in read_skill_catalog(tmp_path, limit=20)] == ["prawdziwa"]


def test_kolejnosc_jest_deterministyczna(tmp_path, skills):
    for name in ("c", "a", "b"):
        skills(name, f"opis {name}")

    assert [n for n, _ in read_skill_catalog(tmp_path, limit=20)] == ["a", "b", "c"]


def test_UCIECIE_listy_jest_glosne_i_wymienia_zgubione(tmp_path, skills, caplog):
    """Sedno pliku: lista ucięta i lista kompletna wyglądają dla modelu identycznie."""
    for i in range(5):
        skills(f"proc-{i}", f"opis {i}")

    with caplog.at_level(logging.WARNING):
        result = read_skill_catalog(tmp_path, limit=2)

    assert [n for n, _ in result] == ["proc-0", "proc-1"]
    assert "proc-2, proc-3, proc-4" in caplog.text, "log ma wymienić, czego model nie zobaczy"


def test_znaki_niewidoczne_sa_usuwane_z_opisu(tmp_path, skills):
    """Ukrywanie instrukcji znakami zero-width jest znaną techniką, a `cat` nie sanityzuje."""
    skills("podstep", "# T\n\nZwykły opis​‮ ukryta instrukcja\n")

    (_, description) = read_skill_catalog(tmp_path, limit=20)[0]

    assert "​" not in description
    assert "‮" not in description


def test_linia_zlozona_wylacznie_ze_znakow_niewidocznych_nie_jest_opisem(tmp_path, skills):
    skills("x", "# T\n\n​​\n\nWłaściwy opis.\n")

    assert read_skill_catalog(tmp_path, limit=20)[0][1] == "Właściwy opis."


def test_dlugi_opis_jest_przycinany(tmp_path, skills):
    skills("gadatliwa", "# T\n\n" + "x" * 500)

    assert len(read_skill_catalog(tmp_path, limit=20)[0][1]) == 200


def test_plik_binarny_nie_wywraca_odczytu(tmp_path):
    directory = tmp_path / "zepsuta"
    directory.mkdir()
    (directory / "SKILL.md").write_bytes(b"\xff\xfe\x00\x01")

    assert read_skill_catalog(tmp_path, limit=20) == ()
