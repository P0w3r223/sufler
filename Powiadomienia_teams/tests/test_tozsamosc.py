"""Porównywanie identyfikatorów AAD — `domain/tozsamosc.py`.

Moduł jest maleńki, ale stoi pod trzema porównaniami, których pomyłka kosztuje różne rzeczy:
bot biorący własną wiadomość za odpowiedź pracownika, cudza treść wpisana do grafiku i milczący
pilotaż. Dlatego ma własne sondy, a nie tylko pokrycie „przy okazji" przez `incoming_after`.
"""

from __future__ import annotations

from powiadomienia_teams.domain.tozsamosc import ten_sam, znormalizuj

_GUID = "AAAA1111-BBBB-2222-CCCC-333344445555"


def test_znormalizuj_sprowadza_do_postaci_porownywalnej():
    assert znormalizuj(_GUID) == _GUID.lower()
    assert znormalizuj(f"  {_GUID}  ") == _GUID.lower()
    assert znormalizuj("{" + _GUID + "}") == _GUID.lower()
    # Klamry ORAZ białe znaki naraz — portal Azure kopiuje właśnie tak.
    assert znormalizuj("  {" + _GUID + "}  ") == _GUID.lower()


def test_klamra_opakowujaca_bialy_znak_daje_PUSTY_wynik():
    """Regresja: `"{ }"`. Obcięcie białych znaków tylko PRZED klamrami zostawiało spację.

    Napis niepusty, który nie pasuje do nikogo, jest gorszy od pustego: `Settings.validate`
    widzi listę pilotażu jako niepustą i przepuszcza start, a filtr milczy. Wynik MUSI być pusty,
    żeby bramka konfiguracji miała co złapać.
    """
    for wpis in ("{ }", "{\t}", "  {   }  "):
        assert znormalizuj(wpis) == "", wpis


def test_znormalizuj_nie_zjada_srodka_identyfikatora():
    """Obcinamy WYŁĄCZNIE brzegi. Klamra w środku nie jest opakowaniem, tylko treścią."""
    assert znormalizuj("ab{cd}ef") == "ab{cd}ef"


def test_ten_sam_rozpoznaje_te_sama_osobe_mimo_zapisu():
    assert ten_sam(_GUID, _GUID.lower())
    assert ten_sam("{" + _GUID + "}", _GUID.upper())
    assert ten_sam(" ab-cd ", "AB-CD")


def test_ten_sam_odrzuca_rozne_osoby():
    assert not ten_sam("u1", "u2")
    assert not ten_sam(_GUID, _GUID.replace("1", "2"))


def test_pusty_identyfikator_nie_jest_tozsamoscia():
    """Najgroźniejszy przypadek brzegowy tego modułu, nie kosmetyka.

    `graph.client.get_me` rzuca przy pustym id, ale ta funkcja stoi też pod filtrem konta bota
    w `runtime.nudge` i pod filtrem nadawcy w `reminders.replies`. Gdyby pusty napis równał się
    czemukolwiek, filtr „to nie ja" uznałby KAŻDEGO za bota i wyciszył cały nasłuch — awaria
    cicha, bo pusty wynik wygląda dokładnie jak „nikt nie odpisał".
    """
    assert not ten_sam("", "u1")
    assert not ten_sam("u1", "")
    assert not ten_sam("", "")
    # To samo dla wartości, która pusta staje się dopiero po normalizacji.
    assert not ten_sam("{}", "u1")
    assert not ten_sam("   ", "u1")
