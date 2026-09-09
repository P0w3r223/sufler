import os
from pathlib import Path

import pytest

from powiadomienia_teams.state import (
    AWAITING_REPLY,
    SELF_FILLED,
    PendingReminder,
    klucz_wpisu,
    load_state,
    save_state,
)


def test_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = {
        "u1": PendingReminder(
            member_id="u1",
            member_name="Ala",
            chat_id="chat-1",
            week_start="2026-07-20",
            status=AWAITING_REPLY,
            watermark="2026-07-19T18:00:00Z",
            resolved=[{"weekday": 0, "start": "08:00", "end": "16:00"}],
        )
    }
    save_state(path, state)
    loaded = load_state(path)
    assert loaded == state
    assert loaded["u1"].resolved[0]["start"] == "08:00"


def test_load_missing_returns_empty(tmp_path: Path):
    assert load_state(tmp_path / "nope.json") == {}


def test_load_ignores_unknown_fields(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text(
        '{"u1": {"member_id":"u1","member_name":"Ala","chat_id":"c",'
        '"week_start":"2026-07-20","status":"awaiting_reply","future_field":"x"}}',
        encoding="utf-8",
    )
    loaded = load_state(path)
    assert loaded["u1"].member_id == "u1"


def test_load_corrupt_file_returns_empty(tmp_path: Path):
    path = tmp_path / "state.json"
    path.write_text("{ not valid json", encoding="utf-8")
    assert load_state(path) == {}


def test_poprzedni_stan_zachowany_jako_kopia(tmp_path):
    """Nadpisanie stanu zostawia kopię — to ostatnia obrona przed podwójną wysyłką próśb."""
    sciezka = tmp_path / "state.json"
    pierwszy = {
        "u1": PendingReminder(
            member_id="u1",
            member_name="Ala",
            chat_id="c1",
            week_start="2026-07-20",
            status=AWAITING_REPLY,
        )
    }
    drugi = {
        "u2": PendingReminder(
            member_id="u2",
            member_name="Bo",
            chat_id="c2",
            week_start="2026-07-27",
            status=AWAITING_REPLY,
        )
    }

    save_state(sciezka, pierwszy)
    assert not sciezka.with_suffix(".json.bak").exists()  # nie ma jeszcze czego kopiować

    save_state(sciezka, drugi)
    kopia = sciezka.with_suffix(".json.bak")
    assert kopia.exists()
    assert "u1" in kopia.read_text(encoding="utf-8")  # kopia to POPRZEDNIA wersja
    assert "u2" in sciezka.read_text(encoding="utf-8")  # bieżąca to nowa


def test_zapis_nie_zostawia_pliku_tymczasowego(tmp_path):
    sciezka = tmp_path / "state.json"
    save_state(
        sciezka,
        {
            "u1": PendingReminder(
                member_id="u1",
                member_name="Ala",
                chat_id="c1",
                week_start="2026-07-20",
                status=AWAITING_REPLY,
            )
        },
    )
    assert not sciezka.with_suffix(".json.tmp").exists()


def test_zapis_nigdy_nie_usuwa_pliku_stanu(tmp_path, monkeypatch):
    """Utrata stanu = ponowne prośby do tych samych osób. Plik nie może zniknąć ANI NA CHWILĘ.

    Wcześniej `save_state` robiło dwa `os.replace` pod rząd (path→.bak, potem tmp→path), a między
    nimi plik stanu nie istniał. Proces ubity w tym oknie kasował stan całkowicie — mimo że dane
    leżały w kopii, bo `load_state` do niej nie zaglądało.
    """
    sciezka = tmp_path / "state.json"
    save_state(
        sciezka,
        {
            "u1": PendingReminder(
                member_id="u1",
                member_name="Ala",
                chat_id="c1",
                week_start="2026-07-20",
                status=AWAITING_REPLY,
            )
        },
    )

    prawdziwy = os.replace

    def replace_z_awaria(src, dst):
        prawdziwy(src, dst)
        # Ubijamy proces WYŁĄCZNIE po podmianie pliku GŁÓWNEGO. Od 0.2.16 `save_state` woła
        # `os.replace` dwa razy: najpierw dla kopii `.bak`, potem dla stanu. Podmiana globalna
        # trafiała więc w kopię i test badał inne okno, niż opisuje.
        if os.fspath(dst) == os.fspath(sciezka):
            raise KeyboardInterrupt("proces ubity tuz po podmianie")

    monkeypatch.setattr(os, "replace", replace_z_awaria)

    with pytest.raises(KeyboardInterrupt):
        save_state(
            sciezka,
            {
                "u2": PendingReminder(
                    member_id="u2",
                    member_name="Bo",
                    chat_id="c2",
                    week_start="2026-07-20",
                    status=AWAITING_REPLY,
                )
            },
        )
    monkeypatch.undo()

    assert sciezka.exists(), "plik stanu zniknął — okno bez stanu wróciło"
    assert list(load_state(sciezka)) == ["u2"]


def test_odtworzenie_stanu_z_kopii(tmp_path):
    """Uszkodzony stan ma być odtworzony z kopii, a nie zamieniony na pusty."""
    sciezka = tmp_path / "state.json"
    save_state(
        sciezka,
        {
            "u1": PendingReminder(
                member_id="u1",
                member_name="Ala",
                chat_id="c1",
                week_start="2026-07-20",
                status=AWAITING_REPLY,
            )
        },
    )
    save_state(
        sciezka,
        {
            "u2": PendingReminder(
                member_id="u2",
                member_name="Bo",
                chat_id="c2",
                week_start="2026-07-20",
                status=AWAITING_REPLY,
            )
        },
    )
    sciezka.write_text("{ucięty json", encoding="utf-8")  # awaria zasilania w trakcie zapisu

    assert list(load_state(sciezka)) == ["u1"]  # kopia sprzed jednego zapisu, nie pustka


def test_brak_stanu_i_kopii_daje_pusty(tmp_path):
    assert load_state(tmp_path / "nie-ma.json") == {}


def test_self_filled_status_round_trips(tmp_path: Path):
    """SELF_FILLED to nowy status terminalny — musi przetrwać zapis/odczyt jak każdy inny."""
    path = tmp_path / "state.json"
    state = {
        "u1": PendingReminder(
            member_id="u1",
            member_name="Ala",
            chat_id="c1",
            week_start="2026-07-20",
            status=SELF_FILLED,
        )
    }
    save_state(path, state)
    loaded = load_state(path)
    assert loaded["u1"].status == SELF_FILLED


def test_new_fields_default_to_empty(tmp_path: Path):
    """Pola wprowadzone dla self-fill/pamięci mają bezpieczne domyślne (stare pliki ich nie
    mają)."""
    pending = PendingReminder(
        member_id="u1",
        member_name="Ala",
        chat_id="c1",
        week_start="2026-07-20",
        status=AWAITING_REPLY,
    )
    assert pending.known_time_off_weekdays == []
    assert pending.employee_memory == []
    assert pending.memory_started_at == ""


def test_load_tolerates_null_values_for_new_fields(tmp_path: Path):
    """Plik stanu zapisany ręcznie/przez starszą wersję z `null` zamiast listy/napisu nie wywraca
    odczytu — `None` jest odsiewane, więc pole wraca do swojego defaultu zamiast zostać `None`
    (na którym `advance_memory`/`history_for_llm` rzuciłyby `TypeError`)."""
    path = tmp_path / "state.json"
    path.write_text(
        '{"u1": {"member_id":"u1","member_name":"Ala","chat_id":"c",'
        '"week_start":"2026-07-20","status":"awaiting_reply",'
        '"known_time_off_weekdays":null,"employee_memory":null,"memory_started_at":null}}',
        encoding="utf-8",
    )
    loaded = load_state(path)
    assert loaded["u1"].known_time_off_weekdays == []
    assert loaded["u1"].employee_memory == []
    assert loaded["u1"].memory_started_at == ""


def test_known_time_off_weekdays_and_memory_round_trip(tmp_path: Path):
    path = tmp_path / "state.json"
    state = {
        "u1": PendingReminder(
            member_id="u1",
            member_name="Ala",
            chat_id="c1",
            week_start="2026-07-20",
            status=AWAITING_REPLY,
            known_time_off_weekdays=[4, 5],
            employee_memory=["pon-pt 8-16", "a piątek zdalnie"],
            memory_started_at="2026-07-19T18:00:00Z",
        )
    }
    save_state(path, state)
    loaded = load_state(path)
    assert loaded["u1"].known_time_off_weekdays == [4, 5]
    assert loaded["u1"].employee_memory == ["pon-pt 8-16", "a piątek zdalnie"]
    assert loaded["u1"].memory_started_at == "2026-07-19T18:00:00Z"


def test_zly_typ_pola_wymaganego_pomija_wpis(tmp_path: Path):
    """`week_start` jako liczba kładł CAŁY obieg nasłuchu — `should_expire` woła
    `_poczatek_tygodnia` z list-comprehension kroku 2, czyli poza izolacją per-osoba,
    a `date.fromisoformat(liczba)` rzuca `TypeError`, którego tamten `except ValueError`
    nie łapie. Awaria deterministyczna, więc powtarzała się w każdym ticku przy bijącym pulsie."""
    path = tmp_path / "state.json"
    path.write_text(
        '{"zly": {"member_id":"u1","member_name":"Ala","chat_id":"c",'
        '"week_start":20260914,"status":"awaiting_reply"},'
        '"dobry": {"member_id":"u2","member_name":"Ola","chat_id":"c2",'
        '"week_start":"2026-09-14","status":"awaiting_reply"}}',
        encoding="utf-8",
    )
    loaded = load_state(path)
    assert "zly" not in loaded, "wpis z liczbą w week_start ma zostać pominięty, nie wczytany"
    assert loaded["dobry"].week_start == "2026-09-14", "zdrowe wpisy przechodzą nietknięte"


def test_zly_typ_pola_opcjonalnego_wraca_do_domyslnej(tmp_path: Path):
    """Jak przy `null`: pole opcjonalne o złym typie wraca do wartości domyślnej, a wpis żyje.
    `employee_memory` jako napis dawał `TypeError` w `advance_memory` — dokładnie tam, gdzie
    `_record_failure` miał już tylko odpuścić wiadomość, więc pending grzązł w pętli ponowień."""
    path = tmp_path / "state.json"
    path.write_text(
        '{"u1": {"member_id":"u1","member_name":"Ala","chat_id":"c",'
        '"week_start":"2026-09-14","status":"awaiting_reply",'
        '"employee_memory":"nie-lista","fail_count":"nie-liczba","awaiting_yes":"nie-bool"}}',
        encoding="utf-8",
    )
    wpis = load_state(path)["u1"]
    assert wpis.employee_memory == []
    assert wpis.fail_count == 0
    assert wpis.awaiting_yes is False


def test_klucz_wpisu_znajduje_po_tozsamosci_nie_znak_w_znak():
    """Idempotencja N15 opierała się na jedynym w projekcie porównaniu znak w znak. Graph nie
    obiecuje tej samej wielkości liter, a rozminięcie się z wpisem dawało DRUGĄ prośbę i drugi
    wpis na ten sam tydzień — oba mogące dojść do `create_shift`, który nie deduplikuje."""
    stan = {
        "ABC-DEF": PendingReminder(
            member_id="ABC-DEF",
            member_name="Ala",
            chat_id="c",
            week_start="2026-09-14",
            status=AWAITING_REPLY,
        )
    }
    assert klucz_wpisu(stan, "abc-def") == "ABC-DEF", "inna wielkość liter to ta sama osoba"
    assert klucz_wpisu(stan, "{ABC-DEF}") == "ABC-DEF", "klamry z portalu Azure też"
    assert klucz_wpisu(stan, "inny") == "inny", "brak wpisu → własny identyfikator wołającego"


def test_klucz_wpisu_zwraca_klucz_a_nie_znormalizowany_identyfikator():
    """`domain.tozsamosc` ZAKAZUJE normalizowania żywej strony: klucze w pliku to surowe id
    z Graph, a `member_id` idzie stąd wprost do POST-a tworzącego zmianę. Wolno zmienić sposób
    ODNAJDYWANIA wpisu, nie postać, w której leży."""
    stan = {
        "ABC-DEF": PendingReminder(
            member_id="ABC-DEF",
            member_name="Ala",
            chat_id="c",
            week_start="2026-09-14",
            status=AWAITING_REPLY,
        )
    }
    klucz = klucz_wpisu(stan, "abc-def")
    assert klucz in stan, "zwrócony klucz musi dać się użyć wprost na słowniku stanu"
    assert stan[klucz].member_id == "ABC-DEF", "postać zapisana zostaje nietknięta"
