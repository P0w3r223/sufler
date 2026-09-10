"""Zapytanie podzielone wysyła ten sam filtr dat co niepodzielone (ADR-0015, audyt A3).

`plan_batches` uzupełnia brakujące granice zakresu wartościami `DATE_FLOOR = 1990-01-01`
i `dzisiaj`, żeby plan był powtarzalny — bez tego przerwanego pobrania nie dałoby się
wznowić. Do 2026-09-09 te dwa zastępniki **nie zostawały w planerze**: `_split` wpisywał je
do `Criteria` każdej partii, a `to_params` wysyłał do API. To samo pytanie zadane dwoma
drogami wysyłało więc dwa różne filtry, a rekordy spoza okna znikały wyłącznie ze ścieżki
podzielonej — czyli z tej, którą program proponuje dla dużych wyników.

**Zmierzone 2026-09-09** na bazie operatora (16 310 rekordów, zero żądań): 76 wpisów sprzed
1990-01-01 (najstarszy 1968-10-13) i 406 z datą rozpoczęcia w przyszłości (najpóźniejszy
2027-05-10, bo CEIDG przyjmuje rejestrację z datą przyszłą) — razem **482 = 2,96 %**.
Wpisów bez daty rozpoczęcia, na które wskazywał komunikat, było w tej bazie **zero**.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ceidg_tool.apiprofile import load_profile
from ceidg_tool.batching import DATE_FLOOR, BatchPlan, plan_batches, refine
from ceidg_tool.config import Settings
from ceidg_tool.criteria import Criteria
from ceidg_tool.pipeline import BatchResult, build_deps
from ceidg_tool.ui import flow
from ceidg_tool.ui.texts import batches_shortfall, batches_surplus
from tests.conftest import FakeClock
from tests.support import RecordingView, criteria

TODAY = date(2026, 9, 9)
PROG = 50_000
DUZO = 400_000


def _plan(*, data_od: str | None = None, data_do: str | None = None) -> BatchPlan:
    return plan_batches(
        criteria(wojewodztwo="podlaskie", data_od=data_od, data_do=data_do),
        DUZO,
        today=TODAY,
        threshold=PROG,
    )


def _parametry(kryteria: Criteria) -> dict[str, str]:
    return dict(kryteria.to_params(load_profile("test")))


def test_partia_pierwsza_nie_wysyla_dolnej_granicy_ktorej_operator_nie_podal() -> None:
    """Rdzeń A3, po stronie zapytania — bo to zapytanie decyduje, co wróci z rejestru."""
    plan = _plan()

    pierwsza = plan.batches[0]
    assert pierwsza.criteria.data_od is None
    assert "dataod" not in _parametry(pierwsza.criteria)
    # Górna granica pierwszej partii jest prawdziwa: to koniec jej kafla, nie zastępnik.
    assert pierwsza.criteria.data_do is not None


def test_partia_ostatnia_nie_wysyla_gornej_granicy_ktorej_operator_nie_podal() -> None:
    """Druga połowa tej samej straty. `dzisiaj` jako sufit jest błędne z konstrukcji:
    rejestr przyjmuje datę rozpoczęcia w przyszłości (406 takich wpisów u operatora)."""
    plan = _plan()

    ostatnia = plan.batches[-1]
    assert ostatnia.criteria.data_do is None
    assert "datado" not in _parametry(ostatnia.criteria)
    assert ostatnia.criteria.data_od is not None


def test_partie_wewnetrzne_maja_obie_granice() -> None:
    """Kontrola pozytywna. „Otwórz wszystko" dałoby partie zachodzące na siebie i pobranie
    tych samych rekordów tyle razy, ile jest partii."""
    plan = _plan()

    assert len(plan.batches) > 2, "test wymaga planu z wnętrzem"
    for partia in plan.batches[1:-1]:
        assert partia.criteria.data_od is not None
        assert partia.criteria.data_do is not None
        assert not partia.otwarty_od and not partia.otwarty_do


def test_zapytanie_z_wlasnym_zakresem_dat_zostaje_nietkniete() -> None:
    """Kontrola pozytywna i granica poprawki: operator, który podał zakres, ma dostać
    dokładnie ten zakres. Otwarcie krawędzi tutaj poszerzałoby jego zapytanie po cichu."""
    plan = _plan(data_od="2000-01-01", data_do="2010-12-31")

    assert not plan.otwarty_od and not plan.otwarty_do
    for partia in plan.batches:
        assert partia.criteria.data_od is not None
        assert partia.criteria.data_do is not None
    assert plan.batches[0].criteria.data_od == date(2000, 1, 1)
    assert plan.batches[-1].criteria.data_do == date(2010, 12, 31)


def test_polowiczny_zakres_otwiera_tylko_ta_krawedz_ktorej_brakuje() -> None:
    """`--od` bez `--do` to zwykłe zapytanie i musi zostać zapytaniem jednostronnym."""
    plan = _plan(data_od="2000-01-01")

    assert not plan.otwarty_od and plan.otwarty_do
    assert plan.batches[0].criteria.data_od == date(2000, 1, 1)
    assert plan.batches[-1].criteria.data_do is None


def test_podzial_partii_skrajnej_nie_przywraca_granicy() -> None:
    """`refine` schodzi o poziom niżej, gdy partia okaże się za duża. Gdyby otwarta krawędź
    tam nie przechodziła, defekt A3 odtwarzałby się o jeden poziom głębiej — i tym razem
    dopiero w trakcie pobierania, gdzie nikt już na tabelę podziału nie patrzy."""
    plan = _plan()
    drobniej = refine(plan.batches[0])

    assert drobniej, "dekada dzieli się na lata"
    assert drobniej[0].criteria.data_od is None and drobniej[0].otwarty_od
    assert all(p.criteria.data_od is not None for p in drobniej[1:])
    assert all(p.criteria.data_do is not None for p in drobniej)


def test_kafle_nadal_pokrywaja_zakres_planistyczny() -> None:
    """Niezmiennik, na którym stoi wznawianie: `od`/`do` kafli są konkretne i rozłączne,
    niezależnie od tego, co partia wysyła do API. Otwartość dotyczy zapytania, nie kafla."""
    plan = _plan()

    assert plan.covers == (DATE_FLOOR, TODAY)
    assert plan.batches[0].od == DATE_FLOOR
    assert plan.batches[-1].do == TODAY
    for wczesniejsza, pozniejsza in zip(plan.batches, plan.batches[1:], strict=False):
        assert (pozniejsza.od - wczesniejsza.do).days == 1


def test_etykieta_partii_mowi_o_otwartej_krawedzi() -> None:
    """Tabela podziału to jedyne miejsce, w którym operator widzi zakres przed zapłatą.
    Sama nazwa kafla („1990-1999") obiecywałaby węziej, niż partia naprawdę pobierze."""
    plan = _plan()

    assert plan.batches[0].label.endswith("i wcześniej")
    assert plan.batches[-1].label.endswith("i później")
    assert not any(p.label.endswith(("i wcześniej", "i później")) for p in plan.batches[1:-1])


# ----------------------------------------------------------- zdanie o różnicy trafień


def test_zdanie_o_roznicy_nie_nazywa_przyczyny_ktorej_nie_zna() -> None:
    """Stare zdanie mówiło, że różnica **to** wpisy bez daty rozpoczęcia. Było fałszywe
    podwójnie: prawdziwą przyczyną były dokładane granice, a wpisów bez daty w bazie
    operatora nie było ani jednego. Zdanie zamykało pytanie zamiast je otworzyć."""
    zdanie = batches_shortfall(counted=58_000, expected=60_000, missing=2_000)

    assert "nie da się przypisać jednej przyczynie" in zdanie
    assert "Rejestr zmienia się" in zdanie
    assert "bez daty rozpoczęcia" in zdanie, "druga możliwość też musi paść"
    assert "powtórz pobranie" in zdanie.lower()


def test_nadwyzka_trafien_ma_obserwatora() -> None:
    """`max(0, expected - counted)` odrzucał przypadek odwrotny bez śladu, więc ten sam
    dryf rejestru w drugą stronę nie miał ani jednego obserwatora."""
    wynik = BatchResult(
        run_ids=("r1",), records=61_000, counted=61_000, expected=60_000, outcomes=(), requests=7
    )

    assert wynik.missing == 0
    assert wynik.surplus == 1_000
    assert "nic nie przepadło" in batches_surplus(61_000, 60_000, 1_000)


def test_zwykly_przebieg_nie_melduje_ani_braku_ani_nadwyzki() -> None:
    """Kontrola pozytywna dla obu liczników naraz."""
    wynik = BatchResult(
        run_ids=("r1",), records=60_000, counted=60_000, expected=60_000, outcomes=(), requests=7
    )

    assert wynik.missing == 0 and wynik.surplus == 0


# ------------------------------------------------- okablowanie nadwyżki (przegląd 2026-09-09)


def test_nadwyzka_dochodzi_z_wyniku_partii_do_notatek_ekranu(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: FakeClock
) -> None:
    """Przegląd wykazał, że skasowanie dopisku o nadwyżce w `flow` zostawia cały pakiet
    zielony: `BatchResult.surplus` był sprawdzony osobno, a zdanie osobno, i nic nie
    pilnowało, że jedno dochodzi do drugiego. Nadwyżkę trudno wywołać prawdziwym API — to
    dryf rejestru w czasie pobierania — więc podstawiamy wynik w miejscu styku."""
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, online=False)
    plan = _plan()
    wynik = BatchResult(
        run_ids=("r1",), records=61_000, counted=61_000, expected=60_000, outcomes=(), requests=7
    )
    monkeypatch.setattr(flow, "run_batched_fetch", lambda *a, **k: wynik)
    view = RecordingView()

    efekt = flow._run_batches(plan, deps, view, threshold=PROG)
    deps.store.close()

    assert any("nic nie przepadło" in u for u in efekt.notes), efekt.notes


def test_zgodny_przebieg_nie_dokleja_notatki_o_nadwyzce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, clock: FakeClock
) -> None:
    """Kontrola pozytywna: „zawsze dopisuj" przeszłoby test wyżej."""
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    deps = build_deps(settings, clock=clock, online=False)
    wynik = BatchResult(
        run_ids=("r1",), records=60_000, counted=60_000, expected=60_000, outcomes=(), requests=7
    )
    monkeypatch.setattr(flow, "run_batched_fetch", lambda *a, **k: wynik)
    view = RecordingView()

    efekt = flow._run_batches(_plan(), deps, view, threshold=PROG)
    deps.store.close()

    assert efekt.notes == ()


def test_partia_o_otwartej_krawedzi_nie_jest_pomijana_jako_juz_pobrana(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Uwaga z przeglądu 2026-09-09, i konsekwencja otwarcia krawędzi, której pierwsza wersja
    ADR-0015 nie przewidziała.

    Przed otwarciem ostatnia partia niosła `data_do = dzisiaj`, więc jej odcisk zmieniał się
    z dnia na dzień i `find_run` nigdy jej nie rozpoznawał. Stabilny odcisk zamienił ten
    przypadek w pułapkę: powtórzony `pobierz --partie` meldował „pominięta (już pobrana)" dla
    **wszystkich** partii i nie przynosił ani jednego rekordu, choć ścieżka niepodzielona
    zawsze pobiera od nowa.

    Rozstrzyga natura kafla, nie data: kafel zamknięty to populacja, która nie rośnie, więc
    „już pobrana" jest o nim prawdą; kafel otwarty rośnie z każdą nową rejestracją."""
    plan = _plan()
    zamkniete = [p for p in plan.batches if not p.otwarty_do]
    otwarte = [p for p in plan.batches if p.otwarty_do]

    assert len(otwarte) == 1, "otwarty jest dokładnie jeden ogon"
    assert zamkniete, "test wymaga też partii zamkniętych"

    # Odcisk kafla otwartego jest ten sam jutro — to jest właśnie powód, dla którego
    # pomijanie go byłoby trwałe, a nie jednodniowe.
    jutro = plan_batches(
        criteria(wojewodztwo="podlaskie"), DUZO, today=date(2026, 9, 10), threshold=PROG
    )
    assert jutro.batches[-1].fingerprint() == otwarte[0].fingerprint()
