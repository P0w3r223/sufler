"""Treść wiadomości do pracowników i administratora (czysta logika) + ich wersja HTML dla Teams.

Każda wiadomość istnieje w DWÓCH postaciach naraz — dlatego builderzy zwracają ``Tresc``:

- **tekst** (sama ``Tresc`` jest napisem) — to czytają testy, log trybu diagnostycznego
  i każdy, kto sprawdza treść „na oko";
- **HTML** (``Tresc.html``) — to dostaje Teams: tabela dla wszystkiego, co ma dni, godziny albo
  liczby, a przy wiadomościach bez danych — pogrubiony tytuł i karta „etykieta → wartość".

Obie postaci niosą TĘ SAMĄ informację, ale nie te same zdania: HTML rozkłada ją na tytuł i wiersze
karty. Wspólny jest tytuł/pierwsze zdanie (np. „Nie dostałem odpowiedzi") — testy, które pytają
o to, CO wysłano, sprawdzają właśnie ten fragment albo porównują z ``to_html(builder(...))``.

Tabele są zwykłym ``<table>`` z ``<thead>``/``<tbody>``, bez stylów: dokładnie tym znacznikiem
Teams renderuje tabele w czacie poprawnie (zmierzone na żywo 2026-08-21 przy tabelach Suflera),
a style w linii klient Teams w dużej części odrzuca — na nich wygląd by się rozjechał.
Treść dynamiczna (imię, nazwa powodu z Shifts) jest ZAWSZE escapowana.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Member, WeekSchedule

_DNI = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
_DNI_SKROT = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]
_DNI_DOPELNIACZ = [
    "poniedziałku",
    "wtorku",
    "środy",
    "czwartku",
    "piątku",
    "soboty",
    "niedzieli",
]


def _do_nastepnego_dnia(start: datetime, end: datetime) -> str:
    """Dopisek „ (do soboty)" dla zmiany przechodzącej przez północ; inaczej pusty napis.

    Bez tego „pt 22:00–06:00" czyta się jak literówkę albo jak zmianę cofniętą w czasie, a od 0.2.8
    taki zapis jest POPRAWNY i zostanie zapisany do grafiku (patrz `interpreter.build_schedule`).
    To jedyna obrona przed odwrotną pomyłką: pracownik, który chciał napisać „8–16", a napisał
    „16–8", musi mieć szansę zauważyć to w prośbie o potwierdzenie, zanim odpowie „tak".

    Obie chwile MUSZĄ być już w strefie zespołu — porównujemy daty lokalne, a nie UTC.
    """
    if start.date() == end.date():
        return ""
    return f" (do {_DNI_DOPELNIACZ[end.weekday()]})"


class Tresc(str):
    """Wiadomość: tekst (sam obiekt jest napisem) + gotowy HTML dla Teams (``html``).

    Podklasa ``str``, a nie osobna struktura, z rozmysłem: tekst był dotąd JEDYNĄ postacią
    wiadomości i czyta go kilkaset asercji oraz każde miejsce, które sprawdza treść. HTML dokłada
    się obok, a jedyne miejsce, które go potrzebuje — ``to_html`` tuż przed wysyłką — rozpoznaje
    go po typie. Sklejenie ``Tresc`` z innym napisem daje zwykły ``str`` i wtedy ``to_html``
    wraca do bezpiecznego renderu tekstu, więc nie da się przez przypadek wysłać HTML-a, który
    nie odpowiada tekstowi.
    """

    html: str

    def __new__(cls, tekst: str, html: str) -> Tresc:
        obj = super().__new__(cls, tekst)
        obj.html = html
        return obj

    def __getnewargs__(self) -> tuple[str, str]:  # type: ignore[override]
        # `copy`/`deepcopy`/`pickle` (np. `dataclasses.asdict`) odtwarzają obiekt przez `__new__`
        # z tymi argumentami — bez tego rzucały TypeError.
        return (str(self), self.html)


def _p(tresc_html: str) -> str:
    return f"<p>{tresc_html}</p>"


def _b(tekst: str) -> str:
    return f"<b>{escape(tekst)}</b>"


def _tabela(naglowki: Sequence[str], wiersze: Iterable[Sequence[str]]) -> str:
    """Tabela z nagłówkiem. Komórki to TEKST — escapowany tutaj, w jednym miejscu."""
    glowa = "".join(f"<th>{escape(h)}</th>" for h in naglowki)
    cialo = "".join(
        "<tr>" + "".join(f"<td>{escape(k)}</td>" for k in wiersz) + "</tr>" for wiersz in wiersze
    )
    return f"<table><thead><tr>{glowa}</tr></thead><tbody>{cialo}</tbody></table>"


def _karta(pola: Iterable[tuple[str, str]]) -> str:
    """Tabela „etykieta → wartość" dla wiadomości bez dni i godzin. Puste wartości pomija."""
    wiersze = "".join(
        f"<tr><td><b>{escape(etykieta)}</b></td><td>{escape(wartosc)}</td></tr>"
        for etykieta, wartosc in pola
        if wartosc
    )
    return f"<table><tbody>{wiersze}</tbody></table>" if wiersze else ""


def etykieta_tygodnia(week_start: date) -> str:
    """„05.10–11.10" — ta sama postać, której używa prośba (`runtime.nudge`)."""
    return f"{week_start:%d.%m}–{week_start + timedelta(days=6):%d.%m}"


def etykieta_tygodnia_iso(week_start_iso: str) -> str:
    """Jak ``etykieta_tygodnia``, dla ``week_start`` ze stanu; nieczytelny → pusty napis."""
    try:
        return etykieta_tygodnia(date.fromisoformat(week_start_iso))
    except (TypeError, ValueError):
        return ""


_NAGLOWKI_GRAFIKU = ("Dzień", "Data", "Godziny", "Tryb pracy")


def _tryb(theme: str | None) -> str:
    return "🔵 zdalnie" if theme == "blue" else "🟢 stacjonarnie"


def _wiersze_zmian(schedule: WeekSchedule, tz: ZoneInfo) -> list[tuple[str, ...]]:
    wiersze: list[tuple[str, ...]] = []
    for sh in schedule.shifts:
        start = sh.start.astimezone(tz)
        end = sh.end.astimezone(tz)
        wiersze.append(
            (
                _DNI[start.weekday()].capitalize(),
                f"{start:%d.%m}",
                f"{start:%H:%M}–{end:%H:%M}{_do_nastepnego_dnia(start, end)}",
                _tryb(sh.theme),
            )
        )
    return wiersze


def _kolejnosc_dnia(wiersz: Sequence[str]) -> int:
    """Klucz sortowania wierszy tabeli grafiku: dzień tygodnia (nie data — przełom roku)."""
    return _DNI.index(wiersz[0].lower())


def _wiersz_dnia(week_start: date, weekday: int, godziny: str, tryb: str) -> tuple[str, ...]:
    dzien = week_start + timedelta(days=weekday)
    return (_DNI[weekday].capitalize(), f"{dzien:%d.%m}", godziny, tryb)


def _komunikat(tekst: str, tytul: str, pola: Iterable[tuple[str, str]] = ()) -> Tresc:
    """Wiadomość bez dni i godzin: pogrubiony tytuł + karta z tym, co ważne."""
    return Tresc(tekst, _p(_b(tytul)) + _karta(pola))


_TEKST_ODMOWY = (
    "OK, nie wprowadzam żadnych zmian w Twoim grafiku na ten tydzień i kończę przypominanie. "
    "Odezwę się ponownie przy kolejnym grafiku."
)


def build_declined_text(week_label: str = "") -> Tresc:
    return _komunikat(
        _TEKST_ODMOWY,
        "OK, nie wprowadzam zmian 👍",
        (
            ("Tydzień", week_label),
            ("Status", "bez zmian w grafiku — kończę przypominanie"),
            ("Co dalej", "Odezwę się ponownie przy kolejnym grafiku."),
        ),
    )


DECLINED_TEXT = build_declined_text()
APPLIED_TEXT = "Gotowe ✅ Zapisałem Twoje zmiany na przyszły tydzień. Dzięki!"
_TEKST_BLEDU_ZAPISU = (
    "Nie udało mi się zapisać wszystkiego 😕 Zajrzyj proszę do zakładki »Zmiany« w Teams i "
    "sprawdź, czego brakuje — część mogła się już zapisać. Uzupełnij tylko brakujące dni."
)


def build_write_failed_text(week_label: str = "") -> Tresc:
    return _komunikat(
        _TEKST_BLEDU_ZAPISU,
        "Nie udało mi się zapisać wszystkiego 😕",
        (
            ("Tydzień", week_label),
            ("Status", "część zmian mogła się już zapisać"),
            ("Co dalej", "Zajrzyj do zakładki »Zmiany« w Teams i uzupełnij tylko brakujące dni."),
        ),
    )


WRITE_FAILED_TEXT = build_write_failed_text()
UNCLEAR_TEXT = (
    "Nie do końca zrozumiałem 🙂 Napisz proszę np. „pon–pt 8–16” "
    "albo „w piątek 10–20, reszta bez zmian”."
)
# Konkretne warianty prośby o doprecyzowanie, wybierane enumem `powod_niejasnosci`
# (`agent.schema.POWODY_NIEJASNOSCI`). Ogólne „nie zrozumiałem" zostaje jako wartość domyślna:
# pracownik, który nie wie, CO było niejasne, odpisuje to samo drugi raz i okno wygasa.
_TEKSTY_NIEJASNOSCI: dict[str, str] = {
    "brak_godzin": UNCLEAR_TEXT,
    "nieznany_dzien": (
        "Nie wiem, o które dni chodzi 🙂 Napisz proszę wprost, np. „wtorek i czwartek wolne” "
        "albo „pon–śr 8–16”."
    ),
    "godziny_sprzeczne": (
        "Te godziny mi się nie kleją 🙂 Podaj proszę początek i koniec tego samego dnia, "
        "np. „8–16” albo „10:30–18:30”."
    ),
    "inny_tydzien": (
        "Pytam o grafik na przyszły tydzień — Twoja odpowiedź dotyczy innego terminu. "
        "Napisz proszę, jak wyglądają godziny w przyszłym tygodniu; resztą zajmie się przełożony."
    ),
    "brak_powodu_wolnego": (
        "Chciałbym wpisać Twoją nieobecność, ale nie mam do niej pasującego powodu w Shifts 😕 "
        "Napisz proszę do przełożonego — sam tego nie zapiszę."
    ),
    "poza_zakresem": (
        "Pomagam wyłącznie z grafikiem pracy 🙂 Napisz proszę, kiedy pracujesz w przyszłym "
        "tygodniu, np. „pon–pt 8–16”."
    ),
    # Pracownik zapytał o swój grafik. Nie odsyłamy go z niczym („pomagam wyłącznie z grafikiem"
    # w odpowiedzi na pytanie o grafik brzmi jak nieporozumienie) i nie czytamy mu grafiku na czacie
    # — treść od modelu nigdy nie trafia do pracownika, więc kierujemy go tam, gdzie widzi prawdę.
    "pytanie_o_grafik": (
        "Swój aktualny grafik zobaczysz w Teams w zakładce »Zmiany« 🙂 Jeśli coś się nie zgadza "
        "albo chcesz to zmienić, napisz po prostu, kiedy pracujesz — np. „pon–pt 8–16”."
    ),
    # Powód TECHNICZNY (`agent.schema.POWODY_TECHNICZNE`), nie wybór modelu: interpretacja nie
    # doszła do skutku po stronie bota. Komunikat NIE może sugerować, że winna jest odpowiedź
    # pracownika — bo nie jest. Prośba o krótszą treść nie jest przerzucaniem winy, tylko jedyną
    # radą, która realnie zwiększa szansę powodzenia następnej próby: krótsza wiadomość zbiega
    # się do decyzji w mniejszej liczbie obiegów narzędzi.
    "przerwana_interpretacja": (
        "Coś mi się zacięło przy czytaniu Twojej odpowiedzi — to po mojej stronie, nie po Twojej "
        "😕 Napisz proszę jeszcze raz, najlepiej krócej, np. „pon–pt 8–16”."
    ),
}

# Enum `agent.schema.POWODY_POMINIECIA` → powód po ludzku. Krótko, bo to wtręt w dłuższym zdaniu.
_POWODY_POMINIECIA: dict[str, str] = {
    "godziny_sprzeczne": "godziny się nie kleją",
    "godziny_niepodane": "brak godzin",
    "poza_tygodniem": "inny tydzień",
    "brak_powodu_wolnego": "brak takiego powodu w Shifts",
}
_TEKST_WYGASNIECIA = (
    "Nie dostałem odpowiedzi, więc na razie nic nie zapisuję. Kiedy będziesz gotowy/gotowa, "
    "napisz, kiedy pracujesz — wrócę do tego przy kolejnym przypomnieniu."
)


def build_expired_text(week_label: str = "") -> Tresc:
    return _komunikat(
        _TEKST_WYGASNIECIA,
        "Nie dostałem odpowiedzi",
        (
            ("Tydzień", week_label),
            ("Status", "nic nie zapisałem"),
            ("Co dalej", "Napisz, kiedy pracujesz (np. „pon–pt 8–16”) — wrócę do tego."),
        ),
    )


EXPIRED_TEXT = build_expired_text()
# Osobny komunikat, bo EXPIRED_TEXT twierdziłby NIEPRAWDĘ: tutaj odpowiedź mogła przyjść (albo
# właśnie przyszła), tylko tydzień docelowy zdążył się zacząć i nie ma już czego zapisać.
_TEKST_MINIONEGO_TYGODNIA = (
    "Tydzień, którego dotyczyło przypomnienie, już się zaczął — nie zapisuję go automatycznie. "
    "Jeśli grafik nadal wymaga uzupełnienia, napisz proszę do przełożonego."
)


def build_stale_week_text(week_label: str = "") -> Tresc:
    return _komunikat(
        _TEKST_MINIONEGO_TYGODNIA,
        "Ten tydzień już się zaczął",
        (
            ("Tydzień", week_label),
            ("Status", "nie zapisuję go automatycznie"),
            ("Co dalej", "Jeśli grafik wymaga uzupełnienia, napisz proszę do przełożonego."),
        ),
    )


STALE_WEEK_TEXT = build_stale_week_text()
# Trzeci powód domknięcia. Pracownik ODPISAŁ (czasem minutę po prośbie), zabrakło tylko „tak" —
# EXPIRED_TEXT zarzucałby mu milczenie, którego nie było.
_TEKST_BRAKU_POTWIERDZENIA = (
    "Nie doczekałem się potwierdzenia, więc nic nie zapisuję. Kiedy będziesz gotowy/gotowa, "
    "napisz, kiedy pracujesz — wrócę do tego przy kolejnym przypomnieniu."
)


def build_no_confirm_text(week_label: str = "") -> Tresc:
    return _komunikat(
        _TEKST_BRAKU_POTWIERDZENIA,
        "Nie doczekałem się potwierdzenia",
        (
            ("Tydzień", week_label),
            ("Status", "nic nie zapisałem"),
            ("Co dalej", "Napisz, kiedy pracujesz — pokażę grafik do potwierdzenia jeszcze raz."),
        ),
    )


NO_CONFIRM_TEXT = build_no_confirm_text()


def build_tydzien_zamkniety_text(week_label: str) -> Tresc:
    """Odpowiedź na wiadomość, która przyszła PO końcu tygodnia, o który pytaliśmy.

    Do 0.2.25 taka wiadomość nie dostawała NIC — temat był domknięty, tydzień minął, więc nikt
    jej nawet nie czytał. Nie zapisujemy (grafiku wstecz nie uzupełniamy — menedżer czyta go jak
    stan faktyczny), ale mówimy to wprost i wskazujemy, kto może pomóc.

    Sformułowanie jest celowo NEUTRALNE wobec tego, o czym pracownik pisał: wiadomość może
    dotyczyć minionego tygodnia albo bieżącego (jeśli ktoś uzupełnił go za tę osobę, nowej prośby
    nie było). Dlatego „nie mam otwartej sprawy", a nie „ten tydzień jest zamknięty" — i bez
    obietnicy kolejnego pytania, bo osoba z uzupełnionym grafikiem go nie dostanie.
    """
    tekst = (
        f"Nie mam teraz otwartej sprawy Twojego grafiku — ostatnia dotyczyła tygodnia {week_label} "
        "i jest już zamknięta, więc niczego nie zapisuję. Zmiany w grafiku zgłoś proszę "
        "przełożonemu."
    )
    return _komunikat(
        tekst,
        "Nie mam teraz otwartej sprawy Twojego grafiku",
        (
            ("Ostatnia sprawa", f"tydzień {week_label} — zamknięta"),
            ("Status", "niczego nie zapisuję"),
            ("Co dalej", "Zmiany w grafiku zgłoś proszę przełożonemu."),
        ),
    )


def build_self_filled_text(week_label: str) -> Tresc:
    """Podziękowanie, gdy pracownik SAM uzupełnił grafik w Shifts, zanim odpisał na czacie.

    Forma neutralna („jest już uzupełniony", nie „uzupełniłeś"), bo grafik mógł wypełnić także
    przełożony. Wysyłane bezwarunkowo — reaguje na działanie pracownika, więc milczenie byłoby
    gorsze.
    """
    return _komunikat(
        f"Widzę, że Twój grafik na tydzień {week_label} jest już uzupełniony ✅ "
        "Dziękuję! W takim razie kończę przypominanie.",
        "Grafik jest już uzupełniony ✅ Dziękuję!",
        (("Tydzień", week_label), ("Status", "uzupełniony — kończę przypominanie")),
    )


def _zdanie_terminu(termin: datetime, tz: ZoneInfo) -> str:
    lokalnie = termin.astimezone(tz)
    return (
        f"{_DNI_DOPELNIACZ[lokalnie.weekday()]} {lokalnie:%d.%m}, "
        f"godz. {lokalnie.hour}:{lokalnie:%M}"
    )


def build_nudge_text(
    member: Member,
    proposal: WeekSchedule,
    week_label: str,
    tz: ZoneInfo,
    off_weekdays: Iterable[int] = (),
    termin: datetime | None = None,
    podstawa: str = "",
) -> Tresc:
    """Zbuduj tekst przypomnienia (czysto). Godziny propozycji renderowane w strefie `tz`.

    `off_weekdays` to znane dni urlopu w docelowym tygodniu (0=pon…6=nd). Wspominamy o nich
    („o te dni nie pytam”), żeby prośba dotyczyła wyłącznie pozostałych dni i żeby pracownik nie
    zgłaszał ponownie urlopu, który już jest w grafiku. W tabeli stoją jako osobne wiersze.

    `podstawa` to zdanie „skąd ta propozycja" (`propose.Propozycja.opis_podstawy`) — od 0.2.26
    propozycja jest typowym tygodniem z kilku ostatnich, a nie kopią zeszłego, więc pracownik
    musi wiedzieć, co właściwie ogląda.

    `termin` to TERMIN ODPOWIEDZI policzony przez kod (`lifecycle.termin_dla_nowej_prosby`), nie
    liczba wpisana w tę stałą. Nazwa jest tu istotna, nie kosmetyczna: `termin_dla_nowej_prosby`
    to `max(termin_kalendarzowy, teraz + min_h)`, a to właśnie ten drugi składnik gwarantuje, że
    obietnica w treści nie wypadnie w przeszłości przy ujemnym przesunięciu (N39).

    Do 0.2.12 treść nie mówiła o terminie nic, więc obietnica i zachowanie
    runtime'u mogły się rozjechać bez śladu — a rozjazd wychodzi dopiero w chwili, w której ktoś
    traci tydzień grafiku (pozycja B7 planu). `None` znaczy „nie da się wyznaczyć" (uszkodzony
    `week_start`) i wtedy zdania o terminie po prostu nie ma: lepiej nie obiecać nic, niż obiecać
    datę wziętą z niczego. Zgodność z N13 zostaje — tekst jest stałą z tego modułu, zmienna jest
    tylko WARTOŚĆ liczona przez kod.
    """
    parts = member.display_name.split()
    first_name = parts[0] if parts else member.display_name
    powitanie = f"Cześć {first_name}! 👋"
    wstep = f"Nie masz jeszcze uzupełnionych zmian na przyszły tydzień ({week_label})."
    lines = [powitanie, wstep]
    html = [
        _p(escape(powitanie)),
        _p(f"Nie masz jeszcze uzupełnionych zmian na przyszły tydzień ({_b(week_label)})."),
    ]
    off = sorted(off_weekdays)
    wiersze_wolne = [
        _wiersz_dnia(proposal.week_start, d, "🏖️ wolne (już w grafiku)", "—") for d in off
    ]
    if off:
        dni = ", ".join(_DNI[d] for d in off)
        lines.append(f"Widzę, że masz wtedy wolne: {dni} — o te dni nie pytam.")
    if proposal.is_empty:
        prosba = (
            "napisz proszę, kiedy pracujesz w pozostałe dni (np. „pon–czw 8–16”)."
            if off
            else "napisz proszę, kiedy pracujesz (np. „pon–pt 8–16”)."
        )
        zdanie = f"Nie znalazłem Twojego grafiku z ostatnich tygodni — {prosba}"
        lines.append(zdanie)
        if wiersze_wolne:
            html.append(_p("Widzę, że w tym tygodniu masz już wolne — o te dni nie pytam:"))
            html.append(_tabela(_NAGLOWKI_GRAFIKU, wiersze_wolne))
        html.append(_p(escape(zdanie)))
    else:
        naglowek = f"Proponuję grafik — {podstawa}:" if podstawa else "Proponuję taki grafik:"
        lines.append(naglowek)
        for sh in proposal.shifts:
            start = sh.start.astimezone(tz)
            end = sh.end.astimezone(tz)
            lines.append(
                f"• {_DNI[start.weekday()]} {start:%H:%M}–{end:%H:%M}"
                f"{_do_nastepnego_dnia(start, end)}"
            )
        instrukcja = (
            "Odpisz „ok”, żeby zapisać tę propozycję, albo napisz, co zmienić "
            "(np. „w piątek 10–20, reszta bez zmian” lub „w piątek mnie nie będzie”)."
        )
        lines.append(instrukcja)
        wiersze = sorted(
            [*_wiersze_zmian(proposal, tz), *wiersze_wolne],
            key=_kolejnosc_dnia,
        )
        tytul = f"Proponowany grafik — {podstawa}:" if podstawa else "Proponowany grafik:"
        html.append(_p(_b(tytul)))
        html.append(_tabela(_NAGLOWKI_GRAFIKU, wiersze))
        html.append(
            _p(
                f"Odpisz {_b('„ok”')}, żeby zapisać tę propozycję, albo napisz, co zmienić "
                "(np. „w piątek 10–20, reszta bez zmian” lub „w piątek mnie nie będzie”)."
            )
        )
    if termin is not None:
        # Zdanie o terminie stoi NA KOŃCU, po propozycji: pracownik ma najpierw zobaczyć, o co
        # jest pytany, a termin przeczytać jako ramę. Wartość liczy kod, tekst jest stałą (N13).
        kiedy = _zdanie_terminu(termin, tz)
        lines.append(
            f"Czekam na odpowiedź do {kiedy} — potem kończę przypominanie "
            f"o tym tygodniu i odezwę się przy kolejnym grafiku."
        )
        html.append(
            _p(
                f"⏰ Czekam na odpowiedź do {_b(kiedy)} — potem kończę przypominanie "
                "o tym tygodniu i odezwę się przy kolejnym grafiku."
            )
        )
    return Tresc("\n".join(lines), "".join(html))


def build_przypomnienie_text(
    week_label: str, termin: datetime | None, tz: ZoneInfo, *, ma_propozycje: bool
) -> Tresc:
    """Jedno przypomnienie dla pracownika, który po prośbie nie napisał ani słowa (pozycja D5).

    KRÓTKIE z rozmysłem. Pierwsza wiadomość niosła gotowiec i pełne instrukcje, i została
    zignorowana; powtórzenie jej w całości nie dokłada informacji, a wygląda jak nagabywanie.
    Ta ma przypomnieć o sprawie i pokazać najkrótszą drogę do jej zamknięcia.

    ``ma_propozycje`` rozstrzyga, czy ta najkrótsza droga w ogóle istnieje: przy pustym gotowcu
    (brak grafiku z ostatnich tygodni) nie ma czego potwierdzić, więc zdanie „odpisz »ok«, żeby
    zapisać" byłoby nieprawdziwe — ten sam podział, który robi ``build_nudge_text``.

    ``termin`` liczy KOD (**B7**), a ``None`` znaczy „nie da się wyznaczyć" i wtedy zdania o nim
    po prostu nie ma: lepiej nie obiecać nic, niż obiecać datę wziętą z niczego. Zgodność z **N13**
    zostaje — tekst jest stałą tego modułu, zmienne są wyłącznie WARTOŚCI.
    """
    lines = [
        f"Przypominam o grafiku na tydzień {week_label} — nie mam jeszcze Twojej odpowiedzi 🙂"
    ]
    if ma_propozycje:
        co_zrobic = (
            "Wystarczy odpisać „ok”, żeby zapisać propozycję z poprzedniej wiadomości, "
            "albo napisz, co zmienić (np. „w piątek 10–20, reszta bez zmian”)."
        )
    else:
        co_zrobic = "Napisz proszę, kiedy pracujesz (np. „pon–pt 8–16”)."
    lines.append(co_zrobic)
    kiedy = _zdanie_terminu(termin, tz) if termin is not None else ""
    if kiedy:
        lines.append(f"Czekam do {kiedy} — potem kończę przypominanie o tym tygodniu.")
    html = (
        _p(_b("Przypominam o grafiku 🙂"))
        + _karta(
            (
                ("Tydzień", week_label),
                ("Status", "czekam na Twoją odpowiedź"),
                ("Czekam do", kiedy),
            )
        )
        + _p(escape(co_zrobic))
    )
    return Tresc("\n".join(lines), html)


def describe_schedule(schedule: WeekSchedule, tz: ZoneInfo) -> str:
    """Opis grafiku do potwierdzenia z trybem pracy jako emotka: 🟢 stacjonarnie, 🔵 zdalnie.

    Kolor emotki idzie za kolorem Shifts (`theme`): green/None → 🟢 (stacjonarnie), blue → 🔵
    (zdalnie), np. „pon 08:00–16:00 🟢”. Zmiana przechodząca przez północ dostaje dopisek
    „(do soboty)” — to na tej wiadomości pracownik mówi „tak”, więc musi z niej wyczytać
    DOKŁADNIE to, co zostanie zapisane (patrz `_do_nastepnego_dnia`).
    """
    parts = []
    for sh in schedule.shifts:
        start = sh.start.astimezone(tz)
        end = sh.end.astimezone(tz)
        mode = "🔵" if sh.theme == "blue" else "🟢"  # blue = zdalnie, green/None = stacjonarnie
        parts.append(
            f"{_DNI_SKROT[start.weekday()]} {start:%H:%M}–{end:%H:%M}"
            f"{_do_nastepnego_dnia(start, end)} {mode}"
        )
    return ", ".join(parts)


def describe_time_off(entries: Iterable[dict[str, Any]]) -> str:
    """Opis dni wolnych do potwierdzenia, np. „pt: Urlop, wt: Zwolnienie lekarskie”.

    Używa FAKTYCZNEJ nazwy powodu z rozstrzygniętego wpisu (``reason_name``), więc pracownik
    widzi dokładnie to, co zostanie zapisane.
    """
    parts = []
    for item in entries:
        weekday = int(item["weekday"])
        name = str(item.get("reason_name") or "Nieobecność")
        parts.append(f"{_DNI_SKROT[weekday]}: {name}")
    return ", ".join(parts)


def build_confirm_text(
    schedule: WeekSchedule,
    time_off: Iterable[dict[str, Any]],
    tz: ZoneInfo,
    pominiete: Iterable[dict[str, Any]] = (),
    teraz: datetime | None = None,
) -> Tresc:
    """Prośba o potwierdzenie przed zapisem (spirit ADR 0006 — zapis tylko po »tak«).

    ``pominiete`` to dni, o których pracownik napisał, ale których NIE zapisujemy (np. sprzeczne
    godziny). Dopisujemy je do treści, bo bez tego potwierdzenie wyglądałoby na komplet: pracownik
    odpowiadał o pięciu dniach, potwierdza cztery i nie ma jak zauważyć, że jeden wypadł.

    ``teraz`` (od 0.2.26) oznacza w tabeli dni, które już MINĘŁY — zapis i tak je pominie
    (``lifecycle.still_writable``), a pracownik ma to widzieć, ZANIM odpowie „tak", nie po fakcie.

    **Tydzień i daty stoją w treści zawsze** (od 0.2.26). Wcześniej potwierdzenie mówiło „pon
    08:00–16:00" bez dat, a klucz stanu to osoba, nie tydzień — kto po nowej piątkowej prośbie
    pisał o tygodniu właśnie mijającym, dostawał potwierdzenie bez żadnej wskazówki, że jego
    godziny pójdą do grafiku NASTĘPNEGO tygodnia.
    """
    time_off = list(time_off)
    pominiete = list(pominiete)
    tydzien = etykieta_tygodnia(schedule.week_start)
    segments = []
    if not schedule.is_empty:
        segments.append(f"grafik: {describe_schedule(schedule, tz)}")
    if time_off:
        segments.append(f"czas wolny: {describe_time_off(time_off)}")
    prosba = (
        f"Zapiszę {'; '.join(segments)} (tydzień {tydzien}). "
        "Potwierdź „tak”, żeby zapisać, albo napisz poprawkę."
    )
    ostrzezenie = describe_pominiete(pominiete)
    tekst = f"{prosba}\n{ostrzezenie}" if ostrzezenie else prosba

    wiersze = _wiersze_zmian(schedule, tz)
    if teraz is not None:
        wiersze = [
            (w[0], w[1], f"{w[2]} ⏪ już minął — nie zapiszę", w[3]) if sh.end <= teraz else w
            for w, sh in zip(wiersze, schedule.shifts, strict=True)
        ]
    for item in time_off:
        wd = int(item["weekday"])
        nazwa = str(item.get("reason_name") or "Nieobecność")
        wiersze.append(_wiersz_dnia(schedule.week_start, wd, f"🏖️ wolne: {nazwa}", "—"))
    for wpis in pominiete:
        dzien = wpis.get("weekday")
        if isinstance(dzien, int) and 0 <= dzien <= 6:
            powod = _POWODY_POMINIECIA.get(str(wpis.get("powod")), "nie udało się odczytać")
            wiersze.append(_wiersz_dnia(schedule.week_start, dzien, "⚠️ nie zapisuję", powod))
    wiersze.sort(key=_kolejnosc_dnia)
    html = (
        _p(_b(f"Zapiszę grafik na tydzień {tydzien}:"))
        + _tabela(_NAGLOWKI_GRAFIKU, wiersze)
        + (_p(escape(ostrzezenie)) if ostrzezenie else "")
        + _p(f"Potwierdź {_b('„tak”')}, żeby zapisać, albo napisz poprawkę.")
    )
    return Tresc(tekst, html)


def describe_pominiete(pominiete: Iterable[dict[str, Any]]) -> str:
    """Zdanie o dniach, których NIE zapisujemy — albo pusty napis, gdy nie ma takich dni.

    Wcześniej taki dzień znikał bez śladu: `build_schedule` robiło `continue`, a pracownik i
    menedżer widzieli dziurę w grafiku dopiero po fakcie. Powód podajemy własnymi słowami —
    pole `powod` to enum z `agent.schema`, nie tekst wygenerowany przez model.
    """
    opisy = []
    for wpis in pominiete:
        weekday = wpis.get("weekday")
        if not isinstance(weekday, int) or not 0 <= weekday <= 6:
            continue
        powod = _POWODY_POMINIECIA.get(str(wpis.get("powod")), "nie udało się odczytać")
        opisy.append(f"{_DNI_SKROT[weekday]} ({powod})")
    if not opisy:
        return ""
    return (
        f"Nie zapisuję tych dni: {', '.join(opisy)}. "
        "Napisz je proszę jeszcze raz, jeśli mają być w grafiku."
    )


def build_nic_do_zapisania_text(juz_w_grafiku: Iterable[int], week_label: str = "") -> Tresc:
    """Cały potwierdzony komplet był już w grafiku — nie zapisano NIC i trzeba to powiedzieć wprost.

    Kuszące jest użycie tu podziękowania ze ścieżki samouzupełnienia („grafik jest już uzupełniony,
    dziękuję!"), bo stan końcowy wygląda tak samo. Byłoby to jednak mylące w najgorszy możliwy
    sposób: pracownik przed chwilą potwierdził KONKRETNE godziny albo urlop, dostałby podziękowanie
    i zamknięcie tematu, a z jego odpowiedzi nie weszłoby do grafiku nic — i nikt by mu tego nie
    powiedział. Dni wymieniamy z nazwy, bo w tej gałęzi zawsze jakieś są.
    """
    dni = ", ".join(_DNI_SKROT[d] for d in sorted(juz_w_grafiku) if 0 <= d <= 6)
    wykaz = f" ({dni})" if dni else ""
    return _komunikat(
        f"Te dni miałeś/miałaś już uzupełnione w grafiku{wykaz}, więc niczego nie zmieniałem — "
        "nie chcę dopisywać ich drugi raz. Jeśli zapisane godziny się nie zgadzają, napisz proszę "
        "do przełożonego. Kończę przypominanie o tym tygodniu.",
        "Te dni są już w grafiku — niczego nie zmieniałem",
        (
            ("Tydzień", week_label),
            ("Dni już w grafiku", dni),
            ("Co dalej", "Jeśli coś się nie zgadza, napisz proszę do przełożonego."),
        ),
    )


def build_applied_text(
    *,
    minione: int = 0,
    juz_w_grafiku: Iterable[int] = (),
    zapisane: WeekSchedule | None = None,
    wolne: Iterable[tuple[int, str]] = (),
    tz: ZoneInfo | None = None,
) -> Tresc:
    """Potwierdzenie zapisu, wymieniające dni, które do grafiku NIE trafiły — i dlaczego.

    Pracownik potwierdził konkretny komplet dni, a zapisać można było mniej. Powody są DWA i mówią
    zupełnie co innego, więc jeden wspólny komunikat („zapisałem część") byłby dla niego
    bezużyteczny:

    - ``minione`` — dni, które zdążyły się skończyć, zanim padło „tak" (odsiewa je
      ``lifecycle.still_writable``). Grafiku wstecz nie uzupełniamy, bo menedżer czyta go jak stan
      faktyczny.
    - ``juz_w_grafiku`` — dni, które w międzyczasie ktoś uzupełnił (odsiewa je
      ``detect.drop_already_covered``). Tutaj NIE ma dziury w grafiku, jest cudza wersja tego dnia,
      a zapis drugiego kompletu byłby nieodwracalny.

    Puste oba → zwykłe ``APPLIED_TEXT``: obietnica „zapisałem Twoje zmiany" jest wtedy w całości
    prawdziwa i nie ma po co jej rozwadniać.

    ``zapisane``/``wolne``/``tz`` (od 0.2.26) to to, co FAKTYCZNIE poszło do Shifts — w HTML
    stoi z tego tabela, żeby pracownik widział dokładnie, co trafiło do grafiku.
    """
    juz = sorted(d for d in juz_w_grafiku if isinstance(d, int) and 0 <= d <= 6)
    if not minione and not juz:
        lines = [APPLIED_TEXT]
    else:
        lines = ["Gotowe ✅ Zapisałem to, czego jeszcze nie było w Twoim grafiku."]
    uwagi = []
    if juz:
        uwagi.append(
            f"Te dni były już uzupełnione, więc ich nie zmieniałem: "
            f"{', '.join(_DNI_SKROT[d] for d in juz)}. Jeśli coś się w nich nie zgadza, "
            "napisz proszę do przełożonego."
        )
    if minione:
        uwagi.append(
            "Dni, które zdążyły już minąć, nie trafiły do grafiku — jeśli mają tam być, "
            "napisz proszę do przełożonego."
        )
    lines += uwagi
    html = _p(_b(lines[0]))
    if zapisane is not None and tz is not None:
        wiersze = _wiersze_zmian(zapisane, tz)
        for wd, nazwa in wolne:
            wiersze.append(_wiersz_dnia(zapisane.week_start, wd, f"🏖️ wolne: {nazwa}", "—"))
        if wiersze:
            wiersze.sort(key=_kolejnosc_dnia)
            html += _p(f"Tydzień {_b(etykieta_tygodnia(zapisane.week_start))} — zapisane:")
            html += _tabela(_NAGLOWKI_GRAFIKU, wiersze)
    html += "".join(_p(escape(u)) for u in uwagi)
    return Tresc("\n".join(lines), html)


_PRZYKLADY = (
    ("„pon–pt 8–16”", "zapiszę cały tydzień w tych godzinach"),
    ("„w piątek 10–20, reszta bez zmian”", "zmienię jeden dzień"),
    ("„w piątek mnie nie będzie”", "wpiszę dzień wolny"),
    ("„we wtorek zdalnie”", "zmienię tryb pracy 🔵"),
)
# Powody, przy których pracownik ma POPRAWIĆ odpowiedź — tylko tam tabela przykładów pomaga.
_Z_PRZYKLADAMI = {
    "",
    "brak_godzin",
    "nieznany_dzien",
    "godziny_sprzeczne",
    "poza_zakresem",
    "przerwana_interpretacja",
}


def build_unclear_text(powod: str = "") -> Tresc:
    """Prośba o doprecyzowanie — możliwie KONKRETNA, zamiast ogólnego „nie zrozumiałem".

    ``powod`` to enum z ``agent.schema`` (nie tekst od modelu), więc wybór komunikatu nie jest
    kanałem, którym cokolwiek z odpowiedzi pracownika mogłoby do niego wrócić. Nieznana wartość
    degraduje się do komunikatu ogólnego. Tam, gdzie pracownik ma poprawić odpowiedź, HTML
    dokłada tabelę gotowych przykładów — to one realnie skracają drugą próbę.
    """
    tekst = _TEKSTY_NIEJASNOSCI.get(powod, UNCLEAR_TEXT)
    klucz = powod if powod in _TEKSTY_NIEJASNOSCI else ""
    html = _p(escape(tekst))
    if klucz in _Z_PRZYKLADAMI:
        html += _tabela(("Napisz na przykład", "Co zrobię"), _PRZYKLADY)
    return Tresc(tekst, html)


# Sufit bloków w jednej wiadomości. Tygodni w stanie przybywa monotonicznie: retencja sprząta
# tylko wpisy TERMINALNE z kotwicą, a `APPLYING` (N4) i wpisy nierozpoznane zostają bezterminowo.
# Bez sufitu raport po pół roku byłby ścianą tekstu, której nikt nie czyta — a to jedyna
# wiadomość o stanie usługi, jaką administrator dostaje. Ta sama logika co
# `_MAX_ZAWIESZONYCH_W_ALERCIE` w `runtime.service`.
MAX_TYGODNI_W_PODSUMOWANIU = 8


@dataclass(frozen=True)
class LiczbyTygodnia:
    """Stan spraw JEDNEGO tygodnia docelowego — wejście do podsumowania.

    Struktura, nie mapa statusów, bo `messages` celowo nie zna słownictwa `state`. Nazwy pól są
    nazwami POZYCJI RAPORTU, a przełożenie statusów na nie należy do `runtime.service` — zgodności
    jednego z drugim pilnuje `tests/test_kontrakty.py`.
    """

    week_start: str
    oczekuje: int = 0
    do_potwierdzenia: int = 0
    zapisane: int = 0
    odmowy: int = 0
    wygasle: int = 0
    samodzielne: int = 0
    niepotwierdzone: int = 0
    nierozpoznane: int = 0
    # Pozycje NIEWYWODZONE ze statusu — dlatego nie ma ich w `service.STATUS_DO_POZYCJI` i dlatego
    # strażnik kontraktu ich nie obejmuje (iteruje po wpisach mapy, nie po polach tej klasy).
    #
    # Skuteczność przypomnienia (D5): ile wpisów dostało przypomnienie i ile z nich skończyło się
    # uzupełnionym grafikiem. Bez tej pary sobotnie zagadnięcie jest dźwignią, której działania
    # NIE WIDAĆ — a to był główny zarzut audytu wobec całego produktu.
    przypomnienia: int = 0
    przypomnienia_skuteczne: int = 0
    # Skuteczność wznowienia (D5, połowa druga): ile domkniętych tematów wróciło do obiegu, bo
    # pracownik odezwał się po terminie, i ile z nich skończyło się uzupełnionym grafikiem.
    wznowione: int = 0
    wznowione_skuteczne: int = 0
    # Miara jakości interpretacji (E4, §10.4). Liczba bez mianownika myli, więc idą parą.
    interpretacje: int = 0
    niejasnosci: int = 0

    @property
    def domkniety(self) -> bool:
        """Czy usługa sama nic już z tym tygodniem nie zrobi I nic w nim nie czeka na człowieka.

        Trzy pozycje wykluczają „domknięty", każda z innego powodu:

        - `oczekuje` i `do_potwierdzenia` — rozmowa trwa, usługa ma jeszcze co robić;
        - `niepotwierdzone` (APPLYING) — status jest terminalny (N4), więc usługa faktycznie nic
          już nie zrobi, ale człowiek MUSI sprawdzić grafik ręcznie;
        - `nierozpoznane` — wpisy, których to wydanie nie umie zaklasyfikować. Nie wiadomo o nich
          nic, a „nic nie wiadomo" nigdy nie znaczy „skończone".

        `wygasle` NIE wyklucza: sprawa tygodnia jest zamknięta, choć administrator może chcieć do
        kogoś napisać. To jest granica między „usługa skończyła" a „warto rzucić okiem" i przebiega
        właśnie tutaj — inaczej każdy tydzień z jedną niedoszłą odpowiedzią wisiałby jako „w toku"
        w nieskończoność i etykieta przestałaby cokolwiek znaczyć.
        """
        return not (
            self.oczekuje or self.do_potwierdzenia or self.niepotwierdzone or self.nierozpoznane
        )


def build_summary_text(*, tygodnie: Iterable[LiczbyTygodnia], nastepny_przebieg: str) -> Tresc:
    """Podsumowanie przebiegu dla administratora — jednocześnie sygnał życia usługi.

    Wysyłane po KAŻDYM przebiegu, także gdy nikogo nie trzeba było zagadnąć: „zero próśb" jest
    informacją, natomiast cisza oznacza, że usługa nie żyje. W instalacji bez monitoringu brak tej
    wiadomości w piątek wieczorem jest jedynym sygnałem awarii.

    **Liczby są rozbite po tygodniu docelowym.** Jedna zbiorcza tabela mieszała świeże
    `awaiting_reply` z tygodnia właśnie otwartego z terminalnymi resztkami tygodnia zamykanego,
    więc suma „oczekuje: 4" nie mówiła nic o tym, czy poprzedni tydzień się domknął — a to jest
    jedyna informacja o tym, jaką administrator w ogóle dostaje. Tygodnie idą od najnowszego:
    pierwszy blok to skutek przebiegu, który właśnie się odbył.
    """
    bloki = sorted(tygodnie, key=lambda t: t.week_start, reverse=True)
    pominiete, bloki = bloki[MAX_TYGODNI_W_PODSUMOWANIU:], bloki[:MAX_TYGODNI_W_PODSUMOWANIU]
    lines = ["Podsumowanie przebiegu powiadomień:"]
    if not bloki:
        # Pusty stan to nie brak wiadomości: „nikogo nie trzeba było zagadnąć" jest informacją,
        # a cisza znaczy „usługa nie żyje". Te dwie rzeczy muszą wyglądać inaczej.
        lines.append("• brak spraw w toku — nikogo nie trzeba było zagadnąć")
    for t in bloki:
        lines.append("")
        lines.append(f"Tydzień od {t.week_start} — {'domknięty' if t.domkniety else 'w toku'}:")
        lines.append(f"• oczekuje na odpowiedź: {t.oczekuje}")
        lines.append(f"• czeka na potwierdzenie: {t.do_potwierdzenia}")
        if t.niepotwierdzone:
            # Osobna pozycja, bo to jedyna kategoria wymagająca RĘCZNEGO działania: pracownik
            # potwierdził zmiany, a zapis do Shifts nie doszedł do skutku. Doliczanie ich do
            # „zapisanych grafików" sprawiało, że raport twierdził coś nieprawdziwego.
            lines.append(
                f"• ⚠️ potwierdzone, ale NIEZAPISANE (do ręcznego uzupełnienia): {t.niepotwierdzone}"
            )
        lines.append(f"• zapisane grafiki: {t.zapisane}")
        lines.append(f"• odmowy: {t.odmowy}")
        # NIE „wygasłe bez odpowiedzi": w EXPIRED lądują trzy różne rzeczy — prawdziwa cisza, brak
        # potwierdzenia po odpowiedzi i domknięcie „tydzień już trwa". Administrator decyduje na
        # tej podstawie, do kogo napisać ręcznie, więc etykieta musi być prawdziwa dla wszystkich.
        lines.append(f"• zamknięte bez zapisu: {t.wygasle}")
        lines.append(f"• uzupełnione samodzielnie: {t.samodzielne}")
        if t.przypomnienia:
            # Odpowiedź na pytanie „czy sobotnie przypomnienie ma sens" — jedyna liczba, po której
            # da się je ocenić bez czytania logu (założenie A12: logów nikt nie czyta).
            lines.append(
                f"• przypomnienia (sobota): {t.przypomnienia}, "
                f"z tego z uzupełnionym grafikiem: {t.przypomnienia_skuteczne}"
            )
        if t.wznowione:
            # Druga dźwignia D5 — bez tej liczby wznowienie byłoby zmianą, o której wiadomo tylko
            # tyle, że weszła.
            lines.append(
                f"• wznowione po terminie: {t.wznowione}, "
                f"z tego z uzupełnionym grafikiem: {t.wznowione_skuteczne}"
            )
        if t.interpretacje:
            # Odsetek, nie sama liczba: „trzy niejasności" u osoby, która napisała dziesięć razy,
            # znaczy co innego niż u tej, która napisała raz (E4 mówi wprost o ODSETKU).
            procent = round(100 * t.niejasnosci / t.interpretacje)
            lines.append(
                f"• odpowiedzi zinterpretowane: {t.interpretacje}, "
                f"w tym niejasne: {t.niejasnosci} ({procent}%)"
            )
        if t.nierozpoznane:
            # Osobna pozycja, bo bez niej takie wpisy znikały z LICZB, zostawiając blok z samymi
            # zerami — czyli tydzień opisany jako „domknięty" z powodu braku informacji o nim.
            lines.append(
                f"• ⚠️ wpisy nierozpoznane przez tę wersję (sprawdź, czy stan nie pochodzi "
                f"z nowszego wydania): {t.nierozpoznane}"
            )
    if pominiete:
        # Ten sam sufit co w alercie o zawieszonych zapisach: wiadomość, którą trzeba przewijać,
        # przestaje być czytana. Ucinamy od NAJSTARSZYCH, bo pierwszy blok ma być skutkiem
        # przebiegu, który właśnie się odbył — i mówimy wprost, ile zostało za kadrem.
        lines.append("")
        lines.append(
            f"…oraz {len(pominiete)} starszych tygodni z wpisami w stanie "
            f"(najstarszy od {min(t.week_start for t in pominiete)})."
        )
    lines.append("")
    lines.append(f"Następny przebieg: {nastepny_przebieg}")
    return Tresc("\n".join(lines), _podsumowanie_html(bloki, pominiete, nastepny_przebieg))


def _podsumowanie_html(
    bloki: Sequence[LiczbyTygodnia], pominiete: Sequence[LiczbyTygodnia], nastepny: str
) -> str:
    """Podsumowanie jako JEDNA tabela: wiersze to pozycje raportu, kolumny to tygodnie.

    Transpozycja celowa: tygodni jest zwykle jeden–dwa, pozycji dziesięć. Kolumna na pozycję
    dawałaby tabelę szerszą niż okno czatu; kolumna na tydzień czyta się jak porównanie
    „ten tydzień kontra poprzedni". Wiersze opcjonalne (przypomnienia, wznowienia, niejasności,
    ⚠️) pojawiają się tylko wtedy, gdy w KTÓRYMKOLWIEK tygodniu jest co w nich pokazać.
    """
    html = _p(_b("Podsumowanie przebiegu powiadomień"))
    if not bloki:
        html += _p("Brak spraw w toku — nikogo nie trzeba było zagadnąć.")
    else:

        def wiersz(nazwa: str, wartosci: Iterable[object]) -> tuple[str, ...]:
            return (nazwa, *(str(w) for w in wartosci))

        def procent(t: LiczbyTygodnia) -> str:
            if not t.interpretacje:
                return "—"
            return (
                f"{t.interpretacje} (niejasne {t.niejasnosci}, "
                f"{round(100 * t.niejasnosci / t.interpretacje)}%)"
            )

        wiersze = [
            wiersz("Stan", ("✅ domknięty" if t.domkniety else "⏳ w toku" for t in bloki)),
            wiersz("Oczekuje na odpowiedź", (t.oczekuje for t in bloki)),
            wiersz("Czeka na potwierdzenie", (t.do_potwierdzenia for t in bloki)),
        ]
        if any(t.niepotwierdzone for t in bloki):
            wiersze.append(
                wiersz("⚠️ Potwierdzone, NIEZAPISANE", (t.niepotwierdzone for t in bloki))
            )
        wiersze += [
            wiersz("Zapisane grafiki", (t.zapisane for t in bloki)),
            wiersz("Odmowy", (t.odmowy for t in bloki)),
            wiersz("Zamknięte bez zapisu", (t.wygasle for t in bloki)),
            wiersz("Uzupełnione samodzielnie", (t.samodzielne for t in bloki)),
        ]
        if any(t.przypomnienia for t in bloki):
            wiersze.append(
                wiersz(
                    "Przypomnienia (skuteczne)",
                    (f"{t.przypomnienia} ({t.przypomnienia_skuteczne})" for t in bloki),
                )
            )
        if any(t.wznowione for t in bloki):
            wiersze.append(
                wiersz(
                    "Wznowione po terminie (skuteczne)",
                    (f"{t.wznowione} ({t.wznowione_skuteczne})" for t in bloki),
                )
            )
        if any(t.interpretacje for t in bloki):
            wiersze.append(wiersz("Odpowiedzi zinterpretowane", (procent(t) for t in bloki)))
        if any(t.nierozpoznane for t in bloki):
            wiersze.append(wiersz("⚠️ Wpisy nierozpoznane", (t.nierozpoznane for t in bloki)))
        naglowki = ("", *(f"Tydzień od {t.week_start}" for t in bloki))
        html += _tabela(naglowki, wiersze)
    if pominiete:
        html += _p(
            escape(
                f"…oraz {len(pominiete)} starszych tygodni z wpisami w stanie "
                f"(najstarszy od {min(t.week_start for t in pominiete)})."
            )
        )
    html += _p(f"Następny przebieg: {_b(nastepny)}")
    return html


def to_html(text: str) -> str:
    """HTML wiadomości dla Teams: gotowy z ``Tresc``, a dla zwykłego tekstu — bezpieczny render.

    Zwykły ``str`` to dziś wyłącznie ścieżka awaryjna (np. tekst sklejony z ``Tresc``): każda
    linia escapowana, podziały jako ``<br>``.
    """
    if isinstance(text, Tresc):
        return text.html
    return "<br>".join(escape(line) for line in text.split("\n"))
