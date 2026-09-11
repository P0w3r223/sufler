"""Dziennik, ładunki i odtwarzanie, które **potrafi odmówić**.

Odtwarzalność, której nie da się obalić, jest deklaracją, a nie własnością. Dlatego połowa tego
pliku sprawdza sytuacje, w których odtworzenie ma powiedzieć „nie": wyczyszczona retencja,
zmieniony katalog reguł, podmieniony ładunek. Test, który tylko pokazuje zgodność, nie odróżnia
narzędzia odtwarzającego od narzędzia, które zawsze mówi „tak".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from krs_tool.__main__ import main
from krs_tool.cli import app
from krs_tool.dziennik.ladunki import Ladunki
from krs_tool.dziennik.odtworzenie import odtworz
from krs_tool.dziennik.skroty import skrot_tekstu
from krs_tool.dziennik.zapis import Dziennik, wpis_z_oceny
from krs_tool.errors import ConfigError, NieodtwarzalneZZachowanychError, OdpisNieczytelnyError
from krs_tool.odpis.czytanie import wczytaj_odpis
from krs_tool.odpis.zrodlo import OdpisZPliku
from krs_tool.signals.katalog import Regula, wczytaj_katalog
from krs_tool.signals.ocena import ocen_odpis
from tests.budowniczy import OKRES_KROPKOWY, wzmianka, zbuduj_odpis

CZAS = "2026-09-11T09:00:00Z"
SPRAWOZDANIE = (wzmianka("10.05.2024", OKRES_KROPKOWY),)


def _tresc(**kwargs: Any) -> str:
    return json.dumps(zbuduj_odpis(sprawozdania=SPRAWOZDANIE, **kwargs), ensure_ascii=False)


def _zapisz(katalog: Path, tresc: str, reguly: tuple[Regula, ...] | None = None) -> str:
    """Ocena zapisana tak, jak robi to polecenie `raport`: najpierw ładunek, potem linia."""
    uzyte = reguly if reguly is not None else wczytaj_katalog()
    ocena = ocen_odpis(wczytaj_odpis(json.loads(tresc)), uzyte)
    wpis = wpis_z_oceny(ocena, CZAS, skrot_tekstu(tresc), uzyte)
    Ladunki(katalog).zapisz(wpis.ocena_id, tresc)
    Dziennik(katalog).dopisz(wpis)
    return wpis.ocena_id


def test_jedna_ocena_to_jedna_linia_czytelna_w_edytorze(tmp_path: Path) -> None:
    """Format jest po to, żeby operator otworzył plik i zobaczył, co i o kim narzędzie mówiło."""
    _zapisz(tmp_path, _tresc())

    linie = (tmp_path / "dziennik.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(linie) == 1
    wpis = json.loads(linie[0])
    assert wpis["numer"] == "0000123456"
    assert wpis["werdykty"]["dzial4_niepusty"] == "wykluczony"


def test_dziennik_dopisuje_zamiast_nadpisywac(tmp_path: Path) -> None:
    """Ta sama ocena zapisana dwa razy zostawia dwa ślady. Tak wygląda plik, który tylko rośnie."""
    _zapisz(tmp_path, _tresc())
    _zapisz(tmp_path, _tresc())

    assert len(Dziennik(tmp_path).wpisy()) == 2


def test_dwie_oceny_tego_samego_odpisu_maja_ten_sam_identyfikator(tmp_path: Path) -> None:
    pierwsza = _zapisz(tmp_path, _tresc())
    druga = _zapisz(tmp_path, _tresc())

    assert pierwsza == druga


def test_odtworzenie_zgodnej_oceny_mowi_identyczny(tmp_path: Path) -> None:
    ocena_id = _zapisz(tmp_path, _tresc())

    wynik = odtworz(tmp_path, ocena_id, wczytaj_katalog())

    assert wynik.identyczne is True
    assert wynik.rozniace_sie_reguly == ()
    assert wynik.katalog_sie_zmienil is False


def test_odtworzenie_po_wyczyszczeniu_retencji_odmawia(tmp_path: Path) -> None:
    """Brak ładunku nie jest usterką — jest skutkiem decyzji o retencji i tak się przedstawia."""
    ocena_id = _zapisz(tmp_path, _tresc())

    usuniete = Ladunki(tmp_path).wyczysc()

    assert usuniete == 1
    with pytest.raises(NieodtwarzalneZZachowanychError) as blad:
        odtworz(tmp_path, ocena_id, wczytaj_katalog())
    assert "z retencji" in str(blad.value)


def test_czyszczenie_ladunkow_nie_rusza_dziennika(tmp_path: Path) -> None:
    """Pamięć o tym, że ocena się odbyła, przeżywa usunięcie materiału, na którym powstała."""
    _zapisz(tmp_path, _tresc())

    Ladunki(tmp_path).wyczysc()

    assert len(Dziennik(tmp_path).wpisy()) == 1


def test_zmiana_katalogu_regul_jest_widoczna_w_odtworzeniu(tmp_path: Path) -> None:
    """Najczęstsza przyczyna różnicy i najłatwiejsza do przeoczenia, więc nazwana wprost."""
    pelny = wczytaj_katalog()
    ocena_id = _zapisz(tmp_path, _tresc(), pelny)

    wynik = odtworz(tmp_path, ocena_id, tuple(r for r in pelny if r.kod != "dzial5_kurator"))

    assert wynik.identyczne is False
    assert wynik.katalog_sie_zmienil is True
    assert "dzial5_kurator" in wynik.rozniace_sie_reguly


def test_podmieniony_ladunek_jest_zglaszany(tmp_path: Path) -> None:
    """Ładunek to plik na dysku operatora. Skrót w dzienniku mówi, czy to wciąż ten sam plik."""
    ocena_id = _zapisz(tmp_path, _tresc())
    Ladunki(tmp_path).zapisz(ocena_id, _tresc(nazwa="INNA SPOLKA"))

    wynik = odtworz(tmp_path, ocena_id, wczytaj_katalog())

    assert wynik.material_sie_zmienil is True


def test_nieznany_identyfikator_konczy_sie_komunikatem_a_nie_pustka(tmp_path: Path) -> None:
    with pytest.raises(OdpisNieczytelnyError) as blad:
        odtworz(tmp_path, "brakujacy", wczytaj_katalog())

    assert "brakujacy" in str(blad.value)


def test_uszkodzona_linia_dziennika_nie_jest_zgadywana(tmp_path: Path) -> None:
    (tmp_path / "dziennik.jsonl").write_text("{to nie jest json}\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        Dziennik(tmp_path).wpisy()


def test_linia_ze_starszej_wersji_narzedzia_konczy_sie_odmowa(tmp_path: Path) -> None:
    """Wpis bez pola nie jest wpisem z pustym polem — i nie wolno go tak potraktować."""
    _zapisz(tmp_path, _tresc())
    linia = json.loads((tmp_path / "dziennik.jsonl").read_text(encoding="utf-8"))
    del linia["werdykty"]
    (tmp_path / "dziennik.jsonl").write_text(
        json.dumps(linia, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError) as blad:
        Dziennik(tmp_path).wpisy()

    assert "werdykty" in str(blad.value)


def test_identyfikator_wynika_z_tresci_a_nie_z_chwili_zapisu(tmp_path: Path) -> None:
    """Bez tego odtworzenie wymagałoby pamiętania identyfikatora, którego nikt nie pamięta."""
    tresc = _tresc()

    ocena_id = _zapisz(tmp_path, tresc)

    assert skrot_tekstu(tresc).startswith(ocena_id)


# --------------------------------------------------------------------------------------
# Polecenia
# --------------------------------------------------------------------------------------


def _plik_odpisu(tmp_path: Path) -> Path:
    plik = tmp_path / "odpis.json"
    plik.write_text(_tresc(), encoding="utf-8")
    return plik


def test_raport_zapisuje_ocene_i_mowi_gdzie(tmp_path: Path) -> None:
    """Operator ma wiedzieć, że na jego dysku została kopia odpisu — zanim dowie się skądinąd."""
    magazyn = tmp_path / "magazyn"

    wynik = CliRunner().invoke(
        app, ["raport", "--plik", str(_plik_odpisu(tmp_path)), "--magazyn", str(magazyn)]
    )

    assert wynik.exit_code == 0
    assert "zapisano w dzienniku" in wynik.output
    assert len(Dziennik(magazyn).wpisy()) == 1
    assert len(Ladunki(magazyn).lista()) == 1


def test_raport_bez_dziennika_nie_zostawia_niczego(tmp_path: Path) -> None:
    magazyn = tmp_path / "magazyn"

    wynik = CliRunner().invoke(
        app,
        [
            "raport",
            "--plik",
            str(_plik_odpisu(tmp_path)),
            "--magazyn",
            str(magazyn),
            "--bez-dziennika",
        ],
    )

    assert wynik.exit_code == 0
    assert not magazyn.exists()


def test_czyszczenie_bez_potwierdzenia_tylko_pokazuje(tmp_path: Path) -> None:
    """Polecenie kasujące, które kasuje od razu, jest polecenie kasującym przez pomyłkę."""
    _zapisz(tmp_path, _tresc())

    wynik = CliRunner().invoke(app, ["wyczysc-ladunki", "--magazyn", str(tmp_path)])

    assert wynik.exit_code == 0
    assert "Nic jeszcze nie zostało usunięte" in wynik.output
    assert len(Ladunki(tmp_path).lista()) == 1


def test_czyszczenie_z_potwierdzeniem_usuwa_ladunki(tmp_path: Path) -> None:
    _zapisz(tmp_path, _tresc())

    wynik = CliRunner().invoke(
        app, ["wyczysc-ladunki", "--potwierdzam", "--magazyn", str(tmp_path)]
    )

    assert wynik.exit_code == 0
    assert Ladunki(tmp_path).lista() == ()
    assert len(Dziennik(tmp_path).wpisy()) == 1


def test_odtworzenie_z_polecenia_mowi_identyczny(tmp_path: Path) -> None:
    ocena_id = _zapisz(tmp_path, _tresc())

    wynik = CliRunner().invoke(app, ["odtworz", "--ocena-id", ocena_id, "--magazyn", str(tmp_path)])

    assert wynik.exit_code == 0
    assert "identyczny" in wynik.output


def test_odmowa_odtworzenia_konczy_sie_komunikatem_i_kodem_wyjscia(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ślad stosu sugerowałby usterkę narzędzia tam, gdzie zaszedł stan przewidziany."""
    ocena_id = _zapisz(tmp_path, _tresc())
    Ladunki(tmp_path).wyczysc()
    monkeypatch.setattr(
        "sys.argv", ["krs-tool", "odtworz", "--ocena-id", ocena_id, "--magazyn", str(tmp_path)]
    )

    with pytest.raises(SystemExit) as wyjscie:
        main()

    assert wyjscie.value.code == 1
    wyjscie_tekst = capsys.readouterr().out
    assert "nie udało się" in wyjscie_tekst
    assert "Traceback" not in wyjscie_tekst


def test_odtworzenie_dziala_dla_odpisu_bez_numeru_w_naglowku(tmp_path: Path) -> None:
    """Odpis bez `numerKRS` to cały powód, dla którego polecenia mają `--krs`.

    Zanim odtworzenie zaczęło brać numer z wpisu, raport wypisywał polecenie, które nie miało
    prawa zadziałać, a odmowa mówiła operatorowi, że nie podał numeru — podczas gdy numer stał
    w tej samej linii dziennika, którą odtworzenie właśnie przeczytało.
    """
    tresc = json.dumps(zbuduj_odpis(sprawozdania=SPRAWOZDANIE, bez_numeru=True), ensure_ascii=False)
    plik = tmp_path / "bez_numeru.json"
    plik.write_text(tresc, encoding="utf-8")
    magazyn = tmp_path / "magazyn"
    zapis = CliRunner().invoke(
        app,
        ["raport", "--plik", str(plik), "--krs", "0000123456", "--magazyn", str(magazyn)],
    )
    assert zapis.exit_code == 0

    (wpis,) = Dziennik(magazyn).wpisy()
    wynik = odtworz(magazyn, wpis.ocena_id, wczytaj_katalog())

    assert wynik.identyczne is True


def test_ladunek_to_te_same_bajty_ktore_ocenialismy(tmp_path: Path) -> None:
    """Skrót materiału ma opisywać plik, z którego powstały werdykty, a nie ten z chwili zapisu.

    Gdyby dziennik czytał plik drugi raz, podmiana pliku między oceną a zapisem dałaby przy
    odtwarzaniu „materiał się nie zmienił" o materiale, którego nikt nigdy nie oceniał — czyli
    jedyne twierdzenie, jakie dziennik stawia o swoim ładunku, byłoby fałszywe.
    """
    plik = tmp_path / "odpis.json"
    oryginal = _tresc()
    plik.write_text(oryginal, encoding="utf-8")
    zrodlo = OdpisZPliku(plik)
    zrodlo.pobierz()

    plik.write_text(_tresc(nazwa="PODMIENIONA"), encoding="utf-8")

    assert zrodlo.tresc == oryginal


def test_identyfikator_z_recznie_zmienionej_linii_nie_wskaze_poza_magazyn(tmp_path: Path) -> None:
    """Identyfikator wskazuje plik ładunku, więc jego kształt sprawdzamy przy wczytaniu linii."""
    _zapisz(tmp_path, _tresc())
    linia = json.loads((tmp_path / "dziennik.jsonl").read_text(encoding="utf-8"))
    linia["ocena_id"] = "../../../etc/x"
    (tmp_path / "dziennik.jsonl").write_text(
        json.dumps(linia, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    with pytest.raises(ConfigError) as blad:
        Dziennik(tmp_path).wpisy()

    assert "kształtu skrótu" in str(blad.value)
