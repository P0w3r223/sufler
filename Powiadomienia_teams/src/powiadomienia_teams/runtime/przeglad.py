"""Raport diagnostyczny pliku stanu — okno na obieg między jednym piątkiem a drugim.

Do tej pory jedynymi oknami na stan były alert startowy i podsumowanie raz w tygodniu. Przez
tydzień pilotażu to za mało: pytanie „co dokładnie leży teraz w pliku" nie miało odpowiedzi
inaczej niż przez wejście na wolumen i czytanie JSON-a ręcznie.

Podział pracy w tym module jest celowy: ``zbadaj_zrodlo`` to JEDYNE miejsce z I/O, a
``raport_stanu`` jest czystą funkcją nad wczytanym stanem. Dzięki temu kształt raportu daje się
przypiąć testem na dosłownym pliku stanu, bez uruchamiania usługi i bez sieci.

**Dlaczego raport mówi SUROWYMI statusami**, a nie pozycjami raportu tygodniowego
(``runtime.service.STATUS_DO_POZYCJI``): podsumowanie odpowiada administratorowi na pytanie „czy
tydzień się domknął", a to polecenie odpowiada operatorowi na pytanie „co dokładnie jest w pliku".
Przy diagnozie liczy się dokładnie ta nazwa, którą widać w JSON-ie i w logach — łącznie z nazwą
statusu, którego to wydanie NIE ZNA. Podsumowanie zbija takie wpisy w jedno ``nierozpoznane``, bo
czytelnikowi tamtej wiadomości nazwa nic nie mówi; tutaj jest odwrotnie: nazwa jest całą treścią
zgłoszenia.

**Nazwisk nie ma świadomie.** Wyjście tego polecenia trafia do terminala operatora, a przy
``docker compose run`` również do logów demona — czyli do miejsca podlegającego rotacji, a nie
polityce retencji. Identyfikator wystarcza, bo ``scripts/lista_czlonkow.py`` rozwiązuje go
w jednym wywołaniu (ten sam kierunek co pozycja A10 planu).
"""

from __future__ import annotations

import contextlib
import json
import math
import os
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

from powiadomienia_teams.config import OknoOdpowiedzi
from powiadomienia_teams.reminders.lifecycle import termin_odpowiedzi, wiek_kotwicy
from powiadomienia_teams.state import (
    APPLIED,
    APPLYING,
    AWAITING_CONFIRM,
    AWAITING_REPLY,
    DECLINED,
    EXPIRED,
    SELF_FILLED,
    PendingReminder,
    daje_sie_odczytac,
    sciezka_kopii,
)

_UTC = timezone.utc

# Kolejność wypisywania statusów w agregacie: droga wpisu przez obieg, nie alfabet. Operator czyta
# ten blok jak oś czasu — „ilu jeszcze nie odpisało" stoi nad „ilu już zapisano", bo w tej
# kolejności o nich myśli. Statusy spoza tej krotki (dryf schematu) idą na koniec, alfabetycznie.
_KOLEJNOSC_STATUSOW = (
    AWAITING_REPLY,
    AWAITING_CONFIRM,
    APPLYING,
    APPLIED,
    DECLINED,
    EXPIRED,
    SELF_FILLED,
)

# Wpis, który wymaga RĘCZNEJ czynności człowieka — jedyna rzecz w tym raporcie, która nie jest
# tylko liczbą. Zapis do Shifts rozpoczęty i niepotwierdzony; usługa go NIE wznowi, bo drugie
# podejście zdublowałoby wpisy nieodwracalnie (patrz `state.APPLYING`).
_UWAGA_APPLYING = "(!) zapis przerwany w połowie — sprawdź grafik ręcznie"
_UWAGA_NIEZNANY = "(!) status nieznany temu wydaniu"
# Wpis OTWARTY po terminie nie jest błędem raportu ani zaległością: wygaszenie wymaga DOWODU
# z udanego odczytu czatu (N9), więc taki wiersz znaczy „termin minął, czekamy na cichy odczyt".
# Bez tego zdania operator czyta go jako zawieszenie obiegu — a najczęstszą przyczyną jest
# uporczywa awaria odczytu, czyli dokładnie sytuacja, w której NIE wolno nikomu nic napisać.
_UWAGA_PO_TERMINIE = "(!) termin minął — wygaśnie po najbliższym udanym, cichym odczycie"
# BEZ „(!)": przypomnienie nie jest usterką ani zaległością, tylko stanem rozmowy. Operator
# pilotażu przychodzi tu z pytaniem „czy ten człowiek dostał już drugie zagadnięcie" — bez tego
# znacznika odpowiedź wymagałaby czytania pliku stanu ręcznie (pozycja D5 planu).
_UWAGA_PRZYPOMNIANO = "przypomniano"
# Jak wyżej, bez „(!)": wznowienie to stan rozmowy, nie usterka.
_UWAGA_WZNOWIONO = "wznowiony"

_BEZ_KOTWICY = "brak"
_BEZ_TERMINU = "nie do wyznaczenia"

# Statusy, dla których termin jeszcze cokolwiek znaczy — TA SAMA para, po której wygasza
# `runtime.listener`. Wypisana wprost, a nie jako „wszystko poza TERMINALNE", bo status nieznany
# temu wydaniu nie jest wpisem otwartym: usługa go nie przetwarza, więc nie wygaśnie.
_OTWARTE = (AWAITING_REPLY, AWAITING_CONFIRM)


def _ze_znakiem(godziny: int) -> str:
    """``+5`` / ``-4`` — znak jest treścią: offset ujemny to termin PRZED początkiem tygodnia."""
    return f"{godziny:+d}"


@dataclass(frozen=True)
class ZrodloStanu:
    """Skąd pochodzą dane raportu — fakty o plikach, ustalone raz, przed czytaniem stanu.

    Trzy różne rzeczy dają tę samą pustą mapę z ``load_state`` i mają PRZECIWNE znaczenia:

    - pliku nie ma — usługa jeszcze nic nie zapisała albo wolumen jest nie ten;
    - plik jest i jest pusty — obieg pracuje, nie ma spraw w toku;
    - plik jest, ale nie daje się odczytać — idempotencja padła i najbliższy przebieg wyśle
      prośby DRUGI RAZ do wszystkich (``state.load_state``).

    Bez tych pól raport nazywał trzeci przypadek „zwykłym stanem spoczynku" — czyli kłamał
    dokładnie w sytuacji, dla której to polecenie powstało.
    """

    sciezka: Path
    istnieje: bool
    czytelny: bool  # czy `load_state` przyjmie plik główny
    kopia_istnieje: bool
    kopia_czytelna: bool
    zapisany: datetime | None  # mtime pliku głównego; None, gdy pliku nie ma
    # Klucze najwyższego poziomu w SUROWYM JSON-ie; ``None``, gdy pliku nie da się sparsować.
    # Różnica wobec liczby wczytanych wpisów to wpisy pominięte jako nieczytelne (`state._wczytaj`
    # robi to po cichu, ostrzeżeniem na stderr — czyli poza tekstem, który operator wkleja dalej).
    wpisow_w_pliku: int | None


def zbadaj_zrodlo(sciezka: Path) -> ZrodloStanu:
    """Jedyne I/O w tym module: fakty o pliku stanu i jego kopii.

    Błąd ``stat`` traktujemy jak „nie wiem, kiedy zapisano" (``zapisany=None``), a nie jak awarię
    raportu: brak jednej informacji pomocniczej nie może odebrać operatorowi całej reszty
    diagnostyki — po to on ten raport uruchamia.

    O czytelność pytamy ``state.daje_sie_odczytac``, a nie własnym ``json.loads``: kryterium
    tolerancji odczytu wolno mieć wyłącznie jedno.
    """
    kopia = sciezka_kopii(sciezka)
    zapisany: datetime | None = None
    # `suppress`, a nie `try/except/pass`: to raport DIAGNOSTYCZNY, a brak czasu modyfikacji jest
    # tu normalnym wynikiem (plik może nie istnieć). Jedyne miejsce w tym kodzie, gdzie połknięcie
    # wyjątku jest zamierzone — i dlatego ma być widoczne z jednej linii, a nie ukryte w czterech.
    with contextlib.suppress(OSError):
        zapisany = datetime.fromtimestamp(os.stat(sciezka).st_mtime, _UTC)
    istnieje = sciezka.exists()
    return ZrodloStanu(
        sciezka=sciezka,
        istnieje=istnieje,
        czytelny=istnieje and daje_sie_odczytac(sciezka),
        kopia_istnieje=kopia.exists(),
        kopia_czytelna=kopia.exists() and daje_sie_odczytac(kopia),
        zapisany=zapisany,
        wpisow_w_pliku=_wpisow_w_pliku(sciezka),
    )


def _wpisow_w_pliku(sciezka: Path) -> int | None:
    """Ile wpisów NAJWYŻSZEGO POZIOMU leży w pliku, zanim odczyt cokolwiek odsieje."""
    try:
        raw = json.loads(sciezka.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return len(raw) if isinstance(raw, dict) else None


def raport_stanu(
    stan: Mapping[str, PendingReminder],
    teraz: datetime,
    *,
    zrodlo: ZrodloStanu,
    okno: OknoOdpowiedzi,
    z_nazwiskami: bool = False,
) -> str:
    """Złóż raport diagnostyczny. Czysta funkcja: te same dane wejściowe dają ten sam tekst.

    ``okno`` jest w nagłówku i zarazem liczy kolumnę „termin": raport MUSI wyznaczać termin tą samą
    funkcją, co usługa (``lifecycle.termin_odpowiedzi``). Gdyby liczył po swojemu — choćby „kotwica
    plus okno", jak do 0.2.12 — rozjechałby się z zachowaniem usługi dokładnie wtedy, gdy ktoś
    sięga po raport, żeby to zachowanie zrozumieć. Sama polityka (offset i dolna granica) idzie do
    nagłówka, bo inaczej operator musi po nią wejść do ``env`` na serwerze, w trakcie diagnozy.

    ``z_nazwiskami`` idzie z ``LOGUJ_NAZWISKA`` (A10). Ta sama flaga rządzi logami i alertami, więc
    rządzi też tym raportem: dwie różne polityki dla jednej rzeczy skończyłyby się tym, że operator
    wyłącza nazwiska w jednym miejscu i nie wie, że wychodzą drugim. Domyślnie identyfikatory —
    ale gdy pracownik odszedł z zespołu, ``lista_czlonkow.py`` nie rozwiąże już jego identyfikatora,
    a nazwisko z pliku stanu jest wtedy jedynym miejscem, w którym ono jeszcze jest.
    """
    linie = _naglowek(zrodlo, teraz, okno=okno, wczytanych=len(stan))
    if stan:
        linie += [""] + _bloki_tygodni(stan)
        linie += [""] + _lista_wpisow(stan, teraz, okno=okno, z_nazwiskami=z_nazwiskami)
        linie += ["", _stopka_o_nazwiskach(z_nazwiskami)]
    return "\n".join(linie)


def _naglowek(
    zrodlo: ZrodloStanu,
    teraz: datetime,
    *,
    okno: OknoOdpowiedzi,
    wczytanych: int,
) -> list[str]:
    if zrodlo.zapisany is None:
        zapis = "nieznany"
    else:
        zapis = f"{_czas(zrodlo.zapisany, okno.tz)} ({_wzglednie(teraz - zrodlo.zapisany)})"
    linie = [
        "PLIK STANU",
        f"  ścieżka:        {zrodlo.sciezka}",
        f"  plik:           {_stan_pliku(zrodlo.istnieje, zrodlo.czytelny)}",
        f"  kopia (.bak):   {_stan_pliku(zrodlo.kopia_istnieje, zrodlo.kopia_czytelna)}",
        # Data zapisu NIE jest sygnałem życia usługi i nie wolno jej tak czytać: stan zapisuje się
        # przy zmianie, więc tydzień bez rozmów to tydzień bez zapisu przy sprawnej pętli.
        # Sygnałem życia są puls (healthcheck) i podsumowanie tygodniowe.
        f"  ostatni zapis:  {zapis}",
        f"  raport:         {_czas(teraz, okno.tz)}",
        # Obie liczby, bo termin bierze się z ich MAKSIMUM: sam offset nie wyjaśnia wiersza, który
        # ma termin późniejszy, niż wynika z tygodnia (rozmowa, w której bot niedawno o coś prosił).
        f"  termin odpowiedzi: poniedziałek 00:00 {_ze_znakiem(okno.offset_h)} h "
        f"(REPLY_DEADLINE_OFFSET_H)",
        f"  nie wcześniej niż: {okno.min_h} h od ostatniej prośby bota (REPLY_MIN_HOURS)",
    ]
    return linie + _diagnoza(zrodlo, wczytanych=wczytanych)


def _stan_pliku(istnieje: bool, czytelny: bool) -> str:
    if not istnieje:
        return "brak"
    return "jest" if czytelny else "JEST, ALE NIE DA SIĘ GO ODCZYTAĆ"


def _diagnoza(zrodlo: ZrodloStanu, *, wczytanych: int) -> list[str]:
    """Zdanie o tym, CZYM jest to, co operator zaraz zobaczy — albo czego nie zobaczy.

    Kolejność gałęzi idzie od najgroźniejszej. Utrata stanu bez użytecznej kopii jest jedyną
    pozycją w tym raporcie, przy której właściwą reakcją jest zatrzymanie usługi: idempotencja
    wysyłki opiera się wyłącznie na tym pliku, więc najbliższy przebieg zaczepiłby cały zespół
    po raz drugi. Cisza w tym miejscu (a tak było przed przeglądem A4) czyta się jak potwierdzenie,
    że wszystko jest w porządku.
    """
    uzyteczny = zrodlo.czytelny or zrodlo.kopia_czytelna
    if not uzyteczny and not zrodlo.istnieje and not zrodlo.kopia_istnieje:
        return [
            "",
            "Nie ma ani pliku stanu, ani kopii. Przed pierwszym przebiegiem to jest normalne;",
            "po nim znaczy, że usługa pisze gdzie indziej niż wskazuje ta ścieżka.",
        ]
    if not uzyteczny:
        return [
            "",
            "UWAGA: stanu NIE DA SIĘ odczytać ani z pliku, ani z kopii. Idempotencja wysyłki stoi",
            "wyłącznie na tym pliku, więc najbliższy przebieg uzna wszystkich za nienagabywanych",
            "i wyśle prośby DRUGI RAZ do całego zespołu. Zatrzymaj usługę i odtwórz stan z kopii",
            "zapasowej, zanim nadejdzie pora przebiegu.",
        ]
    if not zrodlo.czytelny:
        powod = "pliku głównego nie ma" if not zrodlo.istnieje else "plik główny jest nieczytelny"
        return [
            "",
            f"UWAGA: {powod}, więc wpisy poniżej (o ile są) pochodzą z KOPII — czyli sprzed",
            "ostatniego zapisu. Rozmowa obsłużona po tamtym zapisie może wrócić do obsługi.",
        ]
    pominietych = (zrodlo.wpisow_w_pliku or 0) - wczytanych
    linie = []
    if zrodlo.wpisow_w_pliku is not None and pominietych > 0:
        linie += [
            "",
            f"UWAGA: {pominietych} wpis(ów) w pliku pominięto jako nieczytelne — poniższe liczby",
            "opisują resztę. Powód jest w logu usługi, przy wpisie „Pomijam nieczytelny wpis "
            "stanu”.",
        ]
    if not wczytanych:
        linie += [
            "",
            "Brak wpisów. To nie musi znaczyć awarii: wpisy terminalne są sprzątane po upływie",
            "retencji, więc pusty stan przed piątkowym przebiegiem jest zwykłym stanem spoczynku.",
        ]
    return linie


def _bloki_tygodni(stan: Mapping[str, PendingReminder]) -> list[str]:
    """Agregat po statusach, rozbity po tygodniu docelowym, od najnowszego tygodnia.

    Rozbicie po tygodniu jest tu z tego samego powodu co w podsumowaniu (A9): bez niego świeże
    wpisy tygodnia właśnie otwartego mieszają się z resztkami tygodnia zamykanego i żadna z liczb
    nie odpowiada na pytanie, które operator ma naprawdę.
    """
    per_tydzien: dict[str, Counter[str]] = defaultdict(Counter)
    for pending in stan.values():
        per_tydzien[pending.week_start][pending.status] += 1

    linie = [f"WPISY WEDŁUG TYGODNIA DOCELOWEGO (wpisów: {len(stan)}, tygodni: {len(per_tydzien)})"]
    for week_start in sorted(per_tydzien, reverse=True):
        licznik = per_tydzien[week_start]
        linie += ["", f"  tydzień od {week_start} (wpisów: {sum(licznik.values())})"]
        for status in _statusy_w_kolejnosci(licznik):
            uwaga = ""
            if status == APPLYING:
                uwaga = f"   {_UWAGA_APPLYING}"
            elif status not in _KOLEJNOSC_STATUSOW:
                uwaga = f"   {_UWAGA_NIEZNANY}"
            linie.append(f"    {status:<18}{licznik[status]:>3}{uwaga}")
    return linie


def _statusy_w_kolejnosci(licznik: Counter[str]) -> list[str]:
    znane = [s for s in _KOLEJNOSC_STATUSOW if licznik[s]]
    nieznane = sorted(s for s in licznik if s not in _KOLEJNOSC_STATUSOW)
    return znane + nieznane


def _stopka_o_nazwiskach(z_nazwiskami: bool) -> str:
    if z_nazwiskami:
        return (
            "Nazwiska wypisane, bo POWIADOMIENIA_LOGUJ_NAZWISKA=true — to wyjście ma dane osobowe."
        )
    return "Nazwiska nie są wypisywane — identyfikatory rozwiązuje scripts/lista_czlonkow.py."


def _lista_wpisow(
    stan: Mapping[str, PendingReminder],
    teraz: datetime,
    *,
    okno: OknoOdpowiedzi,
    z_nazwiskami: bool = False,
) -> list[str]:
    """Wpisy po jednym w wierszu, od najbliższych terminu.

    Kolejność jest treścią, nie ozdobą: pierwszy wiersz to wpis, którego termin minął najdawniej
    albo minie najprędzej — czyli ten, o którym operator chce wiedzieć pierwszy. Do 0.2.12
    sortowała kotwica, bo ona wyznaczała wygaśnięcie; teraz wyznacza je termin, więc po nim
    sortujemy. Wpisy bez wyznaczalnego terminu (uszkodzony ``week_start``) idą na koniec.
    """
    wpisy = sorted(stan.values(), key=lambda p: _klucz_terminu(p, okno))
    # Nazwisko RAZEM z identyfikatorem, nie zamiast — tak samo jak w logach (`runtime.etykiety`),
    # żeby wiersz dało się skorelować z alertem po tej samej wartości w obu trybach.
    nazwa = {
        p.member_id: (
            f"{p.member_name} ({p.member_id})" if z_nazwiskami and p.member_name else p.member_id
        )
        for p in wpisy
    }
    szer = max(len(nazwa[p.member_id]) for p in wpisy)
    szer = max(szer, len("osoba"))
    linie = [
        "WPISY (od najbliższych terminu)",
        "",
        # `bez odcz.` obok `błędy`, bo to DWA różne liczniki i mylenie ich kosztuje: `błędy` to
        # nieudane INTERPRETACJE (pracownik coś napisał, my nie zrozumieliśmy), `bez odcz.` to
        # obiegi, w których czatu nie dało się w ogóle przeczytać. Po alercie z ADR 0007 operator
        # przychodzi tutaj sprawdzić, ile wpisów stoi i jak blisko sufitu są — bez tej kolumny
        # miałby alert bez sposobu jego sprawdzenia.
        f"  {'osoba':<{szer}}  {'status':<18} {'tydzień od':<12} {'termin':<18} "
        f"{'kotwica':>9}  {'błędy':>5}  {'bez odcz.':>9}  {'niejasne':>9}",
    ]
    for pending in wpisy:
        uwagi = []
        if pending.status == APPLYING:
            uwagi.append(_UWAGA_APPLYING)
        # Tylko wpisy OTWARTE: przy terminalnym termin już nikogo nie obowiązuje, a „(!)" przy
        # `applied` czytałoby się jak zaległość do obsłużenia.
        if pending.status in _OTWARTE and _po_terminie(pending, teraz, okno):
            uwagi.append(_UWAGA_PO_TERMINIE)
        if pending.status in _OTWARTE and pending.przypomniano_at:
            uwagi.append(_UWAGA_PRZYPOMNIANO)
        if pending.wznowiono_at:
            # BEZ filtru `_OTWARTE`: wznowiony temat bywa już znów domknięty, a operator pyta
            # właśnie o to, czy wznowienie do czegoś doprowadziło.
            uwagi.append(_UWAGA_WZNOWIONO)
        ogon = "".join(f"   {u}" for u in uwagi)
        linie.append(
            f"  {nazwa[pending.member_id]:<{szer}}  {pending.status:<18} {pending.week_start:<12} "
            f"{_termin(pending, okno):<18} "
            f"{_wiek(pending, teraz):>9}  {pending.fail_count:>5}  "
            f"{pending.unknown_count:>9}  {_niejasne(pending):>9}{ogon}"
        )
    return linie


def _niejasne(pending: PendingReminder) -> str:
    """``2/9`` — niejasności na tle WSZYSTKICH interpretacji tej osoby (miara E4, §10.4).

    Ułamek, nie sama liczba: „trzy niejasności" u kogoś, kto napisał dziesięć razy, znaczy co
    innego niż u kogoś, kto napisał raz. Bez ani jednej interpretacji (pracownik milczy, albo
    odpowiedział samym „tak" szybką ścieżką) piszemy kreskę — zero z zera nie jest odsetkiem
    i „0/0" czytałoby się jak zmierzone zero.
    """
    if not pending.interpretacje:
        return "—"
    return f"{pending.niejasnosci}/{pending.interpretacje}"


def _po_terminie(pending: PendingReminder, teraz: datetime, okno: OknoOdpowiedzi) -> bool:
    termin = termin_odpowiedzi(pending, okno)
    return termin is not None and teraz >= termin


def _termin(pending: PendingReminder, okno: OknoOdpowiedzi) -> str:
    """Termin odpowiedzi w strefie ZESPOŁU, bez nazwy strefy — ta stoi raz, w nagłówku.

    Strefa bierze się z tej samej polityki, w której termin jest liczony. Osobny parametr strefy
    raportu (tak było przez chwilę) dawał dwie wartości, które muszą się zgadzać, i nic tego nie
    wymuszało — a rozjazd byłby widoczny wyłącznie w instalacji, która je rozjedzie.
    """
    termin = termin_odpowiedzi(pending, okno)
    if termin is None:
        return _BEZ_TERMINU
    return f"{termin.astimezone(okno.tz):%Y-%m-%d %H:%M}"


def _klucz_terminu(pending: PendingReminder, okno: OknoOdpowiedzi) -> tuple[bool, float, str]:
    termin = termin_odpowiedzi(pending, okno)
    # `member_id` w kluczu, żeby kolejność była powtarzalna także przy identycznym terminie — raport
    # bywa porównywany z poprzednim przebiegiem i różnica ma znaczyć zmianę stanu, nie tasowanie.
    #
    # `is not None`, a nie sama prawdziwość: rosnąco po terminie znaczy „najdawniej przekroczony
    # najwyżej", a wpis bez terminu nie ma się z czym porównać i idzie na koniec.
    return (
        termin is None,
        termin.timestamp() if termin is not None else 0.0,
        pending.member_id,
    )


def _wiek(pending: PendingReminder, teraz: datetime) -> str:
    """Wiek kotwicy w GODZINACH — „jak dawno cokolwiek się w tym temacie działo".

    Od 0.2.13 ta kolumna NIE rozstrzyga już o wygaśnięciu (rozstrzyga kolumna „termin"). Zostaje,
    bo odpowiada na inne pytanie diagnostyczne: rozmowa z terminem odległym, a kotwicą sprzed
    trzech dni to rozmowa, w której nikt nic nie napisał — i to jest sygnał o czymś innym niż
    zbliżający się termin. Retencja wpisów terminalnych liczy się dokładnie od tej wielkości.

    Godziny są OBCINANE w dół, nie zaokrąglane: zaokrąglenie pokazywało „48 h" dla wpisu mającego
    47 h 40 min, czyli podawało wielkość większą od faktycznej w kolumnie czytanej przy diagnozie.
    """
    wiek = wiek_kotwicy(pending, teraz)
    if wiek is None:
        return _BEZ_KOTWICY
    return f"{math.floor(wiek.total_seconds() / 3600)} h"


def _wzglednie(delta: timedelta) -> str:
    """CAŁA fraza w nawiasie, nie sama liczba — bo jeden z przypadków nie jest odstępem czasu.

    Znacznik pliku w przyszłości znaczy rozjazd zegara hosta (albo wolumen z innej maszyny)
    i wymaga zdania, nie liczby. Gdy tę gałąź składał wołający, dopisując „temu", powstawało
    „czas w przyszłości temu" — komunikat, który operator musiałby odszyfrować w miejscu, gdzie
    właśnie szuka pewnego gruntu.
    """
    minuty = delta.total_seconds() / 60
    if minuty < 0:
        return "znacznik w przyszłości — sprawdź zegar hosta"
    if minuty < 120:
        return f"{minuty:.0f} min temu"
    return f"{minuty / 60:.0f} h temu"


def _czas(chwila: datetime, strefa: tzinfo) -> str:
    lokalny = chwila.astimezone(strefa)
    return f"{lokalny:%Y-%m-%d %H:%M} {lokalny.tzname()}"
