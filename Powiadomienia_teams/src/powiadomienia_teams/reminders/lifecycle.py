"""Cykl życia przypomnienia: termin odpowiedzi i sprzątanie wpisów terminalnych.

Czysta logika (wstrzykiwany ``now``), operuje na ``PendingReminder`` w pamięci — bez I/O, w pełni
testowalna.

Termin odpowiedzi jest **kalendarzowy**: liczy się od początku tygodnia, którego dotyczy prośba,
a nie od ostatniej aktywności w rozmowie. Do 0.2.12 było odwrotnie (okno ``N`` godzin ciszy od
kotwicy) i dawało dwa defekty naraz — patrz ``termin_odpowiedzi``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime, timedelta, tzinfo
from enum import Enum
from typing import Protocol, TypeVar

from powiadomienia_teams.config import OknoOdpowiedzi
from powiadomienia_teams.domain.czas import parse_graph_datetime
from powiadomienia_teams.state import APPLYING, PendingReminder
from powiadomienia_teams.state import TERMINALNE as _TERMINAL

# ``_TERMINAL`` mieszka w ``state`` obok samych statusów, bo ma DWÓCH odbiorców: sprzątanie stanu
# tutaj (``prune_terminal``) i wygaszanie pamięci rozmowy przy zapisie (``state._do_zapisu``).
# Sprzątanie wpisów ``APPLYING`` jest sprzątaniem śladu po awarii — patrz ``prune_terminal``
# i osobna pozycja w podsumowaniu dla administratora.


class ReadOutcome(Enum):
    """Co ustalił odczyt czatu tej osoby w BIEŻĄCYM przebiegu.

    To przesłanka orzeczenia o wygaśnięciu, nie szczegół techniczny: komunikat domknięcia mówi
    „Nie dostałem odpowiedzi", czyli twierdzi coś o ZACHOWANIU PRACOWNIKA. Wolno je wypowiedzieć
    wyłącznie wtedy, gdy naprawdę zajrzeliśmy do czatu i naprawdę nic tam nie było.
    """

    HANDLED = "handled"  # była nowa wiadomość i została obsłużona
    NOTHING_NEW = "nothing_new"  # odczyt się powiódł, nowej wiadomości nie ma
    # Nic pewnego nie ustaliliśmy: obsługa odpowiedzi wywróciła się w połowie. Dla wygaszania
    # znaczy to samo co awaria odczytu — nie ma podstaw, by twierdzić „nie odpisał" — ale czat
    # ODPOWIADAŁ (``READ_FAILED`` jest osobno), więc licznik z ADR 0007 tej wartości nie liczy.
    UNKNOWN = "unknown"
    # Sam ODCZYT czatu padł: o tej osobie nie wiemy nic i nie mamy jak się dowiedzieć. Jedyna
    # wartość, którą liczy `unknown_count` i jedyna, która zbliża wpis do twardego sufitu
    # (ADR 0007) — bo tylko ona świadczy o tym, że KANAŁ jest niedostępny.
    #
    # Rozdział wobec `UNKNOWN` nie jest kosmetyczny. Zlane w jedno, do sufitu zbliżał wpis także
    # ucięty odczyt GRAFIKU (`GraphTruncatedReadError` omija `_record_failure` świadomie) i każdy
    # błąd między odczytem czatu a blokiem `try` w `_process_pending` — czyli awarie, które
    # o dostępności czatu nie mówią nic, a alert o nich twierdził, że czat milczy.
    READ_FAILED = "read_failed"
    # Odczyt się UDAŁ, ale wątek przestał być rozmową 1:1 — jest w nim ktoś obcy, więc o TEJ osobie
    # nie orzekamy. Dla wygaszania znaczy dokładnie to samo co `UNKNOWN` (patrz ``should_expire``),
    # ale jest osobną wartością, bo licznik i sufit z ADR 0007 celowo tej ścieżki NIE obejmują:
    # tam operator jest już zawołany (``listener._zglos_obcych_raz``), więc awaria nie jest cicha,
    # a zamknięcie wpisu znaczyłoby zamknięcie komuś tygodnia dlatego, że kolega napisał w wątku.
    #
    # Nowe wartości enuma mimo N34: N34 chroni wartości ZAPISYWANE NA DYSK, żeby cofnięcie obrazu
    # było samą podmianą wersji. `ReadOutcome` żyje wyłącznie w pamięci jednego obiegu i nigdy nie
    # jest serializowany, więc powód stojący za N34 tu nie sięga.
    BLOCKED = "blocked"


def _najpozniejszy(*znaczniki: str) -> datetime | None:
    """Najpóźniejszy z podanych znaczników ISO; nieparsowalne i puste pomijamy, brak → ``None``.

    Pomijanie, a nie wyjątek: plik stanu przeżywa wydania i jeden uszkodzony znacznik nie może
    unieruchomić rozmowy. Kierunek jest bezpieczny w obu miejscach użycia — mniej znaczników znaczy
    wcześniejszy termin przy kurtuazji i wcześniejsze sprzątnięcie przy retencji, nigdy odwrotnie.
    """
    kandydaci = []
    for iso in znaczniki:
        if not iso:
            continue
        try:
            kandydaci.append(parse_graph_datetime(iso))
        # `TypeError`/`AttributeError` obok `ValueError`: „nieparsowalne" ma znaczyć także ZŁY TYP,
        # nie tylko zły napis. `parse_graph_datetime(123)` nie rzuca `ValueError`, tylko
        # `AttributeError` na `.replace` — a `if not iso` przepuszcza każdą niezerową liczbę.
        # Filtr typów w `state._wczytaj` zamyka drogę z pliku; ta gałąź jest siatką na wołającego,
        # który zbuduje `PendingReminder` w kodzie (test, przyszły moduł) z pominięciem odczytu.
        except (ValueError, TypeError, AttributeError):
            continue
    return max(kandydaci) if kandydaci else None


def _anchor(pending: PendingReminder) -> datetime | None:
    """Czas OSTATNIEJ AKTYWNOŚCI w temacie (obie strony rozmowy), z fallbackiem na nudge.

    Od 0.2.13 kotwica **nie wyznacza już terminu odpowiedzi** (patrz ``termin_odpowiedzi``) —
    została
    dwóm zastosowaniom, w których naprawdę chodzi o „jak dawno cokolwiek się tu działo":
    ``ready_for_self_fill_check`` (czy bot już dość długo czeka, by zajrzeć do Shifts) i
    ``prune_terminal`` (wiek wpisu terminalnego). Trzecim czytelnikiem jest diagnostyka
    (``wiek_kotwicy`` → ``--stan``).

    Liczą się obie strony: ``watermark`` (ostatnia wiadomość pracownika) i ``bot_last_message_at``
    (ostatnia wiadomość bota w otwartym temacie); dopóki nikt nie napisał, kotwicą jest nudge.

    Brak wszystkich znaczników → ``None`` (nie znamy wieku wpisu).
    """
    return _najpozniejszy(pending.watermark, pending.bot_last_message_at, pending.nudged_at)


def _poczatek_tygodnia(week_start: str, tz: tzinfo) -> datetime | None:
    """Lokalna północ poniedziałku, którego dotyczy prośba. Nieczytelna data → ``None``.

    Ta sama technika co w ``scheduler.weekly``: ``tzinfo`` wstrzykiwane do konstruktora, więc
    „północ" znaczy północ zegara ściennego także w tygodniu ze zmianą czasu.
    """
    try:
        dzien = date.fromisoformat(week_start)
    # `TypeError` obok `ValueError` — patrz `_najpozniejszy`. Tutaj stawka była najwyższa w całym
    # module: `should_expire` woła tę funkcję z list-comprehension kroku 2 `poll_replies`, czyli
    # POZA izolacją per-osoba, więc `week_start` będący liczbą kładł cały obieg nasłuchu
    # deterministycznie, w każdym ticku, przy bijącym pulsie.
    except (ValueError, TypeError):
        return None
    return datetime(dzien.year, dzien.month, dzien.day, tzinfo=tz)


def termin_kalendarzowy(week_start: str, okno: OknoOdpowiedzi) -> datetime | None:
    """Termin z KALENDARZA (bez dolnej granicy kurtuazji): początek tygodnia + ``offset_h``.

    Publiczna, bo tę samą wartość musi znać TREŚĆ prośby (``messages.build_nudge_text``, pozycja B7
    planu). Prośba wysyłana jest przed powstaniem wpisu, więc nie ma jeszcze znaczników bota i nie
    da się policzyć pełnego terminu — ale kurtuazja termin wyłącznie ODDALA, więc data podana
    pracownikowi jest obietnicą, której runtime nie złamie w drugą stronę. Gdyby treść liczyła to po
    swojemu, rozjazd byłby niewidoczny do chwili, w której ktoś traci tydzień grafiku.
    """
    poczatek = _poczatek_tygodnia(week_start, okno.tz)
    return None if poczatek is None else poczatek + timedelta(hours=okno.offset_h)


def termin_dla_nowej_prosby(
    week_start: str, teraz: datetime, okno: OknoOdpowiedzi
) -> datetime | None:
    """Termin, który wolno OBIECAĆ w prośbie wysyłanej TERAZ. Nigdy późniejszy od faktycznego.

    ``max(kalendarz, teraz + min_h)`` — ta sama reguła co w ``termin_odpowiedzi``, tylko z ``teraz``
    w miejscu znaczników bota, których w chwili wysyłki jeszcze nie ma. Faktyczny termin policzy się
    od ``sent_at``, a ``sent_at >= teraz``, więc obietnica jest DOLNĄ granicą: runtime nie zamknie
    tematu wcześniej, niż powiedział.

    Sam termin kalendarzowy tu nie wystarcza. Offset wolno ustawić ujemny (do −168 h, tydzień przed
    początkiem tygodnia docelowego), a wtedy prośba podawałaby pracownikowi datę Z PRZESZŁOŚCI —
    zdanie bez sensu, choć konfiguracja legalna i zgłoszona ostrzeżeniem startowym.
    """
    kalendarz = termin_kalendarzowy(week_start, okno)
    if kalendarz is None:
        return None
    return max(kalendarz, teraz + timedelta(hours=okno.min_h))


def termin_odpowiedzi(pending: PendingReminder, okno: OknoOdpowiedzi) -> datetime | None:
    """DO KIEDY wolno czekać na odpowiedź: termin kalendarzowy, nie budżet ciszy.

    ``termin = max(początek_tygodnia + offset_h, ostatnia_prośba_bota + min_h)``

    Pierwszy składnik to sedno zmiany. Okno liczone od ostatniej aktywności dawało dwa defekty
    naraz:

    1. Przy przebiegu w piątek i oknie 48 h termin zamykał się w NIEDZIELĘ, przed początkiem
       tygodnia, którego dotyczył — a człowiek, który siadł do grafiku w poniedziałek rano, był po
       terminie, choć zachował się normalnie.
    2. Kotwicą było ``max(watermark, bot_last_message_at, nudged_at)``, a bot odzywa się w otwartym
       temacie przy każdym doprecyzowaniu i przy prośbie o potwierdzenie. Okno przedłużało się
       własnym ogonem — o CAŁE 48 h za każdym razem — aż rozmowa dożywała kolejnego piątku
       i zostawała nadpisana razem z uzgodnionym już grafikiem (``runtime.nudge`` alertuje o tym
       zderzeniu, ale uzgodnienie jest już wtedy stracone).

    Drugi składnik to dolna granica kurtuazji i JEDYNE, co zostało z kotwicy N10: nie zamykamy
    tematu
    zaraz po tym, jak bot o coś poprosił. Bez niego pending obsłużony po przestoju dłuższym niż
    tydzień (utrata sesji czeka na ręczne ``--login``) dostawał prośbę o potwierdzenie i wygasał
    w kolejnym cyklu — po dziesięciu sekundach, bo obsłużona odpowiedź resetuje backoff — zdaniem
    „nie doczekałem się potwierdzenia". Wpis stawał się terminalny, więc »tak« pracownika nie było
    już nigdy czytane.

    Liczy się WYŁĄCZNIE ostatnia prośba BOTA (``nudged_at``/``bot_last_message_at``), nie wiadomość
    pracownika: kurtuazja jest odpowiedzią na to, o co poprosiliśmy, a nie nagrodą za aktywność.
    Ogon zostaje więc przycięty do ``min_h`` na jedno pytanie bota — zamiast pełnego okna — i rośnie
    tylko wtedy, gdy pracownik naprawdę pisze. Sufitu świadomie NIE ma: ograniczenie terminu z góry
    przywracałoby defekt N10 przy długim przestoju, a to awaria cicha i po stronie pracownika,
    podczas gdy zderzenie z kolejnym piątkiem jest alertowane.

    ``None`` znaczy „nie da się wyznaczyć terminu, więc wpis nie wygasa" — tak samo bezpiecznie jak
    brak kotwicy dotąd. Osiągalne tylko przy nieczytelnym ``week_start``, czyli przy uszkodzonym
    wpisie stanu; najbliższy przebieg tygodniowy nadpisze go z alertem.

    **Termin jest zarazem godziną, o której wychodzi domknięcie** („nie dostałem odpowiedzi"):
    wygaszenie następuje w pierwszym cichym obiegu po terminie. Przy domyślnym offsecie termin
    wypada w poniedziałek o 05:00, czyli WEWNĄTRZ godzin ciszy — a te odkładają cały obieg, więc
    orzeczenie i wiadomość wychodzą dopiero o 07:00 (**B5**, od 0.2.13). Daje to dwie godziny
    łaski, których nikt nie projektował, i jest opisane w ``deploy/README-serwer.md``. Instalacja,
    dla
    której nawet to jest wtargnięciem, wyłącza samo domknięcie (``SEND_EXPIRY_MESSAGE=false``);
    przesunięcie godziny robi się przez ``CISZA_DO_H`` albo ``REPLY_DEADLINE_OFFSET_H``.

    **Zegar ścienny dotyczy północy, nie sumy z offsetem.** Przy dużym offsecie ujemnym suma może
    wypaść na godzinie lokalnie nieistniejącej (Warszawa, przejście na czas letni): ``astimezone``
    rozstrzyga to wtedy przez ``fold=0``, czyli termin przesuwa się o godzinę względem intencji.
    Osiągalne dopiero przy ``offset_h ≤ -21``, więc świadomie tego nie komplikujemy.
    """
    termin = termin_kalendarzowy(pending.week_start, okno)
    if termin is None:
        return None
    prosba = _najpozniejszy(pending.nudged_at, pending.bot_last_message_at)
    if prosba is not None:
        termin = max(termin, prosba + timedelta(hours=okno.min_h))
    return termin


def wiek_kotwicy(pending: PendingReminder, now: datetime) -> timedelta | None:
    """Ile czasu minęło od kotwicy okna; ``None``, gdy wpis kotwicy nie ma (patrz ``_anchor``).

    Publiczna, bo diagnostyka (``--stan``) pokazuje tę wielkość obok terminu odpowiedzi. Od 0.2.13
    kotwica NIE rozstrzyga już o wygaśnięciu (rozstrzyga ``termin_odpowiedzi``), więc raport podaje
    obie liczby: kotwica odpowiada na „jak dawno cokolwiek się tu działo", termin na „do kiedy
    czekamy". Zwinięcie ich w jedną kolumnę kazałoby operatorowi zgadywać, którą z dwóch polityk
    właśnie widzi — a po tę kolumnę sięga się wtedy, gdy trzeba zrozumieć zachowanie usługi.
    """
    anchor = _anchor(pending)
    return None if anchor is None else now - anchor


def is_expired(pending: PendingReminder, now: datetime, okno: OknoOdpowiedzi) -> bool:
    """Czy minął TERMIN odpowiedzi. Bez wyznaczalnego terminu → False.

    Czysty predykat czasu — sam w sobie NIE wystarcza do wygaszenia; patrz ``should_expire``.
    """
    termin = termin_odpowiedzi(pending, okno)
    return termin is not None and now >= termin


def should_expire(
    pending: PendingReminder, now: datetime, okno: OknoOdpowiedzi, *, read: ReadOutcome
) -> bool:
    """Czy wolno ORZEC wygaśnięcie: minął termin ORAZ mamy na to dowód z udanego odczytu.

    Sam termin nie wystarcza, bo jest liczbą z kalendarza i z konfiguracji, a orzekamy o czymś
    innym: że pracownik miał szansę odpowiedzieć i tego nie zrobił. Te dwie rzeczy rozjeżdżają
    się, gdy usługa NIE SŁUCHAŁA — po przestoju sięgającym za termin (utrata sesji czeka na ręczne
    ``--login``) termin jest przekroczony, choć nikt nie milczał.

    Bez tego warunku pierwszy przebieg po przestoju wysyłał w JEDNYM cyklu prośbę o potwierdzenie
    i zaraz po niej „Nie dostałem odpowiedzi", a pending lądował w terminalnym ``EXPIRED`` — więc
    „tak" pracownika nie było już nigdy czytane. ``UNKNOWN`` blokuje wygaszenie z tego samego
    powodu: awaria odczytu czatu to brak dowodu, a nie dowód braku.
    """
    if read is not ReadOutcome.NOTHING_NEW:
        return False
    return is_expired(pending, now, okno)


def przekroczyl_sufit(pending: PendingReminder, now: datetime, sufit_h: int) -> bool:
    """Czy wpis jest STARSZY niż twardy sufit wieku (ADR 0007) — kandydat do CICHEGO zamknięcia.

    Osobny predykat obok ``should_expire``, w tym samym pliku, i to jest celowe: ADR 0003 umieścił
    politykę wygaszania tutaj, „żeby niezmiennik dało się przeczytać w jednym miejscu". Sufit ten
    niezmiennik OSŁABIA — zamyka wpis BEZ dowodu z udanego odczytu — więc musi być czytelny obok
    reguły, którą nadwyręża, a nie schowany w orkiestratorze.

    **Ta funkcja nie uprawnia do ŻADNEJ wiadomości do pracownika.** Mówi wyłącznie „ten wpis stoi
    tak długo, że przestaje być użyteczny". O zachowaniu człowieka nadal nic nie wiemy, więc
    zamknięcie z sufitu jest ciche (``domkniecia.zamknij_cicho_nierozstrzygniete``); przepuszczenie
    go przez ``zamknij_bez_zapisu`` wysłałoby „Nie dostałem odpowiedzi", czyli zdanie, którego nie
    mamy prawa wypowiedzieć.

    Bez kotwicy → ``False``: nie znamy wieku wpisu, a kierunek bezpieczny to zostawić go otwartym
    (tak samo jak ``prune_terminal``). ``>=``, bo tak porównuje ``is_expired``.
    """
    wiek = wiek_kotwicy(pending, now)
    return wiek is not None and wiek >= timedelta(hours=sufit_h)


def ready_for_self_fill_check(pending: PendingReminder, now: datetime, min_idle_s: int) -> bool:
    """Czy wolno zajrzeć do Shifts, bo pracownik MILCZY od dłuższej chwili (nie odpisuje na czacie).

    Ciszę mierzymy tą samą kotwicą co wygaśnięcie (``_anchor``: ostatnia aktywność, potem czas
    nudge'a) — sprawdzamy grafik dopiero, gdy bot NAPRAWDĘ już czeka, a nie zaraz po nudge'u.
    ``min_idle_s < 0`` wyłącza funkcję; ``0`` sprawdza przy każdym cichym cyklu. Bez kotwicy →
    False.
    """
    if min_idle_s < 0:
        return False
    anchor = _anchor(pending)
    return anchor is not None and now >= anchor + timedelta(seconds=min_idle_s)


class _MaZakonczenie(Protocol):
    """Cokolwiek, co ma koniec w czasie — ``Shift`` i ``TimeOff`` spełniają to strukturalnie."""

    @property
    def end(self) -> datetime: ...


_T = TypeVar("_T", bound=_MaZakonczenie)


def still_writable(items: Iterable[_T], now: datetime) -> tuple[_T, ...]:
    """Zostaw wpisy, które jeszcze się nie skończyły — reszty nie ma po co zapisywać.

    Okno odpowiedzi jest obietnicą wobec pracownika, ale użyteczność zapisu ma własny termin.
    Kryterium to KONIEC wpisu, nie początek: zmiana trwająca w tej chwili jest nadal prawdziwa
    i warto mieć ją w grafiku, natomiast zmiana zakończona wczoraj zaśmieca grafik dniem, który
    minął — a to menedżer czyta jako stan faktyczny.

    Filtr stoi TU, a nie w regule zamykającej całą rozmowę, bo tamta jest zbyt tępa: zamykała
    temat po czasie ODCZYTU, więc „tak" wysłane o 23:58 przepadało, gdy najbliższy przebieg
    wypadał po północy — mimo że nie minął jeszcze ani jeden dzień. Ratujemy część tygodnia,
    która wciąż jest przed nami, zamiast odrzucać wszystko albo zapisywać przeszłość.
    """
    return tuple(item for item in items if item.end > now)


def prune_terminal(
    state: dict[str, PendingReminder],
    now: datetime,
    retain_hours: int,
    *,
    biezacy_tydzien: str = "",
) -> dict[str, PendingReminder]:
    """Usuń wpisy TERMINALNE starsze niż ``retain_hours``, ale NIGDY strażnika bieżącego tygodnia.

    Wpis terminalny pełni podwójną rolę: jest śladem po zakończonym temacie i STRAŻNIKIEM
    idempotencji ``run_once``, która porównuje ``week_start`` — czyli działa per TYDZIEŃ. Sama
    retencja godzinowa te dwie role rozjeżdżała: wpis ``DECLINED`` z piątku 16:05 znikał w niedzielę
    po 16:05, choć tydzień docelowy był wciąż ten sam. Operatorskie ``--once`` w niedzielę wieczorem
    (polecenie z README) albo ``CATCHUP_GRACE_HOURS`` większe od retencji zaczepiało wtedy
    ponownie osobę, której bot obiecał „kończę przypominanie".

    ``retain_hours`` idzie z ``TERMINAL_RETAIN_HOURS`` i jest to liczba WŁASNA tej funkcji. Do
    0.2.12 przychodził tu ``reply_window_hours``, czyli ta sama wartość, która wyznaczała okno
    odpowiedzi — więc każda zmiana terminu przestawiała po cichu strażnika N15, którego nie
    zamierzała dotykać, a skutkiem było ponowne zaczepienie osoby, której bot obiecał „kończę
    przypominanie".

    ``biezacy_tydzien`` to ISO poniedziałku tygodnia docelowego. Wpisy dotyczące jego albo
    późniejszego tygodnia zostają niezależnie od wieku; pusty napis wyłącza tę ochronę (zgodność
    wsteczna dla wywołań, które tygodnia nie znają). Wpis terminalny bez kotwicy zostawiamy (nie
    znamy jego wieku). Zwraca NOWY słownik (niemutujący wejścia).
    """
    kept: dict[str, PendingReminder] = {}
    for key, pending in state.items():
        if pending.status == APPLYING:
            # DOWÓD, nie ślad. `APPLYING` znaczy „potwierdzone, ale zapis nie domknął się" —
            # jedyny status wymagający ręcznego sprawdzenia grafiku klienta. Retencja godzinowa
            # kasowała go, zanim ktokolwiek zdążył zareagować: alert startowy mógł nie dolecieć,
            # a cotygodniowe podsumowanie liczy statusy PO tym sprzątaniu, więc pokazywało zero.
            # Wpis zostaje do ręcznego uprzątnięcia; jeden na incydent, więc stan nie puchnie.
            kept[key] = pending
            continue
        if pending.status in _TERMINAL:
            if biezacy_tydzien and pending.week_start >= biezacy_tydzien:
                kept[key] = pending  # strażnik wciąż chroni bieżący tydzień
                continue
            anchor = _anchor(pending)
            if anchor is not None and now >= anchor + timedelta(hours=retain_hours):
                continue  # dość stary wpis terminalny — wyrzuć
        kept[key] = pending
    return kept
