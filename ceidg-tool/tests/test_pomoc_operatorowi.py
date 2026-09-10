"""Ścieżki, na których program dotąd się poddawał — a od 2026-09-09 pomaga (ADR-0017).

Trzy takie miejsca zmierzono w przebiegu UX na trybie demo z żywym asystentem, wszystkie
o tym samym kształcie: operator dostawał komunikat, tracił to, co napisał, i lądował w menu.

* opis, z którego nie dało się zbudować filtra → pusty ekran interpretacji, domyślne
  „tak, szukaj”, a po Enterze `Błąd: Podaj przynajmniej jedno kryterium`;
* zero trafień → jedno zdanie i `wyjdz`, więc pozycja „popraw kryteria” nie pojawiała się
  przy zerze **nigdy**;
* `--zrodlo raport` bez pokrywającego raportu → wyjątek z nazwą flagi wiersza poleceń,
  w kreatorze, który żadnych flag nie ma.

Testy stoją razem, bo naprawa jest jedna: **żadne wejście operatora nie kończy się ślepym
zaułkiem**. Osobno na końcu to, co ta naprawa odsłoniła w skoroszycie — kolumny i odsetek
kontaktów. Ta sama zasada, tylko wypisana do pliku zamiast na ekran.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from pydantic import ValidationError

from ceidg_tool.assistant import Assistant, AssistantResult
from ceidg_tool.config import Settings
from ceidg_tool.criteria import Criteria, bledy_po_polsku
from ceidg_tool.normalizer import KOLUMNY_TYLKO_ZE_SZCZEGOLOW, SHEET_FIRMY, columns, normalize
from ceidg_tool.pipeline import Deps, build_deps
from ceidg_tool.records import RawRecord, RowContext
from ceidg_tool.reports import filtry_poza_raportem, report_covers
from ceidg_tool.ui import flow, texts
from ceidg_tool.ui.prompts import CancelledError, DefaultsPrompter, ScriptedPrompter
from tests.conftest import FakeClock
from tests.support import FakeApi, RecordingView, criteria

FIXTURES = Path(__file__).parent / "fixtures"
CTX = RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z")


# ------------------------------------------------------------------- atrapy i pomocnicy


class SkryptowanyAsystent:
    """Oddaje przygotowane wyniki i notuje, o co go pytano — po kolei."""

    def __init__(self, wyniki: list[AssistantResult]) -> None:
        self.wyniki = list(wyniki)
        self.opisy: list[str] = []

    def interpret(self, opis: str, *, dzisiaj: date) -> AssistantResult:
        self.opisy.append(opis)
        return self.wyniki.pop(0)


def deps_z_asystentem(assistant: Assistant) -> Deps:
    """Minimalne `Deps` — runda dopytania nie dotyka ani sieci, ani bazy.

    Ograniczenie tego, czego atrapa dotyka, jest tu asercją samą w sobie: gdyby ścieżka opisu
    zaczęła czytać cokolwiek jeszcze, te testy padną."""
    from types import SimpleNamespace

    from ceidg_tool.progress import NullEvents

    return cast(
        "Deps",
        SimpleNamespace(assistant=assistant, assistant_reason=None, events=NullEvents()),
    )


def liczace_api(*counts: int) -> FakeApi:
    """API oddające kolejne liczby na kolejne zapytania `count` — po jednej na wywołanie."""
    api = FakeApi()
    kolejka = list(counts)

    def fallback(request: httpx.Request) -> httpx.Response:
        if "/firmy" in str(request.url) and request.url.params.get("limit") == "1":
            wartosc = kolejka.pop(0) if kolejka else 0
            return httpx.Response(200, json={"count": wartosc, "firmy": []})
        if "/raporty" in str(request.url):
            # Pusta lista, a nie 404: „na dziś nie ma gotowego raportu" to normalny stan
            # rejestru. Testy zera trafień mają jedno województwo, więc `report_covers`
            # przepuszcza je do tego zapytania i bez tej gałęzi padałyby na atrapie.
            return httpx.Response(200, json={"raporty": []})
        raise AssertionError(f"nieoczekiwane żądanie: {request.url}")

    api.fallback = fallback
    return api


def deps_sieciowe(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    return build_deps(settings, clock=clock, http=api.client())


def zapytania_count(api: FakeApi) -> list[str]:
    return [r for r in api.requests if "limit=1" in r]


# -------------------------------------------------------- runda dopytania (ADR-0017)


def test_an_empty_interpretation_asks_instead_of_failing() -> None:
    """Opis bez filtrów **nie może** dojść do „Podaj przynajmniej jedno kryterium”.

    To jest cała treść ADR-0017 w jednym zdaniu. Warunkiem wejścia w rundę są puste
    kryteria, a nie to, czy model o coś zapytał — inaczej obietnica trzymałaby się
    zachowania modelu i przestawałaby obowiązywać przy pierwszej gorszej odpowiedzi.
    """
    pusty = AssistantResult(
        kryteria=Criteria(),
        pytanie="Czego dokładnie szukasz — jakiej branży albo w jakim mieście?",
        propozycje=("salony fryzjerskie w Poznaniu", "piekarnie w Białymstoku"),
    )
    gotowy = AssistantResult(kryteria=criteria(miasto="Poznań"))
    asystent = SkryptowanyAsystent([pusty, gotowy])
    prompter = ScriptedPrompter({"dopytanie": "1", "zatwierdz_interpretacje": "tak"})
    view = RecordingView()

    wynik = flow.collect_from_description(
        deps_z_asystentem(asystent), prompter, view, opis="wszystkie firmy"
    )

    assert wynik == criteria(miasto="Poznań")
    # Wybrana propozycja wraca do modelu jako **nowy opis**, a nie jako gotowe kryteria:
    # przechodzi więc walidator PKD i ekran potwierdzenia układany ze słownika lokalnego.
    assert asystent.opisy == ["wszystkie firmy", "salony fryzjerskie w Poznaniu"]
    assert "Czego dokładnie szukasz" in view.block_titled("Doprecyzujmy").as_text()


def test_the_clarification_round_happens_even_when_the_model_asks_nothing() -> None:
    """Siatka bezpieczeństwa: model milczy, a operator i tak dostaje wyjście.

    Mutacja odwrotna do tego testu — wejście w rundę dopiero wtedy, gdy `wynik.pytanie` jest
    niepuste — przywraca dokładnie stary defekt dla każdej odpowiedzi, w której model
    zapomniał zapytać. Gwarancja, której obserwator zależy od modelu, nie jest gwarancją.
    """
    asystent = SkryptowanyAsystent([AssistantResult(kryteria=Criteria())])
    prompter = ScriptedPrompter({"dopytanie": "1", "wojewodztwo_z_listy": "wielkopolskie"})
    view = RecordingView()

    wynik = flow.collect_from_description(
        deps_z_asystentem(asystent), prompter, view, opis="cokolwiek"
    )

    # Bez propozycji modelu pierwszą pozycją jest droga od niego niezależna: lista województw.
    assert wynik == Criteria(wojewodztwo=("wielkopolskie",))
    assert asystent.opisy == ["cokolwiek"]  # drugie żądanie do modelu nie padło
    assert texts.DOPYTANIE_ZAPASOWE in view.block_titled("Doprecyzujmy").as_text()


def test_choosing_a_voivodeship_costs_no_second_model_request() -> None:
    """Szesnaście wartości ze zbioru zamkniętego — nie ma czego tłumaczyć.

    Żądanie do modelu po to, żeby odczytał nazwę wybraną przed chwilą z listy, byłoby
    wydatkiem na własną odpowiedź."""
    asystent = SkryptowanyAsystent([AssistantResult(kryteria=Criteria(), propozycje=("a", "b"))])
    prompter = ScriptedPrompter({"dopytanie": "3", "wojewodztwo_z_listy": "podlaskie"})

    wynik = flow.collect_from_description(
        deps_z_asystentem(asystent), prompter, RecordingView(), opis="nie wiem"
    )

    assert wynik == Criteria(wojewodztwo=("podlaskie",))
    assert len(asystent.opisy) == 1


def test_the_clarification_round_still_lets_the_operator_leave() -> None:
    """Pomoc nie może zamienić się w pułapkę — droga do menu zostaje.

    Pozycje bez propozycji: 1 województwo, 2 własne zdanie, 3 pytania po kolei, 4 wyjście."""
    asystent = SkryptowanyAsystent([AssistantResult(kryteria=Criteria())])

    with pytest.raises(CancelledError):
        flow.collect_from_description(
            deps_z_asystentem(asystent),
            ScriptedPrompter({"dopytanie": "4"}),
            RecordingView(),
            opis="nie wiem",
        )


def test_the_model_cannot_flood_the_menu_with_proposals() -> None:
    """Propozycje to jedyne miejsce, w którym model pisze zdanie dla operatora — z sufitem.

    Bez niego jedna rozgadana odpowiedź zamienia menu w ścianę tekstu razy cztery,
    a powtórzona pozycja jest dla operatora usterką programu, nie wyborem."""
    from ceidg_tool.assistant.schema import AssistantAnswer
    from ceidg_tool.assistant.translate import (
        DLUGOSC_PROPOZYCJI,
        DLUGOSC_PYTANIA,
        MAKS_PROPOZYCJI,
        to_result,
    )

    odpowiedz = AssistantAnswer(
        propozycje=("to samo", "to samo", "x" * 500, "trzecia", "czwarta", "piąta"),
        pytanie="y" * 400,
    )
    wynik = to_result(odpowiedz, {})

    assert len(wynik.propozycje) <= MAKS_PROPOZYCJI
    assert wynik.propozycje.count("to samo") == 1
    assert all(len(p) <= DLUGOSC_PROPOZYCJI for p in wynik.propozycje)
    assert len(wynik.pytanie) <= DLUGOSC_PYTANIA


# ------------------------------------------------------------------------ zero trafień


def test_a_proposal_cannot_steer_the_terminal() -> None:
    """Propozycje modelu idą do **etykiet opcji**, a te trafiają na stdout z pominięciem `rich`.

        `ConsolePrompter.ask` renderuje je do `questionary`, a na ścieżce awaryjnej wprost do
        `input(...)` — surowym zapisem, którego skan reguły granic 10 nie widzi, bo to nie jest
        ani `print`, ani `rich`. Sam `" ".join(split())` usuwa `
    ` i `	`, ale nie ESC ani BEL.
        Ten kanał powstał razem z rundą dopytania i cała jej argumentacja mówi, że gwarancja nie
        może opierać się na tym, że model się zachowa (przegląd 2026-09-09).
    """
    from ceidg_tool.assistant.schema import AssistantAnswer
    from ceidg_tool.assistant.translate import to_result

    wynik = to_result(
        AssistantAnswer(
            propozycje=("fryzjerzy[2J w Poznaniu", "piekarnie"),
            pytanie="czego[1;31m szukasz?",
        ),
        {},
    )

    wszystko = wynik.pytanie + "".join(wynik.propozycje)
    assert "" not in wszystko and "" not in wszystko
    assert "fryzjerzy" in wynik.propozycje[0]  # treść zostaje, sterowanie znika


def test_zero_hits_offers_to_drop_the_filter_most_likely_at_fault(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Po zerze pada pytanie z konkretem, a przyjęcie propozycji kosztuje jedno `count`."""
    api = liczace_api(0, 615)
    deps = deps_sieciowe(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"brak_trafien": "nazwa", "co_dalej": "lista"})

    decyzja, plan = flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie", miasto="Białystok", nazwa="Piekarnia"),
        deps,
        prompter,
        view,
        threshold=50_000,
    )

    assert decyzja == "lista" and plan.count == 615
    assert plan.criteria.nazwa == ()  # zdjęty został dokładnie ten filtr
    assert plan.criteria.miasto == ("Białystok",)  # i tylko on
    assert len(zapytania_count(api)) == 2  # jedno na pustkę, jedno na poszerzenie
    tresc = view.block_titled("Nic nie znaleziono").as_text()
    assert "dosłownie" in tresc  # powód, a nie sama lista opcji
    deps.store.close()


def test_zero_hits_in_a_schedule_end_quietly(tmp_path: Path, clock: FakeClock) -> None:
    """`--tak` nie poszerza populacji za nikogo — domyślną odpowiedzią jest wyjście.

    Poszerzenie zdejmuje filtr, czyli zmienia zbiór, o który ktoś prosił. Zadanie
    z harmonogramu ma po zerze skończyć, a nie pobrać coś innego."""
    api = liczace_api(0)
    deps = deps_sieciowe(tmp_path, clock, api)

    decyzja, plan = flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie", nazwa="Piekarnia"),
        deps,
        DefaultsPrompter(),
        RecordingView(),
        threshold=50_000,
    )

    assert decyzja == "wyjdz" and plan.count == 0
    assert len(zapytania_count(api)) == 1
    deps.store.close()


def test_the_widening_never_proposes_an_empty_query() -> None:
    """Kryteria bez ani jednego filtra objęłyby cały rejestr — takiej propozycji nie ma.

    Lepsze „to był jedyny filtr” niż pozycja w menu, po wybraniu której program odmawia."""
    assert criteria(miasto="Poznań").poszerzenia() == ()

    pary = criteria(miasto="Poznań", nazwa="X").poszerzenia()
    assert [pole for pole, _ in pary] == ["nazwa", "miasto"]
    assert all(not k.is_empty() for _, k in pary)


def test_dropping_the_industry_filter_drops_the_2007_extension_too() -> None:
    """`pkd_2007` jest rozszerzeniem tego samego filtru (ADR-0012).

    Kandydat, który zostawiłby stare kody, dalej odsiewałby po branży — tylko innym
    rocznikiem — więc nie byłby poszerzeniem, mimo etykiety."""
    kryteria = criteria(miasto="Poznań", pkd="9621Z", pkd_2007="9602Z")

    (pole, kandydat) = next(p for p in kryteria.poszerzenia() if p[0] == "pkd")

    assert pole == "pkd"
    assert kandydat.pkd == () and kandydat.pkd_2007 == ()


def test_every_widening_candidate_has_a_polish_name_and_a_reason() -> None:
    """Filtr bez zdania wypadłby z ekranu po cichu — i to wtedy, gdy jest jedyną propozycją.

    Ta sama kontrola co przy kodach ograniczeń asystenta: dwa zbiory kluczy, dwa sposoby na
    cichą awarię. Kolejność w `Criteria.poszerzenia` jest tu źródłem prawdy."""
    wszystkie = criteria(
        nazwa="X",
        ulica="Y",
        kod="61-001",
        imie="Jan",
        nazwisko="Kowalski",
        miasto="Poznań",
        pkd="9621Z",
        gmina="G",
        powiat="P",
        status="AKTYWNY",
        data_od="2020-01-01",
        wojewodztwo="wielkopolskie",
    )

    pola = {pole for pole, _ in wszystkie.poszerzenia(limit=99)}

    assert pola <= set(texts.POLE_PO_POLSKU)
    assert set(texts.POLE_PO_POLSKU) == set(texts.DLACZEGO_PUSTO)


# ------------------------------------------------------- raport, który nie pokrywa


def test_every_reason_the_report_path_declines_has_a_sentence() -> None:
    """Kody powodów i zdania muszą się pokrywać — `texts` nie importuje `reports` (reguła 6).

    Dokładnie ten rozjazd dał defekt A10: lista odmawiała dwóch statusów, a ekran wymieniał
    jeden."""
    powody = {
        flow._powod_braku_raportu(criteria(wojewodztwo=("podlaskie", "lubelskie")), ()),
        flow._powod_braku_raportu(criteria(miasto="Poznań"), ()),
        flow._powod_braku_raportu(criteria(wojewodztwo="podlaskie"), ()),
        flow._powod_braku_raportu(criteria(wojewodztwo="podlaskie"), ("WYKRESLONY",)),
        flow._powod_braku_raportu(
            criteria(wojewodztwo="podlaskie", nip_sc="3563457932"),
            (),
        ),
    }

    assert powody == set(texts.RAPORT_NIEDOSTEPNY)


def test_filtr_po_spolce_cywilnej_odbiera_droge_raportu() -> None:
    """Dzienny zrzut nie ma kolumny o spółce cywilnej, więc filtr po niej dałby pustkę.

    Zmierzone 2026-09-10 na nagłówku `probe_out/raport_sample.zip`: 24 kolumny, żadna
    o spółce. To ten sam kształt defektu co A10 — źródło milczy o polu, po którym filtrujemy,
    a `--zrodlo auto` wybiera je właśnie dlatego, że jest tanie. Bramka stoi w dwóch
    miejscach, bo `run_report_fetch` wołane jest także z `--zrodlo raport` i ze wznowienia.
    """
    pytanie = criteria(wojewodztwo="podlaskie", nip_sc="3563457932")

    assert not report_covers(pytanie)
    assert filtry_poza_raportem(pytanie) == ("nip_sc",)
    assert report_covers(criteria(wojewodztwo="podlaskie"))
    # Numer domu i lokalu **nie** odbierają tej drogi: `NrBudynku` i `NrLokalu` są w zrzucie,
    # a `matches_criteria` je porównuje. Bez tej asercji równie dobrze przechodziłaby
    # poprawka odrzucająca ścieżkę raportu dla każdego nowego pola.
    assert report_covers(criteria(wojewodztwo="podlaskie", budynek="12A", lokal="3"))


# ------------------------------------------ skoroszyt i podsumowanie: zero to nie pomiar


def test_the_summary_does_not_report_zero_contacts_it_never_fetched(tmp_path: Path) -> None:
    """„z telefonem 0 (0%)” w trybie listy było liczbą odpowiadającą na inne pytanie."""
    plik = tmp_path / "w.xlsx"
    plik.write_bytes(b"x" * 16)

    def podsumowanie(bez_kontaktow: bool, kind: str = "firmy") -> str:
        return texts.summary_table(
            texts.SummaryInput(
                paths=(plik,),
                records=48,
                by_status={"AKTYWNY": 48},
                with_phone=0,
                with_email=0,
                bez_kontaktow=bez_kontaktow,
                sheets=("Firmy",),
                kind=kind,
                run_ids=("r",),
                log_path=tmp_path / "log",
            )
        ).as_text()

    bez = podsumowanie(True)
    ze_szczegolami = podsumowanie(False)

    assert "0 (0%)" not in bez
    assert texts.BEZ_KONTAKTOW in bez
    assert "0 (0%)" in ze_szczegolami  # przy pobranych szczegółach zero jest już pomiarem


def test_the_report_path_has_contacts_without_any_details_fetched(tmp_path: Path) -> None:
    """Kontakty mają **dwa** źródła i pierwsza wersja tej poprawki widziała jedno.

    `/firma` wypełnia telefon i e-mail przez `detail_json`, a dzienny raport wprost w wierszu
    CSV — bez żadnych szczegółów, więc `count_details_for_runs` zwraca dla niego zero.
    Przebieg produkcyjny 2026-09-09 (282 firmy z raportu wielkopolskiego, kontakty w 63 i 68
    wierszach) dostał przez to zdanie „nie pobrano" i radę, żeby powtórzyć ze szczegółami —
    choć dane siedziały w pliku. Naprawa cichej nieprawdy na ścieżce częstszej wyprodukowała
    głośną na rzadszej; ten test pilnuje obu naraz."""
    plik = tmp_path / "w.xlsx"
    plik.write_bytes(b"x" * 16)

    z_raportu = texts.summary_table(
        texts.SummaryInput(
            paths=(plik,),
            records=282,
            by_status={"AKTYWNY": 242},
            with_phone=63,
            with_email=68,
            bez_kontaktow=False,  # raport nie ma szczegółów, ale kontakty niesie
            sheets=("Firmy",),
            kind="raport",
            run_ids=("r",),
            log_path=tmp_path / "log",
        )
    ).as_text()

    assert "63 (22%)" in z_raportu and "68 (24%)" in z_raportu
    assert texts.BEZ_KONTAKTOW not in z_raportu


def test_hiding_columns_without_saying_so_would_be_the_same_defect_reversed(
    tmp_path: Path,
) -> None:
    """Skoroszyt z trybu listy chowa kolumny — i musi o tym powiedzieć na ekranie.

    Ukrycie bez zdania to ta sama cisza co „0 (0%)", tylko odwrócona: operator zobaczy
    w Excelu przeskakujące litery kolumn i uzna, że plik jest niepełny. Ekran czyta na
    pewno, arkusza `Metadane` może nigdy nie otworzyć."""

    def uwagi(bez_kontaktow: bool) -> tuple[str, ...]:
        return texts.summary_notes(
            texts.SummaryInput(
                paths=(),
                records=10,
                by_status={},
                with_phone=0,
                with_email=0,
                bez_kontaktow=bez_kontaktow,
                sheets=(),
                kind="firmy",
                run_ids=("r",),
                log_path=tmp_path / "log",
            )
        )

    assert any("kolumny_ukryte" in u for u in uwagi(True))
    assert not any("kolumny_ukryte" in u for u in uwagi(False))


def test_an_interrupted_details_run_is_told_to_resume_not_to_start_over(
    tmp_path: Path,
) -> None:
    """Ten sam brak kontaktów, inna przyczyna i **inne lekarstwo**.

    Pobranie ze szczegółami przerwane przed etapem szczegółów ma w pliku zero kontaktów tak
    samo jak zwykła lista, więc predykat je zlewa. Ale rada „powtórz z opcją ze szczegółami"
    znaczy dla niego „zacznij od zera", podczas gdy uwaga o niedokończonym przebiegu,
    drukowana obok, mówi `wznow`. Dwa zdania na jednym ekranie odsyłające w dwie strony są
    gorsze niż jedno milczenie (przegląd 2026-09-09)."""

    def wiersz(tryb_szczegoly: bool) -> str:
        return texts.summary_table(
            texts.SummaryInput(
                paths=(),
                records=12,
                by_status={},
                with_phone=0,
                with_email=0,
                bez_kontaktow=True,
                sheets=(),
                kind="firmy",
                run_ids=("r",),
                log_path=tmp_path / "log",
                tryb_szczegoly=tryb_szczegoly,
                statuses=("przerwany",),
            )
        ).as_text()

    assert "wznow" in wiersz(True)
    assert texts.BEZ_KONTAKTOW not in wiersz(True)
    assert texts.BEZ_KONTAKTOW in wiersz(False)


def test_the_detail_only_columns_are_never_filled_by_the_list_endpoint() -> None:
    """Zbiór ukrywanych kolumn zmierzony wobec prawdziwych (zanonimizowanych) rekordów `/firmy`.

    Kierunek jest tu całą treścią: ukrycie kolumny, którą lista **bywa** w stanie wypełnić,
    to defekt — operator traci dane, o których nie wie. Pominięcie kolumny zawsze pustej to
    tylko bałagan. Dlatego test sprawdza wyłącznie ten pierwszy kierunek, za to na danych
    z rejestru, a nie na atrapie, która potrafi zgadzać się sama ze sobą.
    """
    wypelnione: set[str] = set()
    rekordow = 0
    for nazwa in (
        "firmy_limit25.json",
        "firmy_wojewodztwo.json",
        "firmy_page0_limit5.json",
        "firmy_page1_limit5.json",
    ):
        body = json.loads((FIXTURES / nazwa).read_text(encoding="utf-8"))["body"]
        for rekord in body.get("firmy", []):
            rekordow += 1
            znormalizowany = normalize(
                RawRecord(
                    id=rekord["id"],
                    list_json=rekord,
                    detail_json=None,
                    list_utc="2026-09-05T09:00:00Z",
                    detail_utc=None,
                    detail_state="brak",
                    zrodlo="CEIDG_API",
                ),
                CTX,
            )
            wypelnione |= {k for k, v in znormalizowany.firmy.items() if v}

    assert rekordow >= 30, "pomiar bez danych nie jest pomiarem"
    assert KOLUMNY_TYLKO_ZE_SZCZEGOLOW <= set(columns(SHEET_FIRMY))
    assert not (KOLUMNY_TYLKO_ZE_SZCZEGOLOW & wypelnione)


# ------------------------------------------------------------------ zdania zamiast zrzutów


def test_a_bad_nip_gets_a_sentence_not_a_pydantic_dump() -> None:
    """Odbiorcą tego komunikatu jest ktoś, kto przepisał NIP z faktury."""
    with pytest.raises(ValidationError) as exc:
        Criteria(nip=("1234567890",))

    tekst = bledy_po_polsku(exc.value)

    assert "błędną sumę kontrolną" in tekst
    assert "pydantic" not in tekst
    assert "input_type" not in tekst and "value_error" not in tekst


def test_the_demo_says_why_the_assistant_is_off_instead_of_pointing_at_the_keyring() -> None:
    """Tryb pokazu ma inną przyczynę braku klucza niż produkcja — i musi ją nazwać.

    Zdanie „brak klucza: `ceidg-tool token zapisz --asystent`” jest w pokazie nieprawdą
    podwójnie: klucz zwykle istnieje (w `.env`), a polecenie z podpowiedzi pisze do keyringu,
    którego pokaz celowo nie czyta. Operator nie miał więc jak zobaczyć asystenta w jedynym
    miejscu, gdzie wolno go poznawać bez prawdziwych danych osobowych."""
    ustawienia = Settings(token="tok", environment="test", data_dir=Path("x"))
    teraz = datetime(2026, 9, 9, tzinfo=UTC)

    pokaz = texts.first_screen(ustawienia, now=teraz, version="0", demo=True).as_text()
    zwykly = texts.first_screen(ustawienia, now=teraz, version="0", demo=False).as_text()

    assert "ANTHROPIC_API_KEY" in pokaz and "keyring" in pokaz
    assert "token zapisz --asystent" not in pokaz
    assert "token zapisz --asystent" in zwykly


def test_the_cli_does_not_blame_a_missing_key_for_the_operators_own_choice() -> None:
    """`None` z `collect_from_description` znaczy „wybrałem pytania po kolei”, nie „brak klucza”.

    Wskazywanie palcem na rzecz, która akurat działa, to ta sama klasa pomyłki co komunikat
    w pokazie wyżej (przebieg B6)."""
    assert "kreator" in texts.OPIS_PORZUCONY
    assert "brakuje klucza" not in texts.OPIS_PORZUCONY


# ---------------------------------------------- błędy pydantica też są dla operatora (bramka 3)

# Dziewięć omyłek, jakie realnie popełnia ktoś, kto nie zna danych firmowych. Lista pochodzi
# z przejścia bramki 3 na produkcji (2026-09-09), gdzie cztery z nich wracały po angielsku.
OMYLKI_LAIKA: tuple[tuple[str, Any, str], ...] = (
    ("status", "czynna", "dozwolone wartości"),
    ("status", "aktywne", "dozwolone wartości"),
    ("data_od", "01.01.2020", "RRRR-MM-DD"),
    ("data_do", "wczoraj", "RRRR-MM-DD"),
    ("max_rekordow", "dziesiec", "liczba całkowita"),
    ("max_rekordow", "0", "nie mniejsza niż 1"),
    ("szczegoly", "moze", "tak albo nie"),
    ("wojewodztwo", "wielkopolska", "nieznane województwo"),
    ("pkd", "fryzjer", "musi mieć postać"),
)

# Ślady, po których poznaje się, że na ekran trafił komunikat pydantica zamiast naszego zdania.
ANGIELSZCZYZNA: tuple[str, ...] = (
    "Input should",
    "unable to parse",
    "valid date",
    "valid integer",
    "Value error",
    "Assertion failed",
    "pydantic",
)


@pytest.mark.parametrize(("pole", "wartosc", "oczekiwane"), OMYLKI_LAIKA)
def test_bledy_walidacji_sa_po_polsku_takze_te_z_pydantica(
    pole: str, wartosc: Any, oczekiwane: str
) -> None:
    """Operator tego narzędzia z założenia nie zna API — i nie musi znać angielskiego.

    `bledy_po_polsku` brało `blad["msg"]`, które jest polskie tylko wtedy, gdy przyszło
    z naszego walidatora. Wbudowane błędy pydantica (`literal_error`, `date_*`, `int_parsing`,
    `bool_parsing`) szły na ekran po angielsku, a poprawka z fazy 6 czytała się, jakby
    domknęła sprawę: zdjęła odnośnik do errors.pydantic.dev i prefiks „Value error, ”,
    czyli to, co czyniło stare zrzuty nieczytelnymi, ale nigdy nie tłumaczyła.

    `status` jest pytaniem otwartym z podpowiedzią „np. AKTYWNY”, więc wpisanie polskiego
    słowa jest tam błędem **oczekiwanym**. Zmierzone na bramce 3 (2026-09-09).
    """
    with pytest.raises(ValidationError) as exc:
        # Wartości są niepoprawne z rozmysłu — one **są** treścią tego testu.
        Criteria(**{pole: wartosc})

    tekst = bledy_po_polsku(exc.value)

    assert oczekiwane in tekst, tekst
    for slad in ANGIELSZCZYZNA:
        assert slad not in tekst, f"komunikat pydantica przeciekł na ekran: {tekst}"


def test_komunikat_nazywa_wartosc_ktora_operator_wpisal() -> None:
    """Bez wartości zdanie nie mówi, **co** poprawić — a przy kilku wpisach nie mówi które."""
    with pytest.raises(ValidationError) as exc:
        Criteria(status=("aktywny", "czynna"))  # type: ignore[arg-type]

    tekst = bledy_po_polsku(exc.value)

    assert "CZYNNA" in tekst
    # „status.0” to indeks w krotce, nie nazwa pola; dla operatora jest szumem, a wartość
    # wyżej niesie tę samą informację precyzyjniej.
    assert "status.0" not in tekst and "status.1" not in tekst


def test_nieznany_kod_bledu_nie_ginie() -> None:
    """Nowy rodzaj błędu ma czytać się gorzej, nigdy nie znikać.

    Mapujemy po `type`, bo treść komunikatu pydantica to tekst dla ludzi i zmienia się
    między wersjami. Nierozpoznany kod musi więc wrócić dotychczasową ścieżką.
    """
    with pytest.raises(ValidationError) as exc:
        Criteria(data_od=date(2024, 1, 2), data_do=date(2024, 1, 1))

    tekst = bledy_po_polsku(exc.value)

    assert tekst.strip(), "błąd bez zdania jest gorszy niż zdanie niezgrabne"
    assert "Value error" not in tekst
