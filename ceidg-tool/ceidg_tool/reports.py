"""Ścieżka raportów: ZIP z `/raport/{id}` → CSV → rekordy w kształcie API (`zrodlo=CEIDG_RAPORT`).

Raport „Zarejestrowane działalności - województwo X” to pełny dzienny zrzut województwa
(sonda 2026-09-05: 287 tys. wierszy, 24 kolumny, `;`, UTF-8 z BOM, kody PKD rozdzielone
`$##$`, statusy po polsku). Jeden raport zastępuje tysiące żądań `/firmy` dla zapytań
„region + okres”. Wiersze są mapowane na ten sam kształt JSON co odpowiedź API, więc
normalizer, store i eksporter nie odróżniają źródeł poza kolumną `zrodlo`.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
import zipfile
from collections.abc import Iterator, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from .criteria import Criteria, normalize_pkd
from .errors import ExportError
from .recordid import id_z_tresci
from .records import Report

KIND_REGISTERED = "Zarejestrowane działalności"
CSV_DELIMITER = ";"
CSV_ENCODING = "utf-8-sig"

_WOJ_RE = re.compile(r"województwo\s+(.+)$", re.IGNORECASE)
_PKD_RE = re.compile(r"^\d{4}[A-Z]$")

STATUS_TEXT_TO_API: dict[str, str] = {
    "AKTYWNY": "AKTYWNY",
    "ZAWIESZONY": "ZAWIESZONY",
    "WYKRESLONY": "WYKRESLONY",
    "DZIALALNOSC PROWADZONA WYLACZNIE W FORMIE SPOLKI CYWILNEJ": "WYLACZNIE_W_FORMIE_SPOLKI",
    "WYLACZNIE W FORMIE SPOLKI": "WYLACZNIE_W_FORMIE_SPOLKI",
    "OCZEKUJE NA ROZPOCZECIE DZIALALNOSCI": "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI",
    "NIE ROZPOCZAL DZIALALNOSCI": "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI",
}

COLUMNS_REQUIRED = ("Nip", "NazwaPodmiotu", "StatusDzialalnosci", "DataRozpoczeciaDzialalnosci")

# Statusy, których dzienny zrzut nie zawiera. **Zmierzone 2026-09-09** na
# `probe_out/raport_sample.zip` (287 256 wierszy, wielkopolskie, zero żądań): archiwum
# niesie dokładnie trzy wartości `StatusDzialalnosci` — „Aktywny" (76,52 %), „Zawieszony"
# (20,32 %) i „Działalność prowadzona wyłącznie w formie spółki cywilnej" (3,16 %).
# Ani jednego wiersza wykreślonego i ani jednego oczekującego na rozpoczęcie.
#
# Lista jest osobną stałą, a nie warunkiem w `report_covers`, bo mówi o **zawartości
# źródła**, nie o regule decyzyjnej — i bo `STATUS_TEXT_TO_API` niżej mapuje teksty, których
# ten sam pomiar w archiwum nie znalazł. To mapowanie zostaje (kosztuje nic, a rejestr może
# je kiedyś wyemitować), ale nie wolno go czytać jak dowodu, że raport te statusy zawiera.
STATUSY_SPOZA_RAPORTU: frozenset[str] = frozenset(
    {"WYKRESLONY", "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI"}
)

UNFILLED_COLUMNS: frozenset[str] = frozenset(
    {
        "terc",
        "simc",
        "ulic",
        "adres_korespondencyjny",
        "adres_doreczen_elektronicznych",
        "obywatelstwa",
        "wspolnosc_majatkowa",
        "data_wykreslenia",
        "pkd_glowny_nazwa",
        "pkd_nazwa",
        "link",
        "link_ceidg",
        # nie jest "puste", tylko sfabrykowane: raport nie niesie spolek cywilnych, wiec
        # `0` w kazdym wierszu bylo zdaniem, ktorego zrodlo nie umie powiedziec
        "liczba_spolek",
    }
)
"""Kolumny, których dzienny raport CSV **nie jest w stanie** wypełnić — nie „akurat puste",
tylko nieobecne w jego 24 kolumnach (`row_to_record` niżej pokazuje dokładnie, co mapuje).
`link_ceidg` dochodzi osobno: raport nie niesie identyfikatora wpisu, więc publiczny odnośnik
nie ma z czego powstać (ADR-0008, decyzja 5). Eksport chowa te kolumny zamiast pokazywać
kilkanaście pustych; schemat zostaje pełny, żeby oba źródła dawały ten sam układ.

Zbiór jest płaski, a `terc`, `simc` i `ulic` występują i w `Firmy`, i w `Adresy`. Dziś to
bezpieczne, bo `row_to_record` nie tworzy adresów dodatkowych, więc dla raportu arkusz `Adresy`
w ogóle nie powstaje. Gdyby raport zaczął je nieść, zbiór musi się rozdzielić na arkusze —
inaczej ukryłby kolumny, które nagle mają wartości."""


def strip_diacritics(text: str) -> str:
    return (
        "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
        .replace("ł", "l")
        .replace("Ł", "L")
    )


def status_to_api(text: str) -> str:
    """„Aktywny” → `AKTYWNY`; nieznany tekst wraca w postaci UPPER_SNAKE (nie gubimy informacji)."""
    key = re.sub(r"\s+", " ", strip_diacritics(text).strip()).upper()
    if key in STATUS_TEXT_TO_API:
        return STATUS_TEXT_TO_API[key]
    return key.replace(" ", "_")


def parse_report_name(nazwa: str) -> tuple[str, str | None]:
    """(rodzaj, województwo) z nazwy raportu; „brak województwa” → `None`."""
    head, _, tail = nazwa.partition(" - ")
    match = _WOJ_RE.search(tail)
    woj = match.group(1).strip().lower() if match else None
    return head.strip(), woj


def pick_registered_report(
    reports: Sequence[Report], wojewodztwo: str, *, fmt: str = ".csv"
) -> Report | None:
    """Najnowszy raport „Zarejestrowane działalności” dla województwa w danym formacie."""
    wanted = wojewodztwo.strip().lower()
    candidates = [
        r
        for r in reports
        if r.format.lower() == fmt and parse_report_name(r.nazwa) == (KIND_REGISTERED, wanted)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.utworzono)


# ----------------------------------------------------------------------------- CSV → rekord


def _split_pkd(raw: str) -> list[str]:
    codes: list[str] = []
    for piece in re.split(r"\$##\$|[;,|\s]+", raw or ""):
        piece = piece.strip().upper()
        if not piece:
            continue
        try:
            codes.append(normalize_pkd(piece))
        except ValueError:
            if _PKD_RE.fullmatch(piece):
                codes.append(piece)
    return codes


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def record_id_for(row: Mapping[str, str]) -> str:
    """Stabilny identyfikator wiersza raportu: NIP, w braku NIP — REGON, w ostateczności skrót.

    „Stabilny" było do 2026-09-09 nieprawdą dla trzeciej gałęzi: skrót liczył się ze
    wszystkich kolumn, w tym z `Lp.`, czyli z numeru porządkowego w pobraniu. Skład skrótu
    mieszka teraz w `recordid` razem z uzasadnieniem doboru pól (ADR-0016)."""
    nip = _blank_to_none(row.get("Nip"))
    if nip:
        return f"NIP:{nip}"
    regon = _blank_to_none(row.get("Regon"))
    if regon:
        return f"REGON:{regon}"
    return str(
        id_z_tresci(
            nazwa=row.get("NazwaPodmiotu"),
            nazwisko=row.get("Nazwisko"),
            imie=row.get("Imie"),
            data_rozpoczecia=row.get("DataRozpoczeciaDzialalnosci"),
        )
    )


def row_to_record(row: Mapping[str, str], *, wojewodztwo: str | None) -> dict[str, Any]:
    """Wiersz CSV → JSON w kształcie `/firma` (klucze API), by reszta potoku była wspólna."""
    glowny = _split_pkd(row.get("GlownyKodPkd", ""))
    pozostale = _split_pkd(row.get("PozostaleKodyPkd", ""))
    all_codes = list(dict.fromkeys(glowny + pozostale))
    record: dict[str, Any] = {
        "id": record_id_for(row),
        "nazwa": _blank_to_none(row.get("NazwaPodmiotu")),
        "wlasciciel": {
            "imie": _blank_to_none(row.get("Imie")),
            "nazwisko": _blank_to_none(row.get("Nazwisko")),
            "nip": _blank_to_none(row.get("Nip")),
            "regon": _blank_to_none(row.get("Regon")),
        },
        "adresDzialalnosci": {
            "ulica": _blank_to_none(row.get("Ulica")),
            "budynek": _blank_to_none(row.get("NrBudynku")),
            "lokal": _blank_to_none(row.get("NrLokalu")),
            "miasto": _blank_to_none(row.get("Miejscowosc")),
            "kod": _blank_to_none(row.get("KodPocztowy")),
            "gmina": _blank_to_none(row.get("Gmina")),
            "powiat": _blank_to_none(row.get("Powiat")),
            "wojewodztwo": wojewodztwo.upper() if wojewodztwo else None,
            "kraj": "PL",
        },
        "telefon": _blank_to_none(row.get("Telefon")),
        "email": _blank_to_none(row.get("Email")),
        "www": _blank_to_none(row.get("AdresWWW")),
        "rokPkd": _blank_to_none(row.get("RokPKD")),
        "pkdGlowny": {"kod": glowny[0]} if glowny else None,
        "pkd": [{"kod": code} for code in all_codes],
        "status": status_to_api(row.get("StatusDzialalnosci", "")),
        "dataRozpoczecia": _blank_to_none(row.get("DataRozpoczeciaDzialalnosci")),
        "dataZakonczenia": _blank_to_none(row.get("DataZakonczeniaDzialalnosci")),
        "dataZawieszenia": _blank_to_none(row.get("DataZawieszeniaDzialalnosci")),
        "dataWznowienia": _blank_to_none(row.get("DataWznowieniaDzialalnosci")),
    }
    cleaned: dict[str, Any] = _drop_none(record)
    return cleaned


def _drop_none(value: Any) -> Any:
    """API pomija brakujące pola zamiast wysyłać null — raport ma zachowywać się tak samo."""
    if isinstance(value, dict):
        return {k: _drop_none(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_drop_none(v) for v in value]
    return value


def iter_report_rows(zip_path: Path) -> Iterator[dict[str, str]]:
    """Wiersze pierwszego pliku CSV w archiwum, strumieniowo (68 MB CSV nie idzie do pamięci)."""
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ExportError(f"Raport {zip_path} nie jest poprawnym archiwum ZIP: {exc}") from exc
    with archive:
        members = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise ExportError(f"Raport {zip_path} nie zawiera pliku CSV: {archive.namelist()}")
        with archive.open(members[0]) as raw:
            text = io.TextIOWrapper(raw, encoding=CSV_ENCODING, newline="")
            reader = csv.DictReader(text, delimiter=CSV_DELIMITER)
            header = reader.fieldnames or []
            missing = [c for c in COLUMNS_REQUIRED if c not in header]
            if missing:
                raise ExportError(
                    f"Raport {members[0]} nie ma oczekiwanych kolumn {missing}; "
                    f"nagłówek: {header[:8]}…"
                )
            for row in reader:
                yield {k: (v or "") for k, v in row.items() if k is not None}


# ----------------------------------------------------------------------------- filtr lokalny


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _contains_ci(haystack: Any, needle: str) -> bool:
    return needle.casefold() in str(haystack or "").casefold()


def _equals_ci(value: Any, wanted: str) -> bool:
    return str(value or "").casefold() == wanted.casefold()


def matches_criteria(record: Mapping[str, Any], criteria: Criteria) -> bool:
    """Lokalny odpowiednik filtrów `/firmy` dla rekordu z raportu.

    **Odpowiednik jest dobry dokładnie tam, gdzie semantykę serwera zmierzono.** Audyt
    2026-09-08 (pozycja F12) zauważył, że siedem pól porównywano tu dokładnie, choć nikt nie
    sprawdził, jak porównuje je API — a rozjazd oznacza, że ścieżka raportu cicho zwraca inny
    zbiór niż ścieżka API, przy komentarzu zapewniającym, że zbiory są te same.

    Stan wiedzy na 2026-09-09, pole po polu:

    * `nazwa` — **fragment**, nieczułe na wielkość liter (`adam` = `ADAM` = 82 954 trafienia,
      `docs/decisions.md`). Stąd `_contains_ci`.
    * `miasto` — **fragment**, rozstrzygnięte za zero żądań z bazy operatora. Run `eb1df3a8`
      z filtrem `miasto=['Łomża']` zwrócił cztery wpisy z miejscowości `Stara Łomża przy
      Szosie` i `Stara Łomża nad Rzeką`. Sprawdzone też, że żaden z tych wpisów nie ma
      „Łomża" w adresie korespondencyjnym ani w żadnym innym — więc dopasowanie nie mogło
      pójść inną drogą. Do 2026-09-09 stało tu `_equals_ci`, czyli ścieżka raportu **gubiła**
      te wpisy.
    * `kod` — pytanie nie powstaje: `Criteria` waliduje kod pocztowy do postaci `15-333`, więc
      narzędzie nie potrafi wysłać fragmentu, a przy stałej długości „zawiera" i „równa się"
      pokrywają się.
    * `nip`, `regon`, `status`, `pkd`, daty — wartości ze słownika albo znormalizowane;
      porównanie dokładne jest tu tym, o co pyta wywołujący.
    * `budynek`, `lokal` — **niezmierzone i dokładne z wyboru.** Dokumentacja publicznej
      wyszukiwarki każe podać „pełny numer nieruchomości" i „pełny numer lokalu", więc
      dokładne porównanie jest tu zgodne z jedynym opisem, jaki istnieje; fragment
      dopasowywałby „12" do „112" i „12A", co przy numerze jest raczej pomyłką niż pomocą.
      To wybór, nie pomiar — gdyby kiedyś padło pytanie „czemu ten adres nie wchodzi",
      zaczyna się od tego zdania.
    * `nip_sc`, `regon_sc` — **tutaj nie docierają.** Dzienny zrzut nie ma takiej kolumny
      (nagłówek zmierzony 2026-09-10), więc `report_covers` odrzuca całą ścieżkę, a
      `pipeline.run_report_fetch` odmawia drugi raz, po swojej stronie bramki.
    * `imie`, `nazwisko` — **zmierzone 2026-09-10: dopasowanie dokładne.** Para różniąca się samym
      obcięciem wartości przy identycznej reszcie kryteriów: `nazwisko=Nowak` dała 23 trafienia,
      `nazwisko=Nowa` zero; `imie=Marek` 27, `imie=Mare` zero. Porównanie przez `_equals_ci` stało
      tu wcześniej jako domysł i **trafiło** — od tej daty stoi jako pomiar.
    * `powiat`, `gmina`, `ulica` — **niezmierzone i zostają dokładne.**
      Sonda z 2026-09-09 (`scripts/ceidg_probe_match_semantics.py`, dwa żądania produkcyjne)
      wysłała fragmenty ze środka prawdziwych wartości w dwóch grupach i obie wróciły puste
      (HTTP 204). To znaczy tylko tyle, że **co najmniej jedno pole w każdej grupie** nie
      dopasowuje fragmentem; które — tego te dwa żądania nie mówią. Wniosek praktyczny jest
      za to mocny i idzie pod prąd intuicji: **rodzina pól tekstowych nie jest jednorodna**,
      więc semantyki jednego pola nie wolno przenosić na sąsiednie. Dokładnie to założenie
      trzymało tu `_equals_ci` przy `miasto`.
    """
    owner = record.get("wlasciciel") or {}
    address = record.get("adresDzialalnosci") or {}
    if criteria.nip and owner.get("nip") not in criteria.nip:
        return False
    if criteria.regon and owner.get("regon") not in criteria.regon:
        return False
    if criteria.status and record.get("status") not in criteria.status:
        return False
    if criteria.nazwa and not any(_contains_ci(record.get("nazwa"), n) for n in criteria.nazwa):
        return False
    if criteria.imie and not any(_equals_ci(owner.get("imie"), v) for v in criteria.imie):
        return False
    if criteria.nazwisko and not any(
        _equals_ci(owner.get("nazwisko"), v) for v in criteria.nazwisko
    ):
        return False
    # `miasto` osobno, bo jako jedyne z tej piątki zostało zmierzone — i wyszło fragmentem.
    if criteria.miasto and not any(_contains_ci(address.get("miasto"), v) for v in criteria.miasto):
        return False
    for field_name, key in (
        ("powiat", "powiat"),
        ("gmina", "gmina"),
        ("kod", "kod"),
        ("ulica", "ulica"),
        ("budynek", "budynek"),
        ("lokal", "lokal"),
    ):
        wanted = getattr(criteria, field_name)
        if wanted and not any(_equals_ci(address.get(key), v) for v in wanted):
            return False
    if criteria.wojewodztwo and not any(
        _equals_ci(address.get("wojewodztwo"), v) for v in criteria.wojewodztwo
    ):
        return False
    # Oba pola PKD razem, dokładnie jak w `to_params`: ścieżka raportowa ma zwracać ten sam
    # zbiór co API, inaczej „raport zamiast żądań" przestaje być wyborem obojętnym dla wyniku.
    # „Ma", a nie „zwraca": dla pięciu pól wymienionych w docstringu równość jest założeniem,
    # nie pomiarem, i zdanie w trybie oznajmującym było właśnie tym, co audyt zgłosił jako F12.
    # Tu rozszerzenie o rocznik 2007 nie kosztuje ani jednego żądania — filtrujemy lokalnie.
    szukane = criteria.wszystkie_pkd()
    if szukane:
        codes = {str(p.get("kod", "")).upper() for p in record.get("pkd") or []}
        if not codes & set(szukane):
            return False
    started = _parse_date(record.get("dataRozpoczecia"))
    if criteria.data_od and (started is None or started < criteria.data_od):
        return False
    if criteria.data_do and (started is None or started > criteria.data_do):
        return False
    return True


def statusy_poza_raportem(criteria: Criteria) -> tuple[str, ...]:
    """Żądane statusy, których dzienny zrzut w ogóle nie zawiera — posortowane, do komunikatu."""
    return tuple(sorted(STATUSY_SPOZA_RAPORTU.intersection(criteria.status)))


def filtry_poza_raportem(criteria: Criteria) -> tuple[str, ...]:
    """Żądane filtry, których dzienny zrzut nie ma **jako kolumny** — posortowane, do komunikatu.

    Zmierzone 2026-09-10 na nagłówku `probe_out/raport_sample.zip`: archiwum ma 24 kolumny
    (`Lp.`, `Nip`, `Regon`, `NazwaPodmiotu`, `Nazwisko`, `Imie`, kontakt, adres z `NrBudynku`
    i `NrLokalu`, PKD, status, cztery daty) i **ani jednej o spółce cywilnej**. Filtr, którego
    kolumny nie ma, nie odsiewa niczego — odsiewa wszystko, bo porównanie z brakiem zawsze
    wypada fałszywie. To jest ta sama cicha pustka co przy statusach spoza zrzutu (A10),
    tylko wejściem przez adres zamiast przez status."""
    poza: list[str] = []
    if criteria.nip_sc:
        poza.append("nip_sc")
    if criteria.regon_sc:
        poza.append("regon_sc")
    return tuple(poza)


def report_covers(criteria: Criteria) -> bool:
    """Raport pokrywa zapytanie, gdy jest dokładnie jedno województwo, żaden z żądanych
    statusów nie leży poza zrzutem i żaden filtr nie odwołuje się do kolumny, której zrzut nie ma.

    Do audytu 2026-09-08 (A10) warunek wymieniał wyłącznie `WYKRESLONY`, więc zapytanie
    o wpisy oczekujące na rozpoczęcie działalności szło ścieżką raportu i wracało puste —
    bez błędu, bez ostrzeżenia i o cztery rzędy wielkości taniej niż ścieżka API, co czyni
    tę cichą pustkę wyborem domyślnym (`--zrodlo auto`). Filtry spółki cywilnej dokładają
    2026-09-10 drugi przypadek tego samego kształtu: kolumny nie ma w ogóle."""
    if statusy_poza_raportem(criteria):
        return False
    if filtry_poza_raportem(criteria):
        return False
    return len(criteria.wojewodztwo) == 1
