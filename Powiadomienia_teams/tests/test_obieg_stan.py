"""Sonda parzystości wygaszania: plik główny i KOPIA gaszą to samo — obiecana w `state.py`.

Wpis domknięty przestaje nieść treść rozmowy (surowe wiadomości pracownika, kotwicę pamięci,
rozstrzygnięte powody nieobecności) i ma zamkniętą bramkę nieodwracalnego zapisu `awaiting_yes`.
Do 0.2.16 obowiązywało to wyłącznie dla `stan.json`: `.bak` trzymał treść do najbliższego zapisu,
czyli po zatrzymaniu usługi — bezterminowo. Ochrona zależna od tego, KTÓRY plik ktoś otworzy,
nie jest ochroną.

Reguła iteruje po POLACH, które `state._wygaszone()` deklaruje jako gaszone, więc pole dopisane
tam jutro wchodzi pod sondę samo. `awaiting_yes` stoi obok, wymieniony jawnie: jest gaszony
w każdym z dwóch przejść OSOBNO (nie należy do listy pól prywatnościowych — patrz docstring
`_wygaszone`), więc to jedyne pole, którego ta sonda musi pilnować z nazwy.

Sonda jest BEHAWIORALNA: przechodzi prawdziwą ścieżką `save_state`, a nie czyta kodu. Rozjazd,
przed którym stoi, powstał właśnie w kodzie wyglądającym poprawnie w obu miejscach z osobna.
"""

from __future__ import annotations

import json

from powiadomienia_teams import state as st

_POLA_TRESCI = tuple(st._wygaszone())


def _wpis(status: str) -> st.PendingReminder:
    return st.PendingReminder(
        member_id="u1",
        member_name="Jan Przykładowy",
        chat_id="19:czat",
        week_start="2026-09-14",
        status=status,
        watermark="2026-09-11T14:00:00Z",
        employee_memory=["w piątek 10-20", "ok"],
        memory_started_at="2026-09-11T14:00:00Z",
        resolved_time_off=[{"weekday": 4, "reason_id": "r1", "reason_name": "chorobowe"}],
        awaiting_yes=True,
    )


def _czytaj(sciezka) -> dict:
    return json.loads(sciezka.read_text(encoding="utf-8"))["u1"]


def test_domkniecie_gasi_tresc_w_pliku_glownym_i_w_kopii(tmp_path):
    """Ta sama rozmowa, dwa pliki, jedno oczekiwanie — i kopia powstaje z POPRZEDNIEGO stanu."""
    sciezka = tmp_path / "stan.json"

    # 1. Rozmowa otwarta: treść MUSI być na dysku, inaczej sonda mierzyłaby pustkę.
    st.save_state(sciezka, {"u1": _wpis(st.AWAITING_CONFIRM)})
    otwarty = _czytaj(sciezka)
    assert otwarty["employee_memory"], "wpis otwarty stracił treść — sonda straciła przedmiot"
    assert otwarty["awaiting_yes"] is True

    # 2. Ta sama rozmowa domknięta: kopia powstaje z pliku sprzed tego zapisu.
    st.save_state(sciezka, {"u1": _wpis(st.APPLIED)})

    for nazwa, wpis in (
        ("plik główny", _czytaj(sciezka)),
        ("kopia", _czytaj(st.sciezka_kopii(sciezka))),
    ):
        assert wpis["status"] == st.APPLIED, f"{nazwa}: status rozmowy domkniętej"
        for pole in _POLA_TRESCI:
            assert not wpis[pole], f"{nazwa}: pole treści {pole!r} przeżyło domknięcie"
        assert wpis["awaiting_yes"] is False, f"{nazwa}: bramka N38 została otwarta"


def test_wpis_otwarty_zachowuje_tresc(tmp_path):
    """Kierunek przeciwny — bez niego wygaszenie wszystkiego zawsze dawałoby test zielony."""
    sciezka = tmp_path / "stan.json"
    st.save_state(sciezka, {"u1": _wpis(st.AWAITING_CONFIRM)})
    wpis = _czytaj(sciezka)
    for pole in _POLA_TRESCI:
        assert wpis[pole], f"pole treści {pole!r} zgaszone na wpisie OTWARTYM"


def test_lista_pol_gaszonych_nie_wyparowala():
    """Sonda pyta o pola z `_wygaszone()`; pusta lista zamieniłaby ją w milczenie na zielono."""
    assert len(_POLA_TRESCI) >= 3, _POLA_TRESCI
