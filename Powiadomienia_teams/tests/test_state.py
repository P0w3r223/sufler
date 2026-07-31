import os
from pathlib import Path

import pytest

from powiadomienia_teams.state import (
    AWAITING_REPLY,
    PendingReminder,
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
