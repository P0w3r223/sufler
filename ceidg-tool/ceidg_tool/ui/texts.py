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
from typing import Final, Literal

from ..batching import GRANULARITY_LABEL, Batch, BatchPlan
from ..config import DEMO_OSTRZEZENIE, TOKEN_SERVICE_URL, Settings
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


PRZYKLAD_OPISU: Final = "salony fryzjerskie i firmy ubezpieczeniowe we Wrocławiu i Gdańsku"
"""Zdanie, które naprawdę przeszło przez asystenta (2026-09-09): dwie branże i dwa miasta
naraz, `miasto: Gdańsk, Wrocław`, `PKD: 6622Z, 9621Z`. Przykład jest tu **zmierzony**, a nie
wymyślony — obiecywanie na ekranie składni, której nikt nie sprawdził, byłoby tą samą pomyłką,
co dane wzorcowe pisane z pamięci (CLAUDE.md)."""


def pobierz_menu_item(z_asystentem: bool) -> MenuItem:
    """Pozycja menu dla pobierania — mówi, że **wystarczy opisać zdaniem**, gdy asystent działa.

    Opis zdaniem jest pierwszym pytaniem tej ścieżki od fazy 4 (ADR-0011, decyzja 5:
    nie osobna pozycja menu, żeby nie dublować drogi po kryteriach). Tyle że menu
    mówiło „Pobrać firmy **według kryteriów** — lista albo szczegóły”, więc operator
    czytał „formularz” i o istnieniu tamtej drogi nie miał skąd wiedzieć. Funkcja
    istniejąca i niewidoczna jest z punktu widzenia operatora funkcją nieistniejącą —
    a to jest narzędzie dla kogoś, kto nie zna API i nie zna kodów PKD.
    """
    if not z_asystentem:
        return MenuItem("pobierz", "Pobrać firmy według kryteriów", "lista albo szczegóły")
    return MenuItem(
        "pobierz",
        "Pobrać firmy — opisz zdaniem albo podaj kryteria",
        f"np. „{PRZYKLAD_OPISU}”",
    )


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


def first_screen(settings: Settings, *, now: datetime, version: str, demo: bool = False) -> Block:
    """Pierwszy ekran wg UZUPELNIENIE_01 §A: co robi, dokąd wysyła, gdzie pracuje, jaki token.

    W trybie demo ekran mówi o tym **pierwszym** wierszem i w tytule. To znacznik numer jeden
    z ADR-0014: skoro tryb bez rejestru jest własnością produktu, a nie osobnym programem, to
    jedyne, co dzieli pokaz od pracy, jest napisane na ekranie i w skoroszycie. Ekran, który
    o tym milczy, zamienia to demo w najgorszy z rozważanych wariantów.
    """
    environment = "PRODUKCJA (prawdziwe dane osobowe)" if settings.environment == "prod" else "TEST"
    host = "dane.biznes.gov.pl" if settings.environment == "prod" else "test-dane.biznes.gov.pl"
    zrodlo = (
        "syntetyczny rejestr w pamięci procesu (dane wymyślone)"
        if demo
        else (f"wyłącznie do API CEIDG ({host}); brak telemetrii")
    )
    rows: tuple[tuple[str, str], ...] = (
        ("co robi", PROGRAM_PURPOSE),
        ("dokąd wysyłam rekordy", zrodlo),
        ("dokąd wysyła asystent", _assistant_destination(settings, demo=demo)),
        ("środowisko", "POKAZ (bez rejestru)" if demo else environment),
        ("token", f"{settings.token_info.validity_text(now)} (źródło: {settings.token_source})"),
        ("dane i wyniki", str(settings.data_dir)),
    )
    if demo:
        rows = (("UWAGA", DEMO_OSTRZEZENIE), *rows)
    notes = [f"Nowy token: {TOKEN_SERVICE_URL}"]
    if settings.environment != "prod" and not demo:
        notes.append(PROD_HINT)
    tytul = f"ceidg-tool {version}" + (" — POKAZ" if demo else "")
    return Block(title=tytul, rows=rows, notes=tuple(notes))


def _assistant_destination(settings: Settings, *, demo: bool = False) -> str:
    """Wiersz §A o asystencie — rozdzielony od wiersza o rekordach, bo cele są różne.

    Do 2026-09-07 pierwszy ekran mówił „wyłącznie do API CEIDG". To jest kryterium odbioru §A
    i przestawało być prawdą w chwili użycia asystenta, więc wiersz musiał się rozdwoić:
    rekordy idą wyłącznie do CEIDG, a do modelu — treść pytania i słownik PKD, nigdy rekordy.

    Brak klucza ma **dwie różne przyczyny** i do 2026-09-09 obie dostawały to samo zdanie
    „brak klucza — zapisz go poleceniem…". W pokazie było ono nieprawdą podwójnie: klucz
    zwykle istnieje (w `.env`), a polecenie z podpowiedzi pisze do keyringu, którego tryb
    pokazu celowo nie czyta — `_settings_demo` woła `load_settings(env_file=None,
    use_keyring=False)`, żeby produkcyjny token z PESEL-em w ładunku nie miał którędy wejść.
    Operator dostawał więc wskazanie palcem na rzecz, która akurat działa, i nie miał jak
    zobaczyć asystenta w pokazie — czyli w jedynym miejscu, gdzie wolno go poznawać bez
    prawdziwych danych osobowych.
    """
    if settings.anthropic_key is not None:
        return "treść Twojego pytania i słownik PKD do api.anthropic.com; pobrane rekordy — nigdy"
    if demo:
        return (
            "asystent wyłączony — pokaz nie czyta klucza z .env ani z keyringu; "
            "włącz go zmienną środowiskową ANTHROPIC_API_KEY"
        )
    return "asystent wyłączony (brak klucza: `ceidg-tool token zapisz --asystent`)"


# ------------------------------------------------------- runda dopytania (ADR-0017)

DOPYTANIE_ZAPASOWE: Final = (
    "Nie wyciągnąłem z tego zdania ani jednego filtru. Powiedz cokolwiek konkretnego — "
    "wystarczy miejscowość albo czym firma się zajmuje."
)
"""Pytanie układane **przez kod**, gdy model nie zadał własnego.

Siatka bezpieczeństwa, nie ozdoba: gdyby ekran dopytania zależał wyłącznie od tego, czy model
wypełnił `pytanie`, to obietnica „pusty opis nigdy nie kończy się błędem” trzymałaby się
zachowania modelu, czyli nie byłaby obietnicą. `flow` wchodzi w rundę na podstawie **pustych
kryteriów**, a nie na podstawie tego, czy model o coś zapytał."""

DOPYTANIE_JAK_DZIALA: Final = (
    "Wybierz gotowe zdanie albo napisz własne — i tak zobaczysz, co z tego zrozumiałem, "
    "zanim cokolwiek ruszy do rejestru."
)

WOJEWODZTWO_PYTANIE: Final = "Z którego województwa?"


def clarification(opis: str, pytanie: str) -> Block:
    """Ekran rundy dopytania: co napisał operator, o co pyta asystent, co można wybrać.

    Do 2026-09-09 ta sytuacja kończyła się tak: pusty ekran interpretacji („rozumiem jako:
    tylko lista podstawowa”), pytanie „Czy tak rozumiem Twoje zapytanie?” z domyślną
    odpowiedzią **tak**, a po Enterze `Błąd: Podaj przynajmniej jedno kryterium` i powrót do
    menu **bez opisu**. Trzy rzeczy naraz: pytanie o zgodę na nic, domyślna odpowiedź
    prowadząca w błąd i utrata tego, co operator już napisał.
    """
    # Propozycje **nie** są tu wierszami: niesie je menu z `clarification_menu`, a wypisanie
    # ich dwa razy kazałoby operatorowi zgadywać, czy to ta sama lista. Parametr `propozycje`
    # stał w sygnaturze nieużywany do przeglądu 2026-09-09 i mówił czytelnikowi nieprawdę
    # o tym, co ten ekran pokazuje.
    rows = [("Twoje zdanie", opis), ("asystent pyta", pytanie or DOPYTANIE_ZAPASOWE)]
    return Block(title="Doprecyzujmy", rows=tuple(rows), notes=(DOPYTANIE_JAK_DZIALA,))


def clarification_menu(propozycje: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Pozycje rundy dopytania: `(akcja, etykieta)` w kolejności wyświetlania.

    Propozycje modelu idą pierwsze, bo są konkretne i jednym naciśnięciem kończą sprawę.
    Za nimi dwie drogi, które **nie potrzebują modelu** i dlatego są tu zawsze: wybór
    województwa z listy szesnastu (zbiór zamknięty, więc nie da się go napisać źle) i własne
    zdanie. Dopiero na końcu formularz i wyjście — bo odesłanie do formularza jest tym
    zachowaniem, które ta runda ma zastąpić, a nie tym, do którego ma prowadzić."""
    pozycje: list[tuple[str, str]] = [(f"opis:{i}", zdanie) for i, zdanie in enumerate(propozycje)]
    pozycje.append(("wojewodztwo", "wybiorę województwo z listy"))
    pozycje.append(("wlasny", "napiszę to inaczej"))
    pozycje.append(("pytania", "przejdę do pytań po kolei"))
    pozycje.append(("wyjdz", "wróć do menu"))
    return tuple(pozycje)


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
    # Uwagi to **wyłącznie** ograniczenia rejestru. Wiersz o koszcie asystenta (ok. 25 tys.
    # tokenów, ułamek grosza) stał tu do 2026-09-09 i został usunięty na wniosek właściciela:
    # ekran interpretacji jest jedynym tekstem, na podstawie którego operator działa, a stawka
    # za pytanie nie wpływa na żadną jego decyzję — w odróżnieniu od tabeli kosztów żądań,
    # która wciąż poprzedza pobieranie. Deklaracja „co idzie do api.anthropic.com" nie znika:
    # niesie ją wiersz „dokąd wysyła asystent" na pierwszym ekranie (`first_screen`).
    notes = [OGRANICZENIA_ZDANIA[kod] for kod in ograniczenia if kod in OGRANICZENIA_ZDANIA]
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


# ------------------------------------------------------------------- zero trafień (pomoc)

POLE_PO_POLSKU: Final[dict[str, str]] = {
    "nazwa": "fragmentu nazwy",
    "ulica": "ulicy",
    "budynek": "numeru nieruchomości",
    "lokal": "numeru lokalu",
    "kod": "kodu pocztowego",
    "imie": "imienia",
    "nazwisko": "nazwiska",
    "miasto": "miejscowości",
    "pkd": "branży (kodu PKD)",
    "gmina": "gminy",
    "powiat": "powiatu",
    "status": "statusu",
    "daty": "zakresu dat",
    "wojewodztwo": "województwa",
}
"""Nazwy filtrów w dopełniaczu — wchodzą do zdania „spróbuj bez …”."""

DLACZEGO_PUSTO: Final[dict[str, str]] = {
    "nazwa": "dopasowuje się dosłownie, więc odmiana albo skrót w rejestrze go omija",
    "ulica": "rejestr zapisuje ją różnie („Kwiatowa”, „ul. Kwiatowa”), więc łatwo się rozminąć",
    "budynek": "numer bywa zapisany jako „12A”, „12 A” albo „12/3” — „12” nie trafia w żaden",
    "lokal": "numer lokalu jest wypełniony w niespełna co czwartym wpisie, więc częściej go brak",
    "kod": "jedna cyfra obok i nie pasuje już nic",
    "imie": "wpis nosi imię przedsiębiorcy, nie nazwę firmy — łatwo je pomylić",
    "nazwisko": "wpis nosi nazwisko przedsiębiorcy, nie nazwę firmy — łatwo je pomylić",
    "miasto": "rejestr trzyma nazwę urzędową; dzielnica albo nazwa potoczna nie trafia",
    # To jest ten sam pomiar, który stoi w ADR-0012, i najczęstsza przyczyna pustki przy
    # poprawnym kodzie: kod może żyć w PKD 2025 i mimo to nie stać na żadnym wpisie w regionie.
    "pkd": "kod bywa poprawny, a nieużywany — 58,6 % wpisów wciąż ma rocznik PKD 2007",
    "gmina": "nazwa gminy bywa inna niż nazwa miejscowości",
    "powiat": "miasto na prawach powiatu ma powiat równy swojej nazwie, nie przymiotnikowi",
    "status": "wpisy bywają zawieszone albo wykreślone, a filtr statusu je odsiewa",
    "daty": "filtr dotyczy wyłącznie daty rozpoczęcia działalności, nie daty zmiany wpisu",
    "wojewodztwo": "miejscowość mogła zostać przypisana do innego województwa niż podane",
}
"""Dlaczego **akurat ten** filtr bywa winny pustki. Klucze muszą pokrywać się z
`POLE_PO_POLSKU` i z kolejnością w `Criteria.poszerzenia` — pilnuje tego test, bo filtr bez
zdania wypadłby z ekranu po cichu, czyli dokładnie wtedy, gdy jest jedyną propozycją."""

BRAK_TRAFIEN_KOSZT: Final = (
    "Każde sprawdzenie to jedno zapytanie do rejestru — nic się jeszcze nie pobiera."
)

BRAK_TRAFIEN_JEDYNY_FILTR: Final = (
    "To był jedyny filtr, więc nie ma czego zdjąć — zapytanie bez żadnego objęłoby cały "
    "rejestr. Opisz to inaczej: inna miejscowość, inna branża albo szerszy region."
)


def zero_hits(criteria: Criteria, propozycje: Sequence[tuple[str, Criteria]]) -> Block:
    """Ekran zera trafień: co sprawdzono i **co konkretnie** można z tym zrobić.

    Do 2026-09-09 w tym miejscu padało jedno zdanie „Brak firm spełniających kryteria”,
    po czym przepływ zwracał `wyjdz` — pytanie „Co dalej?” z pozycją „popraw kryteria” nie
    pojawiało się przy zerze **nigdy**, bo zero wychodziło wcześniej. Operator wracał do menu
    i przepisywał opis od zera, nie dowiedziawszy się niczego o tym, który filtr był winny.
    A to jest najczęstsza ścieżka kogoś, kto nie wie dokładnie, czego szuka.

    Kolejność propozycji pochodzi z `Criteria.poszerzenia`, a więc z twierdzenia o rejestrze,
    nie z układu ekranu; tutaj zostaje samo nazwanie ich po polsku.
    """
    if not propozycje:
        return Block(
            title="Nic nie znaleziono",
            rows=(("szukano", criteria.describe()),),
            notes=(BRAK_TRAFIEN_JEDYNY_FILTR,),
        )
    rows = tuple(
        (POLE_PO_POLSKU[pole], DLACZEGO_PUSTO[pole], kandydat.describe())
        for pole, kandydat in propozycje
    )
    return Block(
        title="Nic nie znaleziono — spróbujmy bez jednego z filtrów",
        headers=("bez czego", "dlaczego akurat to", "zostaje"),
        rows=rows,
        notes=(BRAK_TRAFIEN_KOSZT,),
    )


def zero_hits_menu(propozycje: Sequence[tuple[str, Criteria]]) -> tuple[tuple[str, str], ...]:
    """Pozycje pytania po zerze trafień: `(wartość, etykieta)`, w kolejności propozycji.

    Etykiety układa `texts`, a nie `flow` — pytanie jest budowane dynamicznie, więc bez tego
    zdanie dla operatora powstawałoby w module decyzyjnym (reguła granic 9 w duchu, choć
    literalnie dotyczy `cli.py`)."""
    pozycje = [(pole, f"szukaj bez {POLE_PO_POLSKU[pole]}") for pole, _ in propozycje]
    pozycje.append(("popraw", "opiszę to inaczej"))
    pozycje.append(("wyjdz", "wróć do menu"))
    return tuple(pozycje)


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
            _zakres_partii(batch),
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
        f"({len(plan.batches)} partii, {_zakres_planu(plan)})",
        headers=("partia", "zakres dat", "szacowane trafienia", "zapytania", "czas"),
        rows=rows,
        notes=tuple(notes),
    )


def _zakres_partii(batch: Batch) -> str:
    """Zakres dat partii tak, jak trafia do zapytania — z „…" tam, gdzie granicy nie ma.

    Kafel ma zawsze obie daty, ale partia skrajna zapytania niepodzielonego nie wysyła
    granicy, której operator nie podał (ADR-0015). Wypisanie daty kafla obiecywałoby zakres
    węższy niż to, co program naprawdę pobierze."""
    od = "…" if batch.otwarty_od else batch.od.isoformat()
    do = "…" if batch.otwarty_do else batch.do.isoformat()
    return f"{od} – {do}"


def _zakres_planu(plan: BatchPlan) -> str:
    """To samo dla nagłówka tabeli podziału: `covers` to granice planistyczne, nie zapytania."""
    od = "…" if plan.otwarty_od else str(plan.covers[0])
    do = "…" if plan.otwarty_do else str(plan.covers[1])
    return f"{od} – {do}"


def batches_shortfall(counted: int, expected: int, missing: int) -> str:
    """Różnica między `count` sprzed podziału a sumą partii — **bez nazywania przyczyny**.

    Do ADR-0015 to zdanie brzmiało „różnica to wpisy bez daty rozpoczęcia działalności,
    których filtr dat nie obejmuje". Było fałszywe podwójnie: prawdziwą przyczyną były
    granice dat, które planer dokładał do partii (482 rekordy = 2,96 % na bazie operatora),
    a wpisów bez daty rozpoczęcia nie było w tej bazie **ani jednego**. Zdanie wskazywało
    palcem na przyczynę nieistniejącą i zasłaniało istniejącą — czyli robiło coś gorszego
    niż milczenie, bo zamykało pytanie."""
    return (
        f"Partie objęły {format_number(counted)} z {format_number(expected)} trafień; "
        f"różnicy ({format_number(missing)}) nie da się przypisać jednej przyczynie. "
        "Rejestr zmienia się między zapytaniem o koszt a pobraniem partii, a wpisy bez daty "
        "rozpoczęcia nie trafiają do żadnej partii, bo podział idzie po tej dacie. "
        "Przy dużej różnicy powtórz pobranie."
    )


def batches_surplus(counted: int, expected: int, surplus: int) -> str:
    """Partie zobaczyły więcej, niż zapowiedziała wycena. Nieszkodliwe i dotąd niewidoczne."""
    return (
        f"Partie objęły {format_number(counted)} trafień, o {format_number(surplus)} więcej "
        f"niż zapowiedziało zapytanie o koszt ({format_number(expected)}). To zwykły dryf "
        "rejestru w czasie pobierania — nic nie przepadło."
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
    # Czy kontaktów w tym pliku **nie może** być. Liczy to `pipeline` z danych, bo źródła są
    # dwa: `detail_json` na ścieżce API i sam wiersz CSV na ścieżce raportu.
    bez_kontaktow: bool
    sheets: tuple[str, ...]
    kind: str
    run_ids: tuple[str, ...]
    log_path: Path
    # Czy operator prosił o szczegóły — rozstrzyga wyłącznie o treści rady, nie o zawartości.
    tryb_szczegoly: bool = False
    extra: tuple[tuple[str, str], ...] = field(default=())
    # Stan każdego runu, w kolejności `run_ids`. Bez tego skoroszyt z pobrania przerwanego
    # w połowie wygląda na ekranie dokładnie tak samo jak komplet (audyt 2026-09-08, A7).
    statuses: tuple[str, ...] = field(default=())


BEZ_KONTAKTOW: Final = (
    "nie pobrano — lista podstawowa ich nie zawiera; powtórz z opcją „lista z pełnymi szczegółami”"
)
"""Zdanie zamiast zera. Kolumn, których lista podstawowa nie niesie, skoroszyt też już nie
pokazuje (`normalizer.KOLUMNY_TYLKO_ZE_SZCZEGOLOW`) — ekran i plik mówią to samo."""

BEZ_KONTAKTOW_NIEDOKONCZONE: Final = (
    "jeszcze nie pobrane — to pobranie prosiło o szczegóły, ale do nich nie doszło; "
    "dokończ je poleceniem `ceidg-tool wznow`"
)
"""Ten sam brak, inna przyczyna i **inne lekarstwo**.

Przerwane pobranie ze szczegółami ma w pliku zero kontaktów tak samo jak zwykła lista, więc
jeden predykat je zlewa — ale rada „powtórz z opcją ze szczegółami" znaczy dla niego „zacznij
od zera", podczas gdy uwaga o niedokończonym przebiegu, drukowana dwa wiersze niżej, mówi
`wznow`. Dwa zdania na jednym ekranie odsyłające w dwie strony to gorszy stan niż jedno
milczenie (przegląd 2026-09-09)."""


def _bez_kontaktow(summary: SummaryInput) -> str:
    return BEZ_KONTAKTOW_NIEDOKONCZONE if summary.tryb_szczegoly else BEZ_KONTAKTOW


def summary_table(summary: SummaryInput) -> Block:
    """Podsumowanie wg §A: plik i rozmiar, firmy wg statusu, odsetek kontaktów, arkusze, log."""
    rows: list[tuple[str, ...]] = [
        ("plik", f"{path} ({format_size(path)})") for path in summary.paths
    ]
    rows.append(("firm", format_number(summary.records)))
    for status, count in summary.by_status.items():
        rows.append((f"  {status}", format_number(count)))
    if summary.records and not summary.bez_kontaktow:
        rows.append(("z telefonem", _share(summary.with_phone, summary.records)))
        rows.append(("z e-mailem", _share(summary.with_email, summary.records)))
    elif summary.records:
        # Odsetek liczony z kolumn, których w tym trybie nikt nie pobierał, zawsze wynosi
        # zero — i czyta się jako pomiar. To ta sama pomyłka, którą projekt zna z liczby
        # 25,2 % w dokumentach: liczba odpowiadająca na inne pytanie niż to, które czytelnik
        # jej zadaje. Zdanie mówi więc, czego **nie ma w pliku** i skąd to wziąć.
        #
        # Warunek pyta o **dwa** źródła kontaktów, nie o jedno, i tego zabrakło w pierwszej
        # wersji tej poprawki: `/firma` wypełnia je przez `detail_json`, a dzienny raport
        # wprost w wierszu CSV — bez żadnych szczegółów. Przebieg produkcyjny 2026-09-09
        # (282 firmy z raportu, kontakty w 22 % i 24 % wierszy) dostał więc zdanie
        # „nie pobrano" i radę, żeby powtórzyć ze szczegółami, choć dane były w pliku.
        # Naprawa cichej nieprawdy na ścieżce częstszej wyprodukowała głośną na rzadszej.
        rows.append(("telefon i e-mail", _bez_kontaktow(summary)))
    rows.append(("arkusze", ", ".join(summary.sheets)))
    rows.append(("log", str(summary.log_path)))
    rows.append(("run_id", ", ".join(summary.run_ids)))
    rows.extend(summary.extra)
    return Block(title="Podsumowanie", rows=tuple(rows), notes=summary_notes(summary))


def _share(part: int, total: int) -> str:
    return f"{format_number(part)} ({100 * part / total:.0f}%)"


def summary_notes(summary: SummaryInput) -> tuple[str, ...]:
    """Zdanie o kolumnie `link_ceidg` zależy od źródła danych, a zdanie o kompletności — od
    stanu runów, z których plik powstał."""
    return (*_niekompletny(summary), *_zrodlo_notes(summary))


def _niekompletny(summary: SummaryInput) -> tuple[str, ...]:
    """Ostrzeżenie tylko wtedy, gdy naprawdę jest o czym mówić.

    Ostrzeżenie stałe uczy operatora je pomijać — ta sama zasada, co przy
    `vintage_skipped`. Eksport wskazanego, nieskończonego runu jest dozwolony; wadą było
    milczenie o tym, nie sam eksport."""
    niepelne = [s for s in summary.statuses if s != "zakonczony"]
    if not niepelne:
        return ()
    stany = ", ".join(sorted(set(niepelne)))
    if len(summary.statuses) == 1:
        return (
            f"Uwaga: to pobranie nie jest zakończone (stan: {stany}), więc skoroszyt zawiera "
            "tylko to, co zdążyło się pobrać. Dokończ je poleceniem `ceidg-tool wznow` "
            "i wyeksportuj ponownie.",
        )
    return (
        f"Uwaga: {len(niepelne)} z {len(summary.statuses)} partii nie jest zakończonych "
        f"(stan: {stany}), więc skoroszyt jest niepełny. Arkusz Metadane podaje stan każdej "
        "partii z osobna, a `ceidg-tool wznow` dokończy brakujące.",
    )


def _zrodlo_notes(summary: SummaryInput) -> tuple[str, ...]:
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
    uwagi = [
        "Kolumna link_ceidg w arkuszu Firmy prowadzi do wpisu w publicznej wyszukiwarce "
        "CEIDG — do ręcznej weryfikacji.",
    ]
    if summary.bez_kontaktow and summary.records:
        # Ukrywanie kolumn bez powiedzenia o tym byłoby tym samym defektem co wcześniejsze
        # „0 (0%)", tylko odwróconym: operator zobaczy w Excelu przeskakujące litery kolumn
        # i uzna, że plik jest niepełny. Ścieżka raportu mówi to od początku (wyżej), a
        # ścieżka listy chowała kolumny od 2026-09-09 i milczała.
        uwagi.append(
            "Kolumn, których lista podstawowa nie zawiera (telefon, e-mail, PKD dodatkowe, "
            "adres korespondencyjny), skoroszyt nie pokazuje. Są w pliku — w Excelu "
            "przywraca je „Odkryj”, a arkusz Metadane wymienia je w wierszu kolumny_ukryte."
        )
    return tuple(uwagi)


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


STATUS_BRAK_W_RAPORCIE: Final = {
    "WYKRESLONY": "WYKREŚLONYCH",
    "OCZEKUJE_NA_ROZPOCZECIE_DZIALANOSCI": "OCZEKUJĄCYCH NA ROZPOCZĘCIE",
}
"""Polskie nazwy statusów, których dzienny zrzut nie zawiera — klucze muszą pokrywać się
z `reports.STATUSY_SPOZA_RAPORTU`. Dwie stałe zamiast jednej, bo `texts` jest modułem czystym
(reguła 6) i nie importuje `reports`; zgodności pilnuje test, dokładnie tak jak przy parze
`WAIT_SLICE_S` / `DEFAULT_LOCK_STALE_S`."""


def _brakujace_statusy() -> str:
    return " i ".join(STATUS_BRAK_W_RAPORCIE[kod] for kod in sorted(STATUS_BRAK_W_RAPORCIE))


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
                # Statusy z jednej listy, a nie przepisane z ręki: `reports.STATUSY_SPOZA_RAPORTU`
                # decyduje, a `STATUS_BRAK_W_RAPORCIE` niżej je nazywa. Dopisanie trzeciego
                # statusu do stałej, a nie do ekranu, byłoby A10 jeszcze raz — lista odmawiała
                # dwóch, ekran wymieniał jeden. Zgodność obu pilnuje test, bo `texts` musi
                # zostać czyste (reguła 6) i nie może importować `reports`.
                f"firm {_brakujace_statusy()}, adresu korespondencyjnego, obywatelstw i spółek",
            ),
        ),
    )


PowodBrakuRaportu = Literal[
    "WIELE_WOJEWODZTW",
    "BRAK_WOJEWODZTWA",
    "STATUS_SPOZA_RAPORTU",
    "FILTR_SPOZA_RAPORTU",
    "BRAK_DZISIEJSZEGO",
]
"""Zamknięty zbiór powodów, dla których ścieżka raportu odpada.

`Literal`, a nie zwykły napis, bo `flow._powod_braku_raportu` musi go zwracać: piąty kod
dopisany tam bez zdania niżej daje wtedy **błąd mypy przy zwrocie**, a nie `KeyError` w środku
kreatora — a `KeyError` nie należy do taksonomii `CeidgError`, więc wyszedłby śladem stosu.
Test porównujący zbiory tego nie łapie: buduje powody z czterech ręcznie napisanych kryteriów,
więc kod, którego nikt nie wywołał, zostawia obie strony przy czwórce (przegląd 2026-09-09).
To ten sam wybór narzędzia co przy regule granic 14 — mypy tam, gdzie skan nie sięga."""

RAPORT_NIEDOSTEPNY: Final[dict[PowodBrakuRaportu, str]] = {
    "WIELE_WOJEWODZTW": (
        "Gotowe raporty dzienne są wydawane osobno dla każdego województwa, a Twoje kryteria "
        "obejmują więcej niż jedno."
    ),
    "BRAK_WOJEWODZTWA": (
        "Gotowe raporty dzienne są wydawane osobno dla każdego województwa, a Twoje kryteria "
        "nie wskazują żadnego."
    ),
    "STATUS_SPOZA_RAPORTU": (
        "Dzienny zrzut nie zawiera wpisów o statusie, o który pytasz, więc wynik z raportu "
        "byłby pusty — i to bez ostrzeżenia."
    ),
    "FILTR_SPOZA_RAPORTU": (
        "Pytasz o spółkę cywilną (NIP albo REGON spółki), a dzienny zrzut w ogóle nie ma "
        "takiej kolumny — z raportu nie da się tego odsiać, więc wynik byłby pusty."
    ),
    "BRAK_DZISIEJSZEGO": (
        "Dla tego województwa nie ma dziś gotowego raportu. Rejestr wydaje je nad ranem "
        "i trzyma około sześciu dni."
    ),
}
"""Dlaczego ścieżka raportu odpada — kody, bo `texts` nie importuje `reports` (reguła 6).
Ustala je `flow`, nazywa ten słownik; zgodności kluczy pilnuje test."""

RAPORT_ZAMIAST_NIEGO: Final = (
    "Zwykła droga przez API zwróci to samo, tylko drożej: zamiast dwóch zapytań będzie ich "
    "tyle, ile stron wyniku. Zaraz zobaczysz dokładną liczbę i czas, zanim cokolwiek ruszy."
)


def report_unavailable(powod: PowodBrakuRaportu, statusy: Sequence[str] = ()) -> Block:
    """Ekran „raport nie pokrywa tych kryteriów" — powód i **co się stanie zamiast tego**.

    Do 2026-09-09 padał tu wyjątek ze zdaniem „Użyj --zrodlo api albo auto", czyli nazwa
    flagi wiersza poleceń — w kreatorze, gdzie żadnej flagi nie ma. Operator tracił przy tym
    kryteria i wracał do menu, choć wszystko, czego brakowało, to jedno pytanie: czy pobrać
    zwykłą drogą. Sam powód też nie padał, więc „popraw i spróbuj jeszcze raz" było zgadywanką.
    """
    rows = [("dlaczego", RAPORT_NIEDOSTEPNY[powod])]
    if statusy:
        rows.append(("statusy spoza raportu", ", ".join(statusy)))
    return Block(
        title="Gotowy raport tu nie pomoże", rows=tuple(rows), notes=(RAPORT_ZAMIAST_NIEGO,)
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


def no_finished_run(total: int) -> str:
    """`eksportuj` bez `--run-id` bierze ostatnie **zakończone** pobranie — tak brzmi pomoc
    tej flagi. Do audytu 2026-09-08 (A7) brało najnowsze pobranie dowolnego stanu, więc
    pobranie przerwane w połowie eksportowało się jako komplet i nic tego nie mówiło."""
    return (
        f"Żadne z {format_number(total)} pobrań w bazie nie jest zakończone. Dokończ je "
        "poleceniem `ceidg-tool wznow` albo wskaż konkretne: "
        "`ceidg-tool eksportuj --run-id <id>` — wtedy skoroszyt powie, że jest niepełny."
    )


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
OPIS_PORZUCONY: Final = (
    "Rezygnacja z opisu zdaniem. `pobierz` nie ma pytań po kolei — ma je kreator "
    "(`ceidg-tool kreator`); tutaj podaj kryteria flagami, na przykład "
    "`-w wielkopolskie --miasto Poznań`."
)
"""`collect_from_description` zwróciło `None`, czyli operator wybrał pytania po kolei.

Osobne zdanie od `ASSISTANT_UNAVAILABLE`, bo to **inna sytuacja**: asystent zadziałał, klucz
jest, a wyszedł z tego wybór operatora. Do 2026-09-09 `pobierz --opis` mówił w tym miejscu
„Asystent jest niedostępny — brakuje klucza API albo pakietu `anthropic`", czyli wskazywał
palcem na rzecz, która akurat działała. Ta sama klasa pomyłki co komunikat o kluczu w trybie
pokazu (przebieg B6)."""
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
