"""Treść powiadomienia 1:1 (czysta logika) + minimalny render do HTML dla Graph."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
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


DECLINED_TEXT = (
    "OK, nie wprowadzam żadnych zmian w Twoim grafiku na ten tydzień i kończę przypominanie. "
    "Odezwę się ponownie przy kolejnym grafiku."
)
APPLIED_TEXT = "Gotowe ✅ Zapisałem Twoje zmiany na przyszły tydzień. Dzięki!"
WRITE_FAILED_TEXT = (
    "Nie udało mi się zapisać wszystkiego 😕 Zajrzyj proszę do zakładki »Zmiany« w Teams i "
    "sprawdź, czego brakuje — część mogła się już zapisać. Uzupełnij tylko brakujące dni."
)
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
EXPIRED_TEXT = (
    "Nie dostałem odpowiedzi, więc na razie nic nie zapisuję. Kiedy będziesz gotowy/gotowa, "
    "napisz, kiedy pracujesz — wrócę do tego przy kolejnym przypomnieniu."
)
# Osobny komunikat, bo EXPIRED_TEXT twierdziłby NIEPRAWDĘ: tutaj odpowiedź mogła przyjść (albo
# właśnie przyszła), tylko tydzień docelowy zdążył się zacząć i nie ma już czego zapisać.
STALE_WEEK_TEXT = (
    "Tydzień, którego dotyczyło przypomnienie, już się zaczął — nie zapisuję go automatycznie. "
    "Jeśli grafik nadal wymaga uzupełnienia, napisz proszę do przełożonego."
)
# Trzeci powód domknięcia. Pracownik ODPISAŁ (czasem minutę po prośbie), zabrakło tylko „tak" —
# EXPIRED_TEXT zarzucałby mu milczenie, którego nie było.
NO_CONFIRM_TEXT = (
    "Nie doczekałem się potwierdzenia, więc nic nie zapisuję. Kiedy będziesz gotowy/gotowa, "
    "napisz, kiedy pracujesz — wrócę do tego przy kolejnym przypomnieniu."
)


def build_self_filled_text(week_label: str) -> str:
    """Podziękowanie, gdy pracownik SAM uzupełnił grafik w Shifts, zanim odpisał na czacie.

    Forma neutralna („jest już uzupełniony", nie „uzupełniłeś"), bo grafik mógł wypełnić także
    przełożony. Wysyłane bezwarunkowo — reaguje na działanie pracownika, więc milczenie byłoby
    gorsze.
    """
    return (
        f"Widzę, że Twój grafik na tydzień {week_label} jest już uzupełniony ✅ "
        "Dziękuję! W takim razie kończę przypominanie."
    )


def build_nudge_text(
    member: Member,
    proposal: WeekSchedule,
    week_label: str,
    tz: ZoneInfo,
    off_weekdays: Iterable[int] = (),
    termin: datetime | None = None,
) -> str:
    """Zbuduj tekst przypomnienia (czysto). Godziny propozycji renderowane w strefie `tz`.

    `off_weekdays` to znane dni urlopu w docelowym tygodniu (0=pon…6=nd). Wspominamy o nich
    („o te dni nie pytam”), żeby prośba dotyczyła wyłącznie pozostałych dni i żeby pracownik nie
    zgłaszał ponownie urlopu, który już jest w grafiku.

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
    lines = [
        f"Cześć {first_name}! 👋",
        f"Nie masz jeszcze uzupełnionych zmian na przyszły tydzień ({week_label}).",
    ]
    off = sorted(off_weekdays)
    if off:
        dni = ", ".join(_DNI[d] for d in off)
        lines.append(f"Widzę, że masz wtedy wolne: {dni} — o te dni nie pytam.")
    if proposal.is_empty:
        prosba = (
            "napisz proszę, kiedy pracujesz w pozostałe dni (np. „pon–czw 8–16”)."
            if off
            else "napisz proszę, kiedy pracujesz (np. „pon–pt 8–16”)."
        )
        lines.append(f"Nie znalazłem Twojego grafiku z zeszłego tygodnia — {prosba}")
    else:
        lines.append("W zeszłym tygodniu Twój grafik wyglądał tak:")
        for sh in proposal.shifts:
            start = sh.start.astimezone(tz)
            end = sh.end.astimezone(tz)
            lines.append(
                f"• {_DNI[start.weekday()]} {start:%H:%M}–{end:%H:%M}"
                f"{_do_nastepnego_dnia(start, end)}"
            )
        lines.append(
            "Odpisz „ok”, żeby powtórzyć to samo, albo napisz, co zmienić "
            "(np. „w piątek 10–20, reszta bez zmian” lub „w piątek mnie nie będzie”)."
        )
    if termin is not None:
        # Zdanie o terminie stoi NA KOŃCU, po propozycji: pracownik ma najpierw zobaczyć, o co
        # jest pytany, a termin przeczytać jako ramę. Wartość liczy kod, tekst jest stałą (N13).
        lokalnie = termin.astimezone(tz)
        lines.append(
            f"Czekam na odpowiedź do {_DNI_DOPELNIACZ[lokalnie.weekday()]} "
            f"{lokalnie:%d.%m}, godz. {lokalnie.hour}:{lokalnie:%M} — potem kończę przypominanie "
            f"o tym tygodniu i odezwę się przy kolejnym grafiku."
        )
    return "\n".join(lines)


def build_przypomnienie_text(
    week_label: str, termin: datetime | None, tz: ZoneInfo, *, ma_propozycje: bool
) -> str:
    """Jedno przypomnienie dla pracownika, który po prośbie nie napisał ani słowa (pozycja D5).

    KRÓTKIE z rozmysłem. Pierwsza wiadomość niosła gotowiec i pełne instrukcje, i została
    zignorowana; powtórzenie jej w całości nie dokłada informacji, a wygląda jak nagabywanie.
    Ta ma przypomnieć o sprawie i pokazać najkrótszą drogę do jej zamknięcia.

    ``ma_propozycje`` rozstrzyga, czy ta najkrótsza droga w ogóle istnieje: przy pustym gotowcu
    (brak grafiku z zeszłego tygodnia) nie ma czego potwierdzić, więc zdanie „odpisz »ok«, żeby
    powtórzyć" byłoby nieprawdziwe — ten sam podział, który robi ``build_nudge_text``.

    ``termin`` liczy KOD (**B7**), a ``None`` znaczy „nie da się wyznaczyć" i wtedy zdania o nim
    po prostu nie ma: lepiej nie obiecać nic, niż obiecać datę wziętą z niczego. Zgodność z **N13**
    zostaje — tekst jest stałą tego modułu, zmienne są wyłącznie WARTOŚCI.
    """
    lines = [
        f"Przypominam o grafiku na tydzień {week_label} — nie mam jeszcze Twojej odpowiedzi 🙂"
    ]
    if ma_propozycje:
        lines.append(
            "Wystarczy odpisać „ok”, żeby zapisać propozycję z poprzedniej wiadomości, "
            "albo napisz, co zmienić (np. „w piątek 10–20, reszta bez zmian”)."
        )
    else:
        lines.append("Napisz proszę, kiedy pracujesz (np. „pon–pt 8–16”).")
    if termin is not None:
        lokalnie = termin.astimezone(tz)
        lines.append(
            f"Czekam do {_DNI_DOPELNIACZ[lokalnie.weekday()]} "
            f"{lokalnie:%d.%m}, godz. {lokalnie.hour}:{lokalnie:%M} — "
            "potem kończę przypominanie o tym tygodniu."
        )
    return "\n".join(lines)


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
) -> str:
    """Prośba o potwierdzenie przed zapisem (spirit ADR 0006 — zapis tylko po »tak«).

    ``pominiete`` to dni, o których pracownik napisał, ale których NIE zapisujemy (np. sprzeczne
    godziny). Dopisujemy je do treści, bo bez tego potwierdzenie wyglądałoby na komplet: pracownik
    odpowiadał o pięciu dniach, potwierdza cztery i nie ma jak zauważyć, że jeden wypadł.
    """
    time_off = list(time_off)
    segments = []
    if not schedule.is_empty:
        segments.append(f"grafik: {describe_schedule(schedule, tz)}")
    if time_off:
        segments.append(f"czas wolny: {describe_time_off(time_off)}")
    prosba = f"Zapiszę {'; '.join(segments)}. Potwierdź „tak”, żeby zapisać, albo napisz poprawkę."
    ostrzezenie = describe_pominiete(pominiete)
    return f"{prosba}\n{ostrzezenie}" if ostrzezenie else prosba


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


def build_nic_do_zapisania_text(juz_w_grafiku: Iterable[int]) -> str:
    """Cały potwierdzony komplet był już w grafiku — nie zapisano NIC i trzeba to powiedzieć wprost.

    Kuszące jest użycie tu podziękowania ze ścieżki samouzupełnienia („grafik jest już uzupełniony,
    dziękuję!"), bo stan końcowy wygląda tak samo. Byłoby to jednak mylące w najgorszy możliwy
    sposób: pracownik przed chwilą potwierdził KONKRETNE godziny albo urlop, dostałby podziękowanie
    i zamknięcie tematu, a z jego odpowiedzi nie weszłoby do grafiku nic — i nikt by mu tego nie
    powiedział. Dni wymieniamy z nazwy, bo w tej gałęzi zawsze jakieś są.
    """
    dni = ", ".join(_DNI_SKROT[d] for d in sorted(juz_w_grafiku) if 0 <= d <= 6)
    wykaz = f" ({dni})" if dni else ""
    return (
        f"Te dni miałeś/miałaś już uzupełnione w grafiku{wykaz}, więc niczego nie zmieniałem — "
        "nie chcę dopisywać ich drugi raz. Jeśli zapisane godziny się nie zgadzają, napisz proszę "
        "do przełożonego. Kończę przypominanie o tym tygodniu."
    )


def build_applied_text(*, minione: int = 0, juz_w_grafiku: Iterable[int] = ()) -> str:
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
    """
    juz = sorted(d for d in juz_w_grafiku if isinstance(d, int) and 0 <= d <= 6)
    if not minione and not juz:
        return APPLIED_TEXT
    lines = ["Gotowe ✅ Zapisałem to, czego jeszcze nie było w Twoim grafiku."]
    if juz:
        lines.append(
            f"Te dni były już uzupełnione, więc ich nie zmieniałem: "
            f"{', '.join(_DNI_SKROT[d] for d in juz)}. Jeśli coś się w nich nie zgadza, "
            "napisz proszę do przełożonego."
        )
    if minione:
        lines.append(
            "Dni, które zdążyły już minąć, nie trafiły do grafiku — jeśli mają tam być, "
            "napisz proszę do przełożonego."
        )
    return "\n".join(lines)


def build_unclear_text(powod: str = "") -> str:
    """Prośba o doprecyzowanie — możliwie KONKRETNA, zamiast ogólnego „nie zrozumiałem".

    ``powod`` to enum z ``agent.schema`` (nie tekst od modelu), więc wybór komunikatu nie jest
    kanałem, którym cokolwiek z odpowiedzi pracownika mogłoby do niego wrócić. Nieznana wartość
    degraduje się do komunikatu ogólnego.
    """
    return _TEKSTY_NIEJASNOSCI.get(powod, UNCLEAR_TEXT)


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


def build_summary_text(*, tygodnie: Iterable[LiczbyTygodnia], nastepny_przebieg: str) -> str:
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
    return "\n".join(lines)


def to_html(text: str) -> str:
    """Zamień tekst z podziałami linii na bezpieczny HTML dla wiadomości Teams."""
    return "<br>".join(escape(line) for line in text.split("\n"))
