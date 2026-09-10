"""Tożsamość wiersza raportu, któremu rejestr nie nadał numeru (ADR-0016, audyt A9).

Wiersz bez NIP-u i bez REGON-u dostawał `HASH:` liczony ze **wszystkich** kolumn — w tym
z `Lp.`, czyli z numeru porządkowego w konkretnym pobraniu. Kolejność w rejestrze zmienia
się z dnia na dzień, więc ten sam przedsiębiorca miał w każdym archiwum inną tożsamość:
baza zbierała duplikaty, których żaden `ON CONFLICT` nie scalał, a porównanie dwóch pobrań
meldowało zmianę tam, gdzie nic się nie zmieniło. To defekt ADR-0013 przeniesiony na drugie
źródło — jeden wpis, dwie pisownie — tylko z innym mechanizmem produkującym drugą.

**Zmierzone 2026-09-09** na `probe_out/raport_sample.zip` (287 256 wierszy, wielkopolskie,
zero żądań): 315 wierszy bez NIP-u i bez REGON-u, a klucz *nazwa + nazwisko + imię + data
rozpoczęcia* daje na nich **zero kolizji** przy stuprocentowym wypełnieniu każdego z tych
czterech pól. Dołożenie pełnego adresu nie zmienia ani jednej kolizji, więc adres nie wchodzi
do klucza — jego składowe są wypełnione w 23-69 % i uzależniłyby tożsamość od staranności
wypełnienia formularza.

Najważniejsza kontrola pozytywna w tym pliku to `test_rozne_firmy_nie_zlewaja_sie_w_jedna`:
klucz zbyt wąski byłby tym samym defektem obróconym o 180 stopni — zamiast mnożyć wpisy,
sklejałby cudze.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import httpx

from ceidg_tool.config import Settings
from ceidg_tool.pipeline import build_deps, run_report_fetch
from ceidg_tool.recordid import id_z_tresci, kanoniczny_id
from ceidg_tool.records import Report
from ceidg_tool.reports import record_id_for
from tests.conftest import FakeClock
from tests.support import FakeApi, criteria
from tests.test_reports import HEADER, row

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"


# ------------------------------------------------------------------ tożsamość jako funkcja


def test_tozsamosc_nie_zalezy_od_numeru_porzadkowego() -> None:
    """Rdzeń A9. `Lp.` to pozycja w pobraniu, a nie cecha przedsiębiorcy."""
    # Klucz w CSV nazywa się „Lp." — z kropką, więc nie da się go podać jako `row(Lp=…)`.
    # Ta pomyłka jest tu warta komentarza: `row(Lp="01")` dokłada **inny** klucz i test
    # przechodziłby, mierząc coś innego niż nazwa obiecuje.
    pierwszy = {**row(Nip="", Regon="", NazwaPodmiotu="ZAKŁAD"), "Lp.": "01"}
    ten_sam_jutro = {**row(Nip="", Regon="", NazwaPodmiotu="ZAKŁAD"), "Lp.": "7412"}

    assert pierwszy["Lp."] != ten_sam_jutro["Lp."], "kontrola: numery muszą się różnić"
    assert record_id_for(pierwszy) == record_id_for(ten_sam_jutro)


def test_tozsamosc_nie_zalezy_od_pol_ktore_zmieniaja_sie_w_zyciu_firmy() -> None:
    """„Wszystkie kolumny poza `Lp.`" byłoby tym samym defektem, o poziom rzadszym:
    zawieszenie działalności, nowy telefon albo dopisany kod PKD nadawałyby wpisowi nową
    tożsamość — i dopiero wtedy, czyli w chwili, w której nikt tego nie testuje."""
    przed = row(Nip="", Regon="", NazwaPodmiotu="ZAKŁAD", StatusDzialalnosci="Aktywny")
    po = row(
        Nip="",
        Regon="",
        NazwaPodmiotu="ZAKŁAD",
        StatusDzialalnosci="Zawieszony",
        Telefon="601000000",
        PozostaleKodyPkd="4711Z",
    )

    assert record_id_for(przed) == record_id_for(po)


def test_rozne_firmy_nie_zlewaja_sie_w_jedna() -> None:
    """Kontrola pozytywna i granica poprawki. Klucz zbyt wąski sklejałby cudze wpisy —
    strata gorsza od mnożenia, bo nieodwracalna i niewidoczna w liczbie rekordów."""
    a = row(Nip="", Regon="", NazwaPodmiotu="ZAKŁAD", Nazwisko="Kowalski", Imie="Jan")
    b = row(Nip="", Regon="", NazwaPodmiotu="ZAKŁAD", Nazwisko="Kowalski", Imie="Anna")
    c = row(
        Nip="",
        Regon="",
        NazwaPodmiotu="ZAKŁAD",
        Nazwisko="Kowalski",
        Imie="Jan",
        DataRozpoczeciaDzialalnosci="1999-01-01",
    )

    assert len({record_id_for(a), record_id_for(b), record_id_for(c)}) == 3


def test_nip_i_regon_maja_pierwszenstwo_przed_skrotem() -> None:
    """Kontrola regresji: skrót jest ostatecznością, a nie nową regułą dla wszystkich."""
    assert record_id_for(row(Nip="3563457932")) == "NIP:3563457932"
    assert record_id_for(row(Nip="", Regon="113110043")) == "REGON:113110043"
    assert record_id_for(row(Nip="", Regon="")).startswith("HASH:")


def test_puste_i_brakujace_pole_daja_ten_sam_skrot() -> None:
    """Własność, na której stanie ewentualna przyszła migracja: wejście liczy tożsamość
    z wiersza CSV (gdzie puste pole to `""`), a migracja liczyłaby ją z `list_json`, gdzie
    `_drop_none` już to pole usunął. Gdyby normalizacja siedziała po stronie wołającego,
    obie drogi policzyłyby dwa różne skróty — i migracja dorobiłaby trzecią tożsamość."""
    z_pustym = id_z_tresci(nazwa="ZAKŁAD", nazwisko="Kowalski", imie="Jan", data_rozpoczecia="")
    z_brakiem = id_z_tresci(nazwa="ZAKŁAD", nazwisko="Kowalski", imie="Jan", data_rozpoczecia=None)
    ze_spacjami = id_z_tresci(
        nazwa=" ZAKŁAD ", nazwisko="Kowalski", imie="Jan", data_rozpoczecia="  "
    )

    assert z_pustym == z_brakiem == ze_spacjami


def test_skrot_nie_jest_guidem_wiec_kanonizacja_go_nie_rusza() -> None:
    """Styk z ADR-0013. `HASH:` ma inny kształt niż GUID wpisu, więc `kanoniczny_id` musi
    go przepuścić bez zmiany — inaczej wiersz raportu dostałby drugą pisownię, czyli
    dokładnie ten defekt, którego oba te ADR-y dotyczą."""
    identyfikator = str(id_z_tresci(nazwa="A", nazwisko="B", imie="C", data_rozpoczecia="D"))

    assert kanoniczny_id(identyfikator) == identyfikator


def test_rozdzielnik_nie_da_sie_przesunac_trescia_pola() -> None:
    """Uwaga z przeglądu 2026-09-09. `"|".join(...)` sklejał pola rozdzielnikiem, który jest
    **legalnym znakiem w nazwie z rejestru**, więc granica pól zależała od ich treści:
    `("A|B", "C", …)` i `("A", "B|C", …)` dawały ten sam skrót. ADR-0016 mierzył zero kolizji
    na krotce czterech pól, a kod liczył skrót ze sklejonego napisu — to nie to samo
    twierdzenie, i akurat tę różnicę steruje ten, kto wypełnia wpis w rejestrze."""
    lewy = id_z_tresci(nazwa="A|B", nazwisko="C", imie="D", data_rozpoczecia="1999-01-01")
    prawy = id_z_tresci(nazwa="A", nazwisko="B|C", imie="D", data_rozpoczecia="1999-01-01")

    assert lewy != prawy


def test_skrot_zalezy_od_wszystkich_czterech_pol() -> None:
    """Kontrola pozytywna: rozdzielnik odporny na kolizję nie może zgubić żadnego pola."""
    baza = {"nazwa": "A", "nazwisko": "B", "imie": "C", "data_rozpoczecia": "1999-01-01"}
    skroty = {str(id_z_tresci(**baza))}
    for pole in baza:
        skroty.add(str(id_z_tresci(**{**baza, pole: "ZMIENIONE"})))

    assert len(skroty) == 5


# ------------------------------------------------------------------ obserwator sklejenia


class NasluchEvents:
    """Atrapa `Events`, która zapamiętuje same zdania — sklejenie musi mieć obserwatora."""

    def __init__(self) -> None:
        self.komunikaty: list[str] = []

    def on_request(self, *a: Any, **k: Any) -> None: ...
    def on_page(self, *a: Any, **k: Any) -> None: ...
    def on_detail(self, *a: Any, **k: Any) -> None: ...
    def on_export(self, *a: Any, **k: Any) -> None: ...
    def on_download(self, *a: Any, **k: Any) -> None: ...
    def on_model(self, *a: Any, **k: Any) -> None: ...
    def on_wait(self, *a: Any, **k: Any) -> None: ...
    def close(self) -> None: ...

    def on_message(self, text: str) -> None:
        self.komunikaty.append(text)


def _archiwum(tmp_path: Path, wiersze: list[dict[str, str]]) -> Path:
    csv_text = ";".join(HEADER) + "\n" + "\n".join(";".join(r.values()) for r in wiersze) + "\n"
    sciezka = tmp_path / "src.zip"
    with zipfile.ZipFile(sciezka, "w") as z:
        z.writestr("Zarejestrowane działalności.csv", "﻿" + csv_text)
    return sciezka


RAPORT = Report(
    "r1",
    "Zarejestrowane działalności - województwo podlaskie",
    ".csv",
    f"{BASE}/raport/r1",
    "2026-09-04 06:40:59",
)


def test_sklejenie_dwoch_wierszy_bez_nipu_mowi_ze_jest_wnioskiem(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Dwie różne firmy o tych samych czterech polach zleją się w jedną. W zmierzonym
    archiwum takich par nie ma ani jednej, ale zero zmierzone raz nie jest zerem na zawsze —
    a sklejenie ciche byłoby dokładnie tą awarią, którą ten projekt znajduje u siebie
    najczęściej. Zdanie musi też powiedzieć, że jest **wnioskiem narzędzia**, a nie
    odczytem z rejestru; powtórzony NIP jest czym innym i ma własne zdanie."""
    wiersze = [
        row(Nip="", Regon="", NazwaPodmiotu="BLIŹNIAK", Miejscowosc="Białystok"),
        row(Nip="", Regon="", NazwaPodmiotu="BLIŹNIAK", Miejscowosc="Łomża"),
    ]
    api = FakeApi()
    archiwum = _archiwum(tmp_path, wiersze)
    api.fallback = lambda request: httpx.Response(200, content=archiwum.read_bytes())
    events = NasluchEvents()
    deps = build_deps(
        Settings(token="tok", environment="test", data_dir=tmp_path / "dane"),
        clock=clock,
        http=api.client(),
        events=events,  # type: ignore[arg-type]
    )

    wynik = run_report_fetch(criteria(wojewodztwo="podlaskie"), deps, RAPORT)
    deps.store.close()

    assert wynik.records == 1, "te same cztery pola tożsamości to jeden wpis"
    sklejenie = [k for k in events.komunikaty if "wniosek narzędzia" in k]
    assert sklejenie, f"sklejenie bez obserwatora; komunikaty: {events.komunikaty}"
    assert "NIP" not in sklejenie[0].split("bez NIP")[0], "to nie jest zdanie o powtórzonym NIP"
