"""Wszystkie komunikaty dla użytkownika jako modele widoku — moduł czysty.

Reguła granic 6 (ADR-0008): bez `rich`, `typer`, `questionary`, `httpx`, `sqlite3`
i `openpyxl`. Dzięki temu każdy ekran da się sprawdzić testem bez terminala, a flagi CLI,
plik YAML, tryb `--tak` i kreator pokazują dosłownie te same zdania.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from ..batching import GRANULARITY_LABEL, BatchPlan
from ..config import TOKEN_SERVICE_URL, Settings
from ..criteria import STATUSY, WOJEWODZTWA, Criteria
from ..estimating import Estimate
from ..normalizer import NormalizedRecord
from ..records import Report

PROGRAM_PURPOSE = "pobiera dane o jednoosobowych działalnościach z API CEIDG do Excela"
PROD_HINT = "produkcja: dodaj --srodowisko prod --produkcja (prawdziwe dane osobowe)"


@dataclass(frozen=True)
class Block:
    """Jeden ekran albo jedna tabela. `headers` puste = blok klucz-wartość."""

    title: str
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    notes: tuple[str, ...] = ()

    def as_text(self) -> str:
        """Postać tekstowa — do logu, do trybu cichego i do asercji w testach."""
        lines = [self.title] if self.title else []
        if self.headers:
            lines.append(" | ".join(self.headers))
        lines.extend(" | ".join(row) for row in self.rows)
        lines.extend(self.notes)
        return "\n".join(lines)


@dataclass(frozen=True)
class MenuItem:
    key: str
    label: str
    hint: str = ""


def format_number(value: int) -> str:
    """Spacja jako separator tysięcy — polski zapis, bez zależności od ustawień lokalnych."""
    return f"{value:,}".replace(",", " ")


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes = seconds / 60
    if minutes < 90:
        return f"{minutes:.0f} min"
    return f"{minutes / 60:.1f} h"


def format_size(path: Path) -> str:
    try:
        kib = path.stat().st_size / 1024
    except OSError:
        return "brak pliku"
    return f"{kib:,.0f} KB".replace(",", " ")


# ----------------------------------------------------------------------------- ekran startowy


def first_screen(settings: Settings, *, now: datetime, version: str) -> Block:
    """Pierwszy ekran wg UZUPELNIENIE_01 §A: co robi, dokąd wysyła, gdzie pracuje, jaki token."""
    environment = "PRODUKCJA (prawdziwe dane osobowe)" if settings.environment == "prod" else "TEST"
    host = "dane.biznes.gov.pl" if settings.environment == "prod" else "test-dane.biznes.gov.pl"
    rows = (
        ("co robi", PROGRAM_PURPOSE),
        ("dokąd wysyłam rekordy", f"wyłącznie do API CEIDG ({host}); brak telemetrii"),
        ("dokąd wysyła asystent", _assistant_destination(settings)),
        ("środowisko", environment),
        ("token", f"{settings.token_info.validity_text(now)} (źródło: {settings.token_source})"),
        ("dane i wyniki", str(settings.data_dir)),
    )
    notes = [f"Nowy token: {TOKEN_SERVICE_URL}"]
    if settings.environment != "prod":
        notes.append(PROD_HINT)
    return Block(title=f"ceidg-tool {version}", rows=rows, notes=tuple(notes))


def _assistant_destination(settings: Settings) -> str:
    """Wiersz §A o asystencie — rozdzielony od wiersza o rekordach, bo cele są różne.

    Do 2026-09-07 pierwszy ekran mówił „wyłącznie do API CEIDG". To jest kryterium odbioru §A
    i przestawało być prawdą w chwili użycia asystenta, więc wiersz musiał się rozdwoić:
    rekordy idą wyłącznie do CEIDG, a do modelu — treść pytania i słownik PKD, nigdy rekordy.
    """
    if settings.anthropic_key is None:
        return "asystent wyłączony (brak klucza: `ceidg-tool token zapisz --asystent`)"
    return "treść Twojego pytania i słownik PKD do api.anthropic.com; pobrane rekordy — nigdy"


OGRANICZENIA_ZDANIA: Final[dict[str, str]] = {
    "SPOLKI_W_KRS": "Spółki (z o.o., akcyjne, jawne) są w KRS — tu znajdziesz tylko JDG.",
    "BRAK_DANYCH_FINANSOWYCH": "Rejestr nie ma przychodów, zysków ani liczby pracowników.",
    "DATA_TYLKO_ROZPOCZECIE": "Filtr dat działa wyłącznie dla daty rozpoczęcia działalności.",
    "KONTAKTY_OPCJONALNE": "Telefon, e-mail i WWW są dobrowolne, więc bywają puste.",
    "BRAK_FILTRA_WIELKOSCI": "Nie da się filtrować po wielkości firmy.",
    "BRAK_FILTRA_BRANZY_POZA_PKD": "Branżę wyraża wyłącznie kod PKD; nie ma filtra słownego.",
    "TYLKO_JDG": "Rejestr obejmuje wyłącznie jednoosobowe działalności gospodarcze.",
    "RAPORT_BEZ_WYKRESLONYCH": "Gotowy raport dzienny nie zawiera wpisów wykreślonych.",
    "KOD_PKD_Z_INNEGO_ROCZNIKA": (
        "Podany przez Ciebie kod PKD pochodzi ze starszej klasyfikacji i dziś nie istnieje — "
        "powyżej jest jego odpowiednik z PKD 2025. Sprawdź, czy nazwa się zgadza."
    ),
}

ASSISTANT_THINKING: Final = (
    "Pytam asystenta o interpretację zdania — zwykle trwa to kilkanaście sekund. "
    "Licznik poniżej pokazuje upływ czasu; do rejestru nie idzie jeszcze żadne zapytanie."
)

ASYSTENT_KOSZT: Final = (
    "asystent: jedno pytanie to ok. 13 tys. tokenów, ułamek grosza; nie wysyła pobranych rekordów"
)


def interpretation(
    opis: str,
    kryteria_opis: str,
    kody_pkd: Sequence[tuple[str, str]],
    ograniczenia: Sequence[str],
) -> Block:
    """Ekran potwierdzenia interpretacji — **układany przez kod, nie przez model**.

    Instrukcja fazy 4 wymaga tego wprost i jest to jedyny tekst, na podstawie którego operator
    działa. Model dostarcza wyłącznie pola i kody; nazwy PKD pochodzą ze **słownika lokalnego**,
    a zdania o ograniczeniach z katalogu wyżej. Dzięki temu ekran da się asertować w testach bez
    modelu — i dzięki temu zły kod czyta się jako zła branża, a nie jako niewidoczna literówka.
    """
    rows: list[tuple[str, str]] = [
        ("Twoje zdanie", opis),
        ("rozumiem jako", kryteria_opis),
    ]
    if kody_pkd:
        rows.append(("PKD", "; ".join(f"{kod} — {nazwa}" for kod, nazwa in kody_pkd)))
    notes = [OGRANICZENIA_ZDANIA[kod] for kod in ograniczenia if kod in OGRANICZENIA_ZDANIA]
    notes.append(ASYSTENT_KOSZT)
    return Block(title="Interpretacja", rows=tuple(rows), notes=tuple(notes))


def token_summary(settings: Settings, *, now: datetime) -> str:
    """`sprawdz-token`: środowisko, źródło i ważność. Nigdy sama wartość tokenu.

    Zwykłe linie, nie tabela: ten wynik bywa czytany skryptem, a `Skrót:` porównywany
    z wpisem w `request_log`."""
    return (
        f"Środowisko: {settings.environment}\n"
        f"Źródło tokenu: {settings.token_source}\n"
        f"Token: {settings.token_info.validity_text(now)}\n"
        f"Skrót: {settings.token_fp}\n"
        f"Asystent: {_assistant_line(settings)}"
    )


def _assistant_line(settings: Settings) -> str:
    """Stan klucza asystenta: jest albo go nie ma. **Nigdy wartość** — tylko odcisk.

    Brak klucza jest normalnym stanem, nie usterką, więc zdanie mówi też, co z tym zrobić:
    operator ma dowiedzieć się, dlaczego kreator nie proponuje opisu zdaniem."""
    if settings.anthropic_key is None:
        return "brak klucza — asystent wyłączony (`ceidg-tool token zapisz --asystent`)"
    return f"klucz z {settings.anthropic_key_source}, skrót {settings.assistant_key_fp}"


def menu_block(items: Sequence[MenuItem]) -> Block:
    rows = tuple((str(i), item.label, item.hint) for i, item in enumerate(items, start=1))
    return Block(title="Co chcesz zrobić?", headers=("nr", "działanie", "uwagi"), rows=rows)


def criteria_block(criteria: Criteria) -> Block:
    return Block(title="Kryteria", rows=(("wybrane", criteria.describe()),))


def hints_block() -> Block:
    """Podpowiedzi do pól, których nie da się zgadnąć — zbiory zamknięte z `criteria`."""
    return Block(
        title="Dozwolone wartości",
        rows=(
            ("województwo", ", ".join(WOJEWODZTWA)),
            ("status", ", ".join(STATUSY)),
            # Przykład musi być kodem **żywym w PKD 2025**: do 2026-09-07 stało tu
            # `62.01.Z`, którego w tej klasyfikacji nie ma i którego asystent odmawia —
            # kreator uczył więc operatora kodu odrzucanego przez własne narzędzie.
            ("PKD", "62.10.B albo 6210B"),
            ("daty", "RRRR-MM-DD, filtr dotyczy daty rozpoczęcia działalności"),
        ),
    )


# ----------------------------------------------------------------------------- koszty


def cost_table(est: Estimate, *, threshold: int, capped: bool = False) -> Block:
    """Tabela kosztów wg UZUPELNIENIE_01 §A: zakres, liczba zapytań, czas, zawartość.

    Ścieżka raportu ma własny blok (`report_offer`), bo pada przed zapytaniem o `count`."""
    rows: list[tuple[str, ...]] = []
    rows.append(
        (
            "lista podstawowa",
            str(est.requests_list),
            format_duration(est.seconds_list),
            "nazwa, NIP, REGON, adres działalności, status, data rozpoczęcia",
        )
    )
    rows.append(
        (
            "z pełnymi szczegółami",
            str(est.requests_list + est.requests_details),
            format_duration(est.seconds_total),
            "dodatkowo PKD, telefon, e-mail, spółki, adres korespondencyjny",
        )
    )
    notes: list[str] = []
    if est.count > threshold and not capped:
        notes.append(
            f"Trafień jest więcej niż {format_number(threshold)} — program nie zacznie "
            "pobierania sam. Zawęź kryteria albo podziel je na partie."
        )
    elif est.count > threshold:
        notes.append(
            f"Trafień jest więcej niż {format_number(threshold)}, ale limit rekordów jest "
            "ustawiony, więc pobranie obejmie tylko pierwsze z nich."
        )
    return Block(
        title=f"Znaleziono {format_number(est.count)} firm",
        headers=("zakres danych", "liczba zapytań", "szacowany czas", "zawartość"),
        rows=tuple(rows),
        notes=tuple(notes),
    )


def estimate_text(est: Estimate) -> str:
    """Zwięzła wersja wyceny do logu i do trybu nieinteraktywnego."""
    return "\n".join(
        (
            f"Znaleziono {format_number(est.count)} firm.",
            f"Lista podstawowa: ~{est.requests_list} zapytań, "
            f"ok. {format_duration(est.seconds_list)}.",
            f"Z pełnymi szczegółami: ~{est.requests_list + est.requests_details} zapytań, "
            f"ok. {format_duration(est.seconds_total)}.",
        )
    )


def update_cost_table(
    *,
    count: int,
    requests: int,
    seconds: float,
    since: datetime,
    until: datetime,
    windows: int,
) -> Block:
    """Koszt aktualizacji przed jej rozpoczęciem — ta sama rola co `cost_table` przy pobieraniu.

    `aktualizuj` ruszał bez pytania, bo wyglądało na to, że liczbę zmian poznaje dopiero
    z pierwszej strony. Odpowiedź `/zmiana` niesie jednak `count` całego zakresu, więc jedno
    tanie żądanie na okno wystarcza, żeby operator zobaczył trzydzieści minut pracy **zanim**
    się zaczną (przechodzenie bramki 3, 2026-09-06)."""
    rows = [
        ("zakres zmian", f"{since:%Y-%m-%d %H:%M} – {until:%Y-%m-%d %H:%M} UTC"),
        ("zmienionych wpisów", format_number(count)),
        ("zapytań", f"do {format_number(requests)}"),
        ("szacowany czas", f"do {format_duration(seconds)}"),
    ]
    if windows > 1:
        rows.append(("okien po 5 dni", str(windows)))
    notes = [
        "Szczegóły pobierane są tylko dla wpisów, których nie ma świeżych w bazie, więc "
        "realny koszt bywa niższy od podanego.",
    ]
    if count == 0:
        notes = ["Nic się nie zmieniło od ostatniej aktualizacji — nie ma czego pobierać."]
    return Block(title="Aktualizacja bazy o zmiany", rows=tuple(rows), notes=tuple(notes))


def split_table(
    plan: BatchPlan, estimates: Sequence[Estimate], *, szczegoly: bool = False
) -> Block:
    """Propozycja podziału: jedna partia na wiersz, czasy szacowane przy równym rozłożeniu.

    Koszt liczony dla trybu, który jest w kryteriach — inaczej tabela straszyłaby kosztem
    szczegółów kogoś, kto chce samą listę."""

    def requests(est: Estimate) -> int:
        return est.requests_list + (est.requests_details if szczegoly else 0)

    def seconds(est: Estimate) -> float:
        return est.seconds_total if szczegoly else est.seconds_list

    rows = tuple(
        (
            batch.label,
            f"{batch.od.isoformat()} – {batch.do.isoformat()}",
            f"~{format_number(est.count)}",
            str(requests(est)),
            format_duration(seconds(est)),
        )
        for batch, est in zip(plan.batches, estimates, strict=True)
    )
    total_requests = sum(requests(e) for e in estimates)
    total_seconds = sum(seconds(e) for e in estimates)
    mode = "z pełnymi szczegółami" if szczegoly else "sama lista podstawowa"
    notes = [
        f"Koszt policzony dla trybu: {mode}. Liczby w partiach są szacunkiem przy równym "
        "rozłożeniu w czasie; przed każdą partią program sprawdza jej rzeczywisty `count` "
        "jednym zapytaniem.",
        f"Razem: {total_requests} zapytań, ok. {format_duration(total_seconds)}. "
        "Każda partia ma własny checkpoint, więc przerwaną pracę da się wznowić.",
    ]
    if plan.exhausted:
        notes.append(
            "Nawet partie miesięczne nie schodzą poniżej progu — rozważ zawężenie kryteriów "
            "województwem, kodem PKD albo statusem."
        )
    return Block(
        title=f"Propozycja podziału {GRANULARITY_LABEL[plan.granularity]} "
        f"({len(plan.batches)} partii, {plan.covers[0]} – {plan.covers[1]})",
        headers=("partia", "zakres dat", "szacowane trafienia", "zapytania", "czas"),
        rows=rows,
        notes=tuple(notes),
    )


# ----------------------------------------------------------------------------- podsumowanie


@dataclass(frozen=True)
class SummaryInput:
    """Dane do podsumowania końcowego — zebrane przez `flow`, żeby `texts` pozostało czyste."""

    paths: tuple[Path, ...]
    records: int
    by_status: dict[str, int]
    with_phone: int
    with_email: int
    sheets: tuple[str, ...]
    kind: str
    run_ids: tuple[str, ...]
    log_path: Path
    extra: tuple[tuple[str, str], ...] = field(default=())


def summary_table(summary: SummaryInput) -> Block:
    """Podsumowanie wg §A: plik i rozmiar, firmy wg statusu, odsetek kontaktów, arkusze, log."""
    rows: list[tuple[str, ...]] = [
        ("plik", f"{path} ({format_size(path)})") for path in summary.paths
    ]
    rows.append(("firm", format_number(summary.records)))
    for status, count in summary.by_status.items():
        rows.append((f"  {status}", format_number(count)))
    if summary.records:
        rows.append(("z telefonem", _share(summary.with_phone, summary.records)))
        rows.append(("z e-mailem", _share(summary.with_email, summary.records)))
    rows.append(("arkusze", ", ".join(summary.sheets)))
    rows.append(("log", str(summary.log_path)))
    rows.append(("run_id", ", ".join(summary.run_ids)))
    rows.extend(summary.extra)
    return Block(title="Podsumowanie", rows=tuple(rows), notes=summary_notes(summary))


def _share(part: int, total: int) -> str:
    return f"{format_number(part)} ({100 * part / total:.0f}%)"


def summary_notes(summary: SummaryInput) -> tuple[str, ...]:
    """Zdanie o kolumnie `link_ceidg` zależy od źródła danych, nie od stanu bazy."""
    if summary.kind == "raport":
        return (
            "Rekordy z raportu nie mają identyfikatora wpisu, więc kolumna link_ceidg jest "
            "pusta; do weryfikacji użyj NIP w wyszukiwarce CEIDG.",
            # Wyjaśnienie było tylko w arkuszu `Metadane`, którego operator bez wiedzy o API
            # może nigdy nie otworzyć. W Excelu zobaczy przeskakujące litery kolumn i uzna,
            # że plik jest niepełny — a to jest ekran, który czyta na pewno.
            "Kolumn, których dzienny raport nie zawiera (m.in. TERC, SIMC, adres "
            "korespondencyjny, nazwy PKD), skoroszyt nie pokazuje. Są w pliku — w Excelu "
            "przywraca je „Odkryj”, a arkusz Metadane wymienia je w wierszu kolumny_ukryte.",
        )
    return (
        "Kolumna link_ceidg w arkuszu Firmy prowadzi do wpisu w publicznej wyszukiwarce "
        "CEIDG — do ręcznej weryfikacji.",
    )


def runs_table(rows: Sequence[tuple[str, ...]]) -> Block:
    """Lista pobrań z bazy: `run_id`, stan, tryb, liczby, data i skrót kryteriów."""
    return Block(
        title="Pobrania w bazie",
        headers=("run_id", "status", "tryb", "rekordy", "count", "utworzono", "kryteria"),
        rows=tuple(rows),
    )


def reports_table(reports: Sequence[Report]) -> Block:
    """Gotowe raporty CEIDG. Nazwa pochodzi z API, więc przechodzi przez neutralizację."""
    return Block(
        title="Gotowe raporty CEIDG",
        headers=("nazwa", "format", "utworzono", "id"),
        rows=tuple(
            (report.nazwa, report.format, report.utworzono, report.id)
            for report in sorted(reports, key=lambda r: (r.nazwa, r.utworzono))
        ),
        notes=(f"Raportów: {len(reports)}",),
    )


def report_offer(report: Report) -> Block:
    """Oferta ścieżki raportu: koszt i to, czego w raporcie nie ma."""
    return Block(
        title="Dostępny raport dzienny",
        rows=(
            ("nazwa", report.nazwa),
            ("utworzony", report.utworzono),
            ("koszt", "2 zapytania zamiast tysięcy"),
            (
                "czego brakuje",
                "firm WYKREŚLONYCH, adresu korespondencyjnego, obywatelstw i spółek",
            ),
        ),
    )


def _wiersz_dzis(nazwa: str, dzis: str) -> str:
    """Zdanie o dzisiejszym znaczeniu dokładanego kodu — dwa warianty, bo dwie sytuacje.

    Gdy nazwa się zmieniła, trzeba ją pokazać: `8551Z` to była „Pozaszkolna edukacja sportowa",
    a dziś jest „Pozostała edukacja sportowa". Gdy się nie zmieniła — jak przy `1086Z` — to samo
    zdanie powtarzało dosłownie treść kolumny obok i czytało się jak usterka programu.
    Wyszło dopiero przy wyrenderowaniu ekranu przez `rich`; model widoku wyglądał poprawnie.
    """
    if dzis == nazwa:
        return "…ten kod jest nadal w użyciu, więc dojdą też dzisiejsze firmy z tym kodem"
    return f"…ten kod istnieje też dziś i znaczy: {dzis}"


def resume_offer(run_id: str, status: str, rekordow: int, opis_kryteriow: str) -> str:
    """Propozycja wznowienia — z **opisem kryteriów znalezionego przebiegu**.

    Zdanie mówiło „z tymi kryteriami" i przestało być prawdą, gdy wznowienia zaczęto szukać
    dla trzech postaci kryteriów naraz (ADR-0012): trafienie może pochodzić z populacji innej
    niż zamówiona, a różnica bywa kilkukrotna. Pytanie ma domyślną odpowiedź „tak", więc
    operator musi widzieć, **co** wznawia, zanim naciśnie Enter. Argumenty są typami prostymi,
    bo `ui/texts.py` nie zna `store` ani `RunInfo`.
    """
    return (
        f"W bazie jest niedokończone pobranie {run_id} ({status}, {rekordow} rekordów). "
        f"Jego kryteria: {opis_kryteriow}"
    )


def vintage_conflict(kody: Sequence[str]) -> str:
    """Plik zapytania mówi jedno, flaga drugie — program nie zgaduje, które ważniejsze."""
    return (
        f"Plik zapytania zawiera stare kody PKD ({', '.join(kody)}), a --bez-pkd-2007 każe ich "
        "nie używać. Nie zgaduję, które ma wygrać: usuń pole pkd_2007 z pliku albo pomiń flagę."
    )


def vintage_skipped(kody: Sequence[str]) -> str:
    """Jedno zdanie o tym, czego zapytanie nie obejmie — dla przebiegów bez pytania.

    Pada tylko wtedy, gdy rozszerzenie **istniało** i zostało pominięte, więc nie jest
    ostrzeżeniem zawsze prawdziwym, które uczy operatora je ignorować.
    """
    return (
        f"Pomijam stare kody PKD ({', '.join(kody)}), więc wynik nie obejmie firm, które nie "
        "przeszły jeszcze na PKD 2025. Użyj --pkd-2007, żeby je dołączyć."
    )


def vintage_offer(
    poprzednicy: Sequence[tuple[str, str, Sequence[tuple[str, str]], str]],
    *,
    waskie: int | None = None,
    szerokie: int | None = None,
) -> Block:
    """Ekran wyboru rocznika PKD: co dołożymy, co to wciągnie i ile to jest firm.

    Zdanie pisze kod, nie model i nie API — jak wszystko, co operator czyta. Liczby są
    opcjonalne, bo na ścieżce raportowej nie znamy ich przed pobraniem archiwum, a wymyślenie
    ich byłoby gorsze niż ich brak: cała wartość tego ekranu polega na tym, że jest mierzony.
    """
    rows: list[tuple[str, ...]] = []
    for kod, nazwa, rowniez, dzis in poprzednicy:
        rows.append((kod, nazwa))
        if rowniez:
            obce = "; ".join(f"{k} {n}" for k, n in rowniez)
            rows.append(("", f"…ale obejmuje też: {obce}"))
        if dzis:
            # Ten sam łańcuch jest nadal w użyciu, więc dołożenie go bierze także firmy, które
            # mają ten kod jako dzisiejszy. Osobne zdanie, bo to inny rodzaj poszerzenia niż
            # „prowadzi też do innego kodu" i inaczej się go czyta.
            rows.append(("", _wiersz_dzis(nazwa, dzis)))
    notes = [
        "Rejestr jest w trakcie przejścia na PKD 2025 i potrwa ono do 31.12.2026.",
        "Firmy, które jeszcze nie przeszły, mają w rejestrze stary kod — filtr ich nie widzi.",
    ]
    if waskie is not None and szerokie is not None:
        notes.append(
            f"Tylko PKD 2025: {format_number(waskie)} firm. "
            f"Ze starymi kodami: {format_number(szerokie)} firm."
        )
        # Różnicę podajemy wprost, bo to jest liczba, o którą naprawdę toczy się wybór.
        # Sam iloraz („3x") brzmi abstrakcyjnie przy dwóch liczbach czytanych po raz pierwszy.
        if szerokie > waskie:
            notes.append(f"Różnica to {format_number(szerokie - waskie)} firm.")
    else:
        notes.append(
            "Ile ich jest, wiadomo dopiero po pobraniu raportu — ta ścieżka filtruje lokalnie, "
            "więc szerszy wybór nie kosztuje ani jednego dodatkowego zapytania."
        )
    return Block(
        title="Stare kody PKD — poszerzyć wyszukiwanie?",
        headers=("kod PKD 2007", "co obejmuje"),
        rows=tuple(rows),
        notes=tuple(notes),
    )


def vintage_applied(
    poprzednicy: Sequence[tuple[str, str, Sequence[tuple[str, str]], str]],
) -> Block:
    """Rozszerzenie zastosowane bez pytania — ale nigdy bez pokazania.

    Ciche poszerzenie zapytania jest tym samym defektem co cicha podmiana kodu przez model
    (przebieg A5): operator widzi wtedy wynik, którego nie potrafi wytłumaczyć. Dotyczy to obu
    dróg, którymi rozszerzenie wchodzi bez pytania — czystego dokładania i jawnej flagi
    `--pkd-2007`. Ta druga była cicha do 2026-09-07, mimo komentarza mówiącego wprost, że nie
    wolno jej taką zostawić; flaga wyraża zgodę na dołożenie kodów, nie na nieoglądanie ich.
    """
    rows: list[tuple[str, ...]] = []
    szersze = False
    for kod, nazwa, rowniez, dzis in poprzednicy:
        rows.append((kod, nazwa))
        if rowniez:
            szersze = True
            obce = "; ".join(f"{k} {n}" for k, n in rowniez)
            rows.append(("", f"…obejmuje też: {obce}"))
        if dzis:
            szersze = True
            rows.append(("", _wiersz_dzis(nazwa, dzis)))
    notes = ["Bez nich zapytanie pomija firmy, które nie przeszły jeszcze na PKD 2025."]
    if szersze:
        notes.insert(
            0,
            "Część z nich obejmuje szerzej niż pytanie — w wyniku znajdą się też branże "
            "wypisane wyżej. Bez tego rozszerzenia nie da się ich rozdzielić.",
        )
    else:
        notes.insert(
            0,
            "Te kody znaczą dokładnie to samo co wybrane przez Ciebie, tylko w starszej "
            "klasyfikacji, więc dokładam je bez pytania.",
        )
    return Block(
        title="Doliczam stare kody PKD",
        headers=("kod PKD 2007", "co obejmuje"),
        rows=tuple(rows),
        notes=tuple(notes),
    )


def batches_table(rows: Sequence[tuple[str, str, int, int]]) -> Block:
    """Los poszczególnych partii: etykieta, status, trafienia, rekordy."""
    return Block(
        title="Partie",
        headers=("partia", "co się stało", "trafienia", "rekordy"),
        rows=tuple(
            (label, status, format_number(count), format_number(records))
            for label, status, count, records in rows
        ),
    )


# ----------------------------------------------------------------------------- pojedyncza firma

_CARD_FIELDS: tuple[tuple[str, str], ...] = (
    ("nazwa", "nazwa"),
    ("nip", "NIP"),
    ("regon", "REGON"),
    ("status", "status"),
    ("data_rozpoczecia", "data rozpoczęcia"),
    ("ulica", "ulica"),
    ("budynek", "numer"),
    ("kod_pocztowy", "kod pocztowy"),
    ("miasto", "miejscowość"),
    ("wojewodztwo", "województwo"),
    ("pkd_glowny_kod", "PKD główne"),
    ("pkd_glowny_nazwa", "PKD główne — nazwa"),
    ("pkd_wszystkie", "wszystkie PKD"),
    ("telefon", "telefon"),
    ("email", "e-mail"),
    ("www", "strona"),
    ("link_ceidg", "wpis w CEIDG"),
)


def firm_card(record: NormalizedRecord | None, *, nip: str) -> Block:
    """Karta jednej firmy. Brak trafienia to normalny wynik, nie błąd."""
    if record is None:
        return Block(
            title=f"NIP {nip}",
            rows=(("wynik", "brak wpisu o tym numerze NIP w rejestrze CEIDG"),),
            notes=(
                "Numer przeszedł kontrolę sumy kontrolnej, więc to nie literówka — wpisu "
                "po prostu nie ma w rejestrze albo dotyczy spółki (KRS, nie CEIDG).",
            ),
        )
    rows = tuple(
        (label, str(record.firmy.get(key)))
        for key, label in _CARD_FIELDS
        if record.firmy.get(key) not in (None, "")
    )
    return Block(title=f"NIP {nip}", rows=rows)


# ----------------------------------------------------------------------------- komunikaty poleceń
#
# Reguła granic 9 (ADR-0008, domknięta w ADR-0009): `cli.py` nie układa własnych zdań.
# Zdania stoją tu jako stałe i funkcje, więc te same słowa widzi operator flag, pliku YAML,
# trybu `--tak` i kreatora — a nie dwie kopie, które rozjadą się przy pierwszej poprawce.

NO_RESUMABLE: Final = "Brak przerwanych pobrań."
NO_RUNS: Final = "Brak pobrań w bazie."
FIX_CRITERIA: Final = "Popraw kryteria i uruchom polecenie ponownie."
# To samo sprawdzenie stoi w `cli` (przed zbudowaniem zależności) i w `flow` (przed
# jedynym zapytaniem o `count`). Sprawdzenia są dwa świadomie, zdanie ma być jedno.
EMPTY_CRITERIA: Final = (
    "Podaj przynajmniej jedno kryterium — bez filtra zapytanie objęłoby cały rejestr."
)
TOKEN_EMPTY: Final = "Pusty token — nic nie zapisano."
TOKEN_REMOVED: Final = "Usunięto."
TOKEN_ABSENT: Final = "W magazynie nie było tokenu."
INTERRUPTED: Final = "\nPrzerwano. Postęp jest zapisany — wznów poleceniem `ceidg-tool wznow`."

# Pytania zadawane poza protokołem `Prompter`: zgoda na produkcję i skasowanie bazy zapadają
# w `cli`, zanim powstanie warstwa `ui` (ADR-0008), a token wpisuje się bez echa, czego
# protokół nie umie. Tu leży samo brzmienie — mechanizm zostaje tam, gdzie był.
CONFIRM_PROD: Final = "Chcesz użyć PRODUKCJI (prawdziwe dane osobowe, limity API)? Potwierdź"
TOKEN_PROMPT: Final = "Wklej token JWT"
# Zdanie domyślne — używane tylko wtedy, gdy powodu **nie znamy** (brak klucza albo brak extry
# `asystent`). Gdy powód jest znany, `flow` pokazuje jego własną treść: wymienianie przyczyn na
# ślepo wskazywało przy braku słownika PKD na klucz i pakiet, które akurat były na miejscu.
ASSISTANT_UNAVAILABLE: Final = (
    "Asystent jest niedostępny — brakuje klucza API albo pakietu `anthropic`. Kryteria podasz "
    "pytaniami po kolei albo flagami; stan klucza pokaże `ceidg-tool sprawdz-token`."
)
ASSISTANT_CANCELLED: Final = "Rezygnacja z opisu — wracam do menu."
ASSISTANT_NEEDS_A_HUMAN: Final = (
    "Opis zdaniem wymaga potwierdzenia interpretacji, a tryb --tak nie podejmuje tej decyzji "
    "za Ciebie. Uruchom bez --tak albo podaj kryteria flagami; kreator zapisze je do pliku "
    "zapytania, który nadaje się do harmonogramu."
)
ASSISTANT_KEY_PROMPT: Final = "Wklej klucz API asystenta (sk-ant-…)"
ASSISTANT_KEY_EMPTY: Final = "Pusty klucz — nic nie zapisano."
ASSISTANT_KEY_REMOVED: Final = "Usunięto klucz asystenta z magazynu haseł."
ASSISTANT_KEY_ABSENT: Final = "W magazynie nie było klucza asystenta."


# Cztery rzeczy, nie trzy. Pobrane raporty ZIP to pełne dzienne zrzuty województwa —
# największy zbiór danych osobowych na dysku — więc pytanie musi je wymienić, a nie
# dopiero podsumowanie po fakcie.
PURGED_ITEMS = "checkpointy, logi i pobrane raporty ZIP"


def confirm_purge_all(store_path: Path) -> str:
    return f"Usunąć bazę {store_path} oraz {PURGED_ITEMS}?"


def purge_all_needs_consent(store_path: Path) -> str:
    """Odmowa skasowania bazy w trybie nieinteraktywnym — tak samo jak zgoda na produkcję.

    `--tak` znaczy „przyjmij decyzje domyślne", a domyślna odpowiedź na „skasować wszystko?"
    brzmi „nie". Skasowania nie da się cofnąć, więc literówka w harmonogramie nie może
    kosztować bazy: potrzebna jest druga, jawna flaga.
    """
    return (
        f"Usunięcie bazy {store_path} razem z {PURGED_ITEMS} jest nieodwracalne, "
        "a tryb nieinteraktywny nie podejmuje takiej decyzji za Ciebie. Uruchom bez --tak, "
        "żeby potwierdzić pytaniem, albo dodaj --potwierdzam-usuniecie."
    )


def purge_all_partial(failures: Sequence[tuple[str, str]]) -> str:
    """Czego nie udało się usunąć. Przerwanie po pierwszym błędzie zostawiało resztę na dysku.

    Najczęstsza przyczyna to drugi proces trzymający otwarty plik bazy albo logu na Windows.
    Kasowanie idzie wtedy dalej, bo zatrzymanie się na bazie zostawiłoby raporty ZIP —
    czyli akurat te dane, których operator najbardziej chciał się pozbyć.
    """
    lines = ", ".join(f"{path} ({reason})" for path, reason in failures)
    return f"Nie udało się usunąć: {lines}. Zamknij inne uruchomienia narzędzia i powtórz."


def token_saved() -> str:
    return f"Zapisano token w magazynie haseł. Nowy token uzyskasz: {TOKEN_SERVICE_URL}"


def assistant_key_saved() -> str:
    """Zdanie po zapisaniu klucza asystenta — mówi też, co się przez to zmienia na ekranie."""
    return (
        "Zapisano klucz asystenta w magazynie haseł. Od tej chwili kreator proponuje opisanie "
        "zapytania jednym zdaniem."
    )


def token_removed(removed: bool) -> str:
    return TOKEN_REMOVED if removed else TOKEN_ABSENT


def unresolved_note(unresolved: int) -> str:
    """Zdanie o wpisach, które zostały bez szczegółów i bez wyjaśnienia.

    Jedno źródło dla obu ścieżek: `aktualizuj` dopina je do własnego podsumowania,
    `pobierz --szczegoly` przez `ExecuteResult.notes`. Dwie kopie tego zdania rozjechałyby
    się przy pierwszej poprawce, a to jest zdanie o cichej stracie."""
    return (
        f"Uwaga: {unresolved} wpisów zostało bez szczegółów i bez wyjaśnienia "
        "(ani pobrane, ani nieznalezione, ani błędne) — zajrzyj do logu."
    )


def stale_details_note(stale: int) -> str:
    """Zdanie o wpisach, które zostały z opisem sprzed zmiany.

    Osobne od `unresolved_note`, bo to inna awaria i inna rada: tam wpis nie ma szczegółów
    wcale, tutaj ma — tylko nieaktualne, więc żaden licznik „brakujących" go nie pokaże."""
    return (
        f"Uwaga: {stale} wpisów zachowało opis sprzed zgłoszonej zmiany. "
        "To nie powinno się zdarzyć — powtórz `aktualizuj` dla tego samego zakresu "
        "i zgłoś problem, jeśli liczba się utrzyma."
    )


def update_summary(records: int, details: int, unresolved: int = 0, stale: int = 0) -> str:
    """Wynik `aktualizuj` — jedno zdanie dla polecenia i dla kreatora.

    Liczba szczegółów dostaje punkt odniesienia. 2026-09-08 to zdanie brzmiało
    „Zmienionych wpisów: 13401, szczegółów: 0." po trzech godzinach pracy i 2 681 żądaniach —
    liczba była poprawna i nie miała się do czego odnieść, więc „0" czytało się jak
    „nic nie trzeba było dopisywać", a znaczyło „wszystko przepadło"."""
    zdanie = f"Zmienionych wpisów: {records}, szczegółów: {details}."
    if unresolved:
        zdanie += " " + unresolved_note(unresolved)
    if stale:
        zdanie += " " + stale_details_note(stale)
    return zdanie


def update_declined(count: int) -> str:
    """Aktualizacja nie ruszyła — dwa różne powody, dwa różne zdania.

    „Nic się nie zmieniło" to dobra wiadomość, a „nie zaczynam" to decyzja operatora;
    jedno zdanie na oba przypadki kazałoby zgadywać, co się właściwie stało."""
    if count == 0:
        return "Baza jest aktualna — od ostatniej aktualizacji nic się nie zmieniło."
    return f"Nie zaczynam aktualizacji. Zmian do pobrania: {format_number(count)}."


def report_saved(path: Path) -> str:
    return f"Zapisano {path} ({format_size(path)})"


def purge_all_summary(zips: int) -> str:
    return f"Usunięto bazę, logi i {zips} pobranych raportów."


def purge_summary(runs: int, records: int, zips: int) -> str:
    return f"Usunięto {runs} pobrań, {records} rekordów cache i {zips} raportów ZIP."
