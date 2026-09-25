"""Testy stanu idempotencji digestu (``teams_digest/state.py``) — kto już dostał w tym tygodniu.

Ten plik trzyma JEDYNY dowód na to, że ktoś dostał cotygodniową wiadomość: jego uszkodzenie albo
utrata znaczy DUPLIKAT DM-a u każdego odbiorcy. Niezmienniki są te same co w ``github/state.py``
i ``teams_graph/state.py`` (R1) i sprawdzamy je tak samo: zapis ATOMOWY (temp + ``os.replace``,
więc ubicie w trakcie nie zostawia uciętego pliku), odczyt TOLERANCYJNY (uszkodzony plik → pusty
stan i ostrzeżenie, nie wywrócenie procesu), oraz ``prune`` przycinający stan do kilku ostatnich
tygodni bez zjadania tygodni, które nadrabianie musi jeszcze widzieć.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sufler.adapters.inbound.teams_digest import state

_W30 = "2026-W30"
_W31 = "2026-W31"


def test_load_missing_file_returns_empty_state(tmp_path: Path):
    assert state.load(tmp_path / "nie-ma.json") == {}


def test_save_then_load_round_trips_dict(tmp_path: Path):
    path = tmp_path / "digest_state.json"
    original = {
        state.key(_W30, "EMP-1"): "2026-07-20T08:00:12+02:00",
        state.key(_W30, "EMP-2"): "2026-07-20T08:00:15+02:00",
    }

    state.save(path, original)

    assert state.load(path) == original


def test_key_pairs_week_with_person_so_a_new_week_starts_clean():
    """Klucz to (tydzień, osoba): ta sama osoba w kolejnym tygodniu to INNY wpis.

    Gdyby kluczem było samo ``source_id``, pierwszy przebieg wyciszyłby odbiorcę na zawsze.
    """
    assert state.key(_W30, "EMP-1") != state.key(_W31, "EMP-1")
    assert state.key(_W30, "EMP-1").startswith(f"{_W30}:")


def test_save_creates_missing_parent_directory(tmp_path: Path):
    path = tmp_path / "podkatalog" / "glebiej" / "digest_state.json"

    state.save(path, {state.key(_W30, "EMP-1"): "t"})

    assert state.load(path) == {state.key(_W30, "EMP-1"): "t"}


def test_load_corrupted_json_returns_empty_and_warns(tmp_path: Path, caplog):
    path = tmp_path / "digest_state.json"
    path.write_text('{ "2026-W30:EMP-1": ', encoding="utf-8")  # ucięty przez stop w trakcie zapisu

    with caplog.at_level("WARNING"):
        result = state.load(path)

    assert result == {}
    assert any("nieczytelny" in rec.message for rec in caplog.records)


def test_load_non_dict_returns_empty(tmp_path: Path):
    path = tmp_path / "digest_state.json"
    path.write_text('["2026-W30:EMP-1"]', encoding="utf-8")

    assert state.load(path) == {}


def test_load_coerces_values_to_strings_so_callers_can_trust_the_shape(tmp_path: Path):
    """Ręcznie poprawiony plik nie może wpuścić do stanu wartości innego typu."""
    path = tmp_path / "digest_state.json"
    path.write_text('{"2026-W30:EMP-1": 17}', encoding="utf-8")

    assert state.load(path) == {"2026-W30:EMP-1": "17"}


def test_save_leaves_original_intact_if_replace_fails(tmp_path: Path, monkeypatch):
    """Awaria w chwili podmiany NIE może uszkodzić stanu — inaczej wszyscy dostają digest 2×."""
    path = tmp_path / "digest_state.json"
    state.save(path, {state.key(_W30, "EMP-1"): "stary"})

    def boom(src: str, dst: str) -> None:
        raise OSError("symulacja ubicia w trakcie zapisu")

    monkeypatch.setattr(state.os, "replace", boom)
    with pytest.raises(OSError):
        state.save(path, {state.key(_W30, "EMP-2"): "nowy"})

    assert json.loads(path.read_text(encoding="utf-8")) == {state.key(_W30, "EMP-1"): "stary"}


def test_save_leaves_no_tmp_file(tmp_path: Path):
    path = tmp_path / "digest_state.json"

    state.save(path, {state.key(_W30, "EMP-1"): "t"})

    assert not path.with_suffix(path.suffix + ".tmp").exists()


def test_prune_keeps_only_the_listed_weeks(tmp_path: Path):
    stan = {
        state.key("2026-W20", "EMP-1"): "stare",
        state.key(_W30, "EMP-1"): "swieze",
        state.key(_W31, "EMP-2"): "swieze",
    }

    assert state.prune(stan, keep_weeks=(_W30, _W31)) == {
        state.key(_W30, "EMP-1"): "swieze",
        state.key(_W31, "EMP-2"): "swieze",
    }


def test_prune_does_not_mutate_the_state_it_was_given():
    """Wołający zapisuje wynik dopiero po porównaniu — mutacja wejścia zjadłaby to porównanie."""
    stan = {state.key("2026-W20", "EMP-1"): "stare", state.key(_W30, "EMP-1"): "swieze"}

    state.prune(stan, keep_weeks=(_W30,))

    assert len(stan) == 2


def test_prune_with_no_kept_weeks_empties_the_state():
    assert state.prune({state.key(_W30, "EMP-1"): "t"}, keep_weeks=()) == {}


def test_prune_matches_on_the_week_prefix_not_on_a_substring():
    """Etykieta rozstrzyga się PRZED dwukropkiem — nazwa tygodnia w ``source_id`` nie ocala."""
    stan = {state.key("2026-W20", f"EMP-{_W30}"): "stare"}

    assert state.prune(stan, keep_weeks=(_W30,)) == {}
