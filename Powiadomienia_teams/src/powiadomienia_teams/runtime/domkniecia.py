"""Domykanie tematów: co bot mówi, gdy przestaje pytać — i w jakiej kolejności to utrwala.

Temat kończy się na cztery różne sposoby i KAŻDY musi powiedzieć prawdę. Kto nie odpisał, słyszy
„nie dostałem odpowiedzi"; kto odpisał, ale nie potwierdził — „nie doczekałem się potwierdzenia";
kto uzupełnił grafik sam — podziękowanie. Wspólny komunikat byłby dla pracownika bezużyteczny,
a dla części z nich po prostu nieprawdziwy.

Sposób PIĄTY (``zamknij_cicho_nierozstrzygniete``, ADR 0007) nie mówi pracownikowi NIC — i to jest
ta sama zasada, nie wyjątek od niej. Wpis schodzi z obiegu, bo jego czatu nie dało się odczytać
w serii obiegów, a od ostatniej aktywności minął sufit; o zachowaniu człowieka nie ustaliliśmy
wtedy niczego, więc każde zdanie na jego temat byłoby zgadywaniem. Milczenie jest jedyną prawdziwą
treścią, jaką mamy. Dowiaduje się
operator, nie pracownik.

Wzorzec utrwalania jest jeden dla wszystkich ścieżek: status terminalny NAJPIERW, wysyłka POTEM.
To jest cena semantyki „co najwyżej raz" — proces ubity między jednym a drugim zostawia temat
zamknięty bez wiadomości, a nie wiadomość bez zamknięcia (czyli nie zapętla się na kolejnym
przebiegu). Nieudana wysyłka jest wyłącznie logowana: status jest już terminalny, więc ponowienia
i tak nie będzie. Wyjątkiem jest utrata sesji — dotyczy całej usługi, nie tej jednej wiadomości,
i propaguje dalej.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo

from powiadomienia_teams import state as st
from powiadomienia_teams.config import OknoCiszy, Settings
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.messages import (
    build_self_filled_text,
    build_write_failed_text,
    etykieta_tygodnia_iso,
    to_html,
)
from powiadomienia_teams.runtime import etykiety
from powiadomienia_teams.runtime.cisza import najblizsza_dozwolona, wolno_pisac
from powiadomienia_teams.runtime.wysylka import NIE_POLYKAJ, do_pracownika

logger = logging.getLogger(__name__)


def do_domkniecia(
    wpisy: list[st.PendingReminder],
    teraz: datetime,
    okno_domkniec: OknoCiszy,
    czego: str,
) -> list[st.PendingReminder]:
    """Które tematy wolno TERAZ domknąć — w godzinach ciszy żaden.

    **To jest szew, nie warunek do zapamiętania.** Domknięcie idzie do człowieka, który w tej
    rozmowie NIC nie napisał, więc obowiązuje go cisza nawet wtedy, gdy wołający ją świadomie
    pominął: `--poll-once` wyłącza ciszę po to, żeby ODPOWIEDZIEĆ tym, którzy właśnie napisali.
    Przy domyślnym terminie (poniedziałek 05:00, wewnątrz ciszy) i sekwencji wdrożenia stawiającej
    to polecenie wśród kroków weryfikacyjnych operator zawiadamiał o wygaśnięciu milczących
    pracowników o piątej rano.

    Reguła siedzi TUTAJ, a nie u wołającego, bo stała u niego jako dwa ręcznie dopisane warunki —
    a pozycja **D5** planu (wznowienie rozmowy) dokłada kolejną ścieżkę piszącą do milczących.
    Nowa funkcja domykająca dziedziczy więc bramkę zamiast jej potrzebować.

    Sprostowanie: zdanie o „strażniku statycznym w `test_cisza.py`", który miałby pilnować, że
    każda funkcja domykająca bierze `okno_domkniec` w sygnaturze, było NIEPRAWDZIWE — takiego
    strażnika nigdy nie napisano (jedyny `ast.parse` w `tests/` mieszka w `test_szew_wysylki.py`).
    Zdjęte, bo obietnica strażnika działa gorzej niż jego brak: usypia czujność przy dopisywaniu
    kolejnej ścieżki.

    Jawny wyjątek od tej reguły: ``zamknij_cicho_nierozstrzygniete`` (ADR 0007) `okno_domkniec`
    NIE bierze i brać nie powinno. Cisza odkłada WIADOMOŚCI do pracownika, a tam żadna wiadomość
    nie powstaje — bramka na wysyłkę, której nie ma, tylko odsuwałaby w czasie zamknięcie wpisu.

    Odłożenie NICZEGO nie kosztuje: termin i tak minął, wpis czeka nietknięty, a pętla usługi
    orzeka wygaśnięcie po ciszy. Log dopiero po policzeniu wpisów — zdanie „odkładam wygaszenia"
    przy pustym stanie mówiłoby o pracy, której nie było.
    """
    if wolno_pisac(teraz, okno_domkniec) or not wpisy:
        return wpisy
    logger.info(
        "Godziny ciszy — %d %s odkładam do %s (odpowiadam tylko tym, którzy napisali)",
        len(wpisy),
        czego,
        najblizsza_dozwolona(teraz, okno_domkniec).isoformat(),
    )
    return []


def zamknij_bez_zapisu(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    closed: list[st.PendingReminder],
    tresc: Callable[[str], str],
    teraz: datetime,
    powod: str,
    *,
    okno_domkniec: OknoCiszy,
) -> None:
    """Zamknij tematy terminalnie: status EXPIRED utrwalony PRZED wysyłką (»co najwyżej raz«).

    ``tresc`` buduje wiadomość z etykiety tygodnia TEGO wpisu (np. ``messages.build_expired_text``)
    — domknięcie ma mówić, którego tygodnia dotyczy, a porcja może obejmować różne tygodnie.
    """
    closed = do_domkniecia(closed, teraz, okno_domkniec, "wygaszeń")
    if not closed:
        return
    for pending in closed:
        pending.status = st.EXPIRED
        # Temat domknięty — bramka szybkiej ścieżki zapisu przestaje obowiązywać (N38).
        # Dziś wpis terminalny i tak nie wchodzi do `open_items`, ale pozycja **D5** planu
        # (wznowienie rozmowy) tę własność zdejmie — i wtedy zostawiona otwarta flaga
        # znaczyłaby zapis po samym »tak«, którego nikt o nic nie pytał.
        pending.awaiting_yes = False
    st.save_state(settings.state_path, state)
    if settings.send_expiry_message:
        _powiadom_o_zamknieciu(settings, client, closed, tresc, teraz, powod)


def zamknij_cicho_nierozstrzygniete(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    closed: list[st.PendingReminder],
) -> None:
    """Zamknij wpisy, których NIE DA SIĘ rozstrzygnąć — bez jednego słowa do pracownika (ADR 0007).

    Piąta ścieżka domykająca i jedyna niema. Powód zamknięcia leży po NASZEJ stronie: odczyt czatu
    tej osoby padł w serii kolejnych obiegów ORAZ od ostatniej aktywności minęło więcej niż
    `sufit_wpisu_bez_odczytu_h`, więc wpis blokowałby jej przypomnienie w każdym kolejnym tygodniu
    (klucz stanu to `member_id`). Obie przesłanki są konieczne — sam wiek nie wystarcza, bo po
    dłuższym przestoju WSZYSTKIE wpisy są starsze niż sufit, a jeden 429 z Graph zamykałby je
    razem z odpowiedziami czekającymi w czatach.

    O tym, czy pracownik odpisał, nie wiemy NIC — i dlatego nie wolno tu użyć `zamknij_bez_zapisu`:
    tamta wysyła `EXPIRED_TEXT` („Nie dostałem odpowiedzi"), czyli zarzut postawiony na podstawie
    naszej własnej awarii.

    Brak `client` w sygnaturze jest zamierzony i jest tu strażnikiem: bez niego nie ma czym wysłać
    wiadomości, więc „ciche" nie zależy od tego, czy ktoś o tym pamiętał. Z tego samego powodu nie
    ma `okno_domkniec` — cisza odkłada wiadomości, a wiadomości nie ma (patrz `do_domkniecia`).

    Status terminalny + `awaiting_yes=False` jak w `zamknij_bez_zapisu` (N38). Jeden zapis na całą
    porcję. O zamknięciu woła operatora WOŁAJĄCY — alert niesie liczbę i etykiety, których ta
    funkcja nie ma po co znać.
    """
    if not closed:
        return
    for pending in closed:
        pending.status = st.EXPIRED
        pending.awaiting_yes = False
    st.save_state(settings.state_path, state)


def _powiadom_o_zamknieciu(
    settings: Settings,
    client: GraphClient,
    closed: list[st.PendingReminder],
    tresc: Callable[[str], str],
    teraz: datetime,
    powod: str,
) -> None:
    """Wyślij uprzejme domknięcie osobom z zamkniętym tematem (stan EXPIRED już utrwalony).

    Izolacja per-osoba; nieudana wysyłka jest tylko logowana — status jest już terminalny, więc
    ani nie ponowimy zapisu, ani nie zdublujemy wiadomości przy kolejnym przebiegu. Treść jest
    parametrem, bo powody domknięcia są różne i KAŻDY komunikat musi być prawdziwy: „nie dostałem
    odpowiedzi" wolno napisać tylko temu, kto faktycznie nie odpisał.
    """
    for pending in closed:
        try:
            text = tresc(etykieta_tygodnia_iso(pending.week_start))
            do_pracownika(settings, client, pending.chat_id, to_html(text), teraz=teraz)
            logger.info("Zamknięto temat dla %s (%s)", etykiety.osoba(pending, settings), powod)
        # Patrz `wysylka.NIE_POLYKAJ`. Tutaj utrata sesji kosztuje najwięcej: przebieg, w którym
        # WSZYSTKIE tematy były domykane, kończyłby się po połknięciu cicho, a utrata tokenu
        # wyszłaby dopiero z pulsu — do 24 h później. Status jest już utrwalony, więc wyjście
        # w tym miejscu niczego nie psuje.
        except NIE_POLYKAJ:
            raise
        except Exception:
            logger.exception(
                "Nie udało się wysłać domknięcia do %s", etykiety.osoba(pending, settings)
            )


def zamknij_samodzielnie_uzupelnione(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    closed: list[st.PendingReminder],
    tz: ZoneInfo,
    teraz: datetime,
    *,
    okno_domkniec: OknoCiszy,
) -> None:
    """Zamknij tematy osób, które SAME uzupełniły grafik: SELF_FILLED utrwalony PRZED wysyłką.

    Wzorzec „co najwyżej raz" jak w ``zamknij_bez_zapisu``: najpierw commit terminalnego statusu
    (jeden zapis dla
    wszystkich), potem podziękowania. Podziękowanie leci BEZWARUNKOWO (nie zależy od
    ``send_expiry_message``, inaczej niż wygaśnięcie) — reaguje na działanie pracownika, więc
    milczenie byłoby gorsze niż uprzejme domknięcie (jak przy ``STALE_WEEK_TEXT``).

    Podziękowanie też idzie do kogoś, kto NIC nie napisał na czacie — stąd ta sama bramka co przy
    wygaszaniu (``do_domkniecia``).
    """
    closed = do_domkniecia(closed, teraz, okno_domkniec, "podziękowań za samouzupełnienie")
    if not closed:
        return
    for pending in closed:
        pending.status = st.SELF_FILLED
        # Temat domknięty — bramka szybkiej ścieżki zapisu przestaje obowiązywać (N38).
        # Dziś wpis terminalny i tak nie wchodzi do `open_items`, ale pozycja **D5** planu
        # (wznowienie rozmowy) tę własność zdejmie — i wtedy zostawiona otwarta flaga
        # znaczyłaby zapis po samym »tak«, którego nikt o nic nie pytał.
        pending.awaiting_yes = False
    st.save_state(settings.state_path, state)
    for pending in closed:
        podziekuj_za_samodzielne_uzupelnienie(settings, client, pending, tz, teraz)


def podziekuj_za_samodzielne_uzupelnienie(
    settings: Settings,
    client: GraphClient,
    pending: st.PendingReminder,
    tz: ZoneInfo,
    teraz: datetime,
) -> None:
    """Podziękuj za grafik, który uzupełnił się bez nas. Stan MUSI być już utrwalony.

    Do tego samego domknięcia dochodzi się DWIEMA drogami i każda utrwala stan inaczej: milczący
    pracownik zamykany hurtem (``zamknij_samodzielnie_uzupelnione`` → jeden ``save_state`` dla
    wszystkich) oraz
    ten, który powiedział „tak" na komplet już obecny w grafiku (``_apply_confirmed_yes`` →
    ``_commit`` z porcją wiadomości). Różni je WYŁĄCZNIE sposób zapisu, więc wspólna jest dokładnie
    ta część: etykieta tygodnia, treść i izolacja nieudanej wysyłki.

    Utrata sesji propaguje — dotyczy całej usługi, nie tej jednej wiadomości. Zwykła awaria wysyłki
    jest tylko logowana: status jest już terminalny, więc ponowienia i tak nie będzie, a wyjątek
    stąd zostałby wyżej zaraportowany jako „nie udało się obsłużyć odpowiedzi", która została
    obsłużona (ta sama pułapka, którą zamyka ``_powiadom_o_nieudanym_zapisie``).
    """
    week_label = etykieta_tygodnia_iso(pending.week_start)
    try:
        do_pracownika(
            settings,
            client,
            pending.chat_id,
            to_html(build_self_filled_text(week_label)),
            teraz=teraz,
        )
        logger.info(
            "Zamknięto temat dla %s (grafik uzupełniony samodzielnie)",
            etykiety.osoba(pending, settings),
        )
    # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
    except NIE_POLYKAJ:
        raise
    except Exception:
        logger.exception(
            "Nie udało się wysłać podziękowania do %s", etykiety.osoba(pending, settings)
        )


def powiadom_o_nieudanym_zapisie(
    settings: Settings, client: GraphClient, pending: st.PendingReminder, teraz: datetime
) -> None:
    """Powiedz pracownikowi, że zapis padł — we własnym ``try``, spójnie z resztą wysyłek.

    Bez tego opakowania awaria TEJ wysyłki leciała do ``_process_pending``, gdzie
    ``_record_failure``
    podbijał licznik prób na wpisie już terminalnym i logował mylące „nie udało się obsłużyć
    odpowiedzi" dla odpowiedzi, która została obsłużona.
    """
    try:
        tresc = build_write_failed_text(etykieta_tygodnia_iso(pending.week_start))
        do_pracownika(settings, client, pending.chat_id, to_html(tresc), teraz=teraz)
    # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
    except NIE_POLYKAJ:
        raise
    except Exception:
        logger.exception(
            "Zapis dla %s padł i nie udało się o tym powiadomić pracownika",
            etykiety.osoba(pending, settings),
        )
