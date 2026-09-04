"""Nasłuch odpowiedzi: od odczytu czatu do nieodwracalnego zapisu w Shifts.

Wszystko, co dzieje się PO wysłaniu prośby — interpretacja odpowiedzi, prośba o potwierdzenie,
zapis po jawnym „tak", domykanie tematów. Moduł nie wie, kiedy go wołać: terminarzem i pulsem
zajmuje się ``runtime.service``, a wysyłaniem próśb ``runtime.nudge``.

Dwa niezmienniki, na których stoi cała reszta i których nie wolno tu naruszyć:

1. **Zapis dopiero po jawnym „tak"** — model proponuje, kod decyduje, a nieodwracalny POST do
   Shifts następuje wyłącznie w ``_apply_confirmed_yes``.
2. **„Co najwyżej raz"** — stan jest utrwalany PRZED każdym nieodwracalnym skutkiem. Pominięcie
   naprawia człowiek, zdublowanie jest nie do cofnięcia w grafiku klienta.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from powiadomienia_teams import alerts
from powiadomienia_teams import state as st
from powiadomienia_teams.agent import tools as agent_tools
from powiadomienia_teams.agent.interpreter import (
    LlmClient,
    LlmNiedostepnyError,
    build_schedule,
    build_time_offs,
    interpret_reply,
    schedule_to_intervals,
)
from powiadomienia_teams.agent.odczyt import (
    SnapshotGrafikReader,
    opisz_uzgodnione_wolne,
    opisz_zmiany,
)
from powiadomienia_teams.config import Settings, TeamContext
from powiadomienia_teams.domain.czas import parse_graph_datetime, to_graph_iso
from powiadomienia_teams.domain.models import TimeOff, WeekSchedule
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.client import GraphClient, GraphTruncatedReadError
from powiadomienia_teams.messages import (
    DECLINED_TEXT,
    EXPIRED_TEXT,
    NO_CONFIRM_TEXT,
    STALE_WEEK_TEXT,
    UNCLEAR_TEXT,
    build_applied_text,
    build_confirm_text,
    build_nic_do_zapisania_text,
    build_unclear_text,
    to_html,
)
from powiadomienia_teams.reminders.detect import (
    drop_already_covered,
    member_filled_week,
    shift_weekdays,
)
from powiadomienia_teams.reminders.guards import CrossUserWriteError, ensure_single_owner
from powiadomienia_teams.reminders.lifecycle import (
    ReadOutcome,
    ready_for_self_fill_check,
    should_expire,
    still_writable,
)
from powiadomienia_teams.reminders.propose import baza_interpretacji
from powiadomienia_teams.reminders.replies import (
    advance_memory,
    history_for_llm,
    incoming_after,
    is_pure_affirmation,
    message_text,
)
from powiadomienia_teams.reminders.timeoff import resolve_time_off
from powiadomienia_teams.runtime import etykiety, operator
from powiadomienia_teams.runtime.budzet import PrzebiegPrzekroczylCzasError
from powiadomienia_teams.runtime.cisza import najblizsza_dozwolona, wolno_pisac
from powiadomienia_teams.runtime.domkniecia import (
    powiadom_o_nieudanym_zapisie,
    zamknij_bez_zapisu,
    zamknij_samodzielnie_uzupelnione,
)
from powiadomienia_teams.runtime.snapshot import SnapshotGrafiku
from powiadomienia_teams.runtime.wysylka import NIE_POLYKAJ, do_pracownika

logger = logging.getLogger(__name__)
_UTC = timezone.utc


@dataclass
class PollOutcome:
    """Wynik jednego przebiegu listenera — steruje adaptacyjnym odstępem w ``run_forever``.

    ``last_activity`` = moment ostatniej ZNANEJ aktywności: czas TEGO przebiegu, jeśli cokolwiek
    w nim obsłużyliśmy, inaczej najświeższy ``watermark`` wśród WCIĄŻ otwartych pendingów (albo
    ``None``). Cisza wydłuża odstęp, obsłużona odpowiedź skraca go do bazowego.

    Rozróżnienie „czas przebiegu" vs „czas wiadomości" jest istotne przy godzinnym suficie:
    świeżo wykryta odpowiedź mogła powstać 50 minut temu, więc liczenie ciszy od jej
    ``createdDateTime`` wrzuciłoby następny odstęp z powrotem pod sufit — i każda tura rozmowy
    (odpowiedź → pytanie potwierdzające → „tak" → zapis) kosztowałaby do godziny zamiast sekund.
    """

    open_count: int
    last_activity: datetime | None


def _latest_activity(pendings: list[st.PendingReminder]) -> datetime | None:
    """Najświeższy sparsowany ``watermark`` z listy pendingów (None, gdy brak poprawnych)."""
    times = []
    for pending in pendings:
        if pending.watermark:
            try:
                times.append(parse_graph_datetime(pending.watermark))
            except ValueError:
                continue
    return max(times) if times else None


def _build_writable(
    pending: st.PendingReminder, tz: ZoneInfo, group_id: str | None, now: datetime
) -> tuple[WeekSchedule, tuple[TimeOff, ...], int]:
    """Zbuduj to, co JESZCZE warto zapisać — czysto, bez I/O, żeby dało się to sprawdzić przed
    nieodwracalnym krokiem.

    Wpisy już zakończone są odsiewane (``still_writable``): tydzień docelowy mógł się zacząć,
    zanim pracownik potwierdził, a zmiana sprzed dwóch dni wpisana do grafiku jest dla menedżera
    fałszywym stanem faktycznym. Pusty wynik znaczy „nie ma czego zapisać" i MUSI zostać
    obsłużony przez wołającego, zanim ustawi APPLIED.

    Trzeci element to LICZBA odsianych wpisów. Bez niej zapis częściowy byłby nie do odróżnienia
    od pełnego i pracownik dostawałby „zapisałem Twoje zmiany" na komplet, którego nie zapisano —
    dziura w grafiku niewidoczna dla obu stron.
    """
    week_start = date.fromisoformat(pending.week_start)
    pelny = build_schedule(pending.member_id, week_start, pending.resolved, tz, group_id)
    # Powody czasu wolnego rozstrzygnięte już przy potwierdzeniu — tu tylko budujemy wpisy
    # (bez odczytu z Graph w sekcji krytycznej po APPLIED). Dni już objęte urlopem w Graphie
    # (`known_time_off_weekdays`) odsiewamy PRZED budową: nie tworzymy dla nich drugiego timeOff
    # i nie liczymy ich jako „pominięte" (to nie dziura w grafiku, tylko istniejący już urlop).
    juz_wolne = set(pending.known_time_off_weekdays)
    nowe_time_off = [
        wpis for wpis in pending.resolved_time_off if int(wpis.get("weekday", -1)) not in juz_wolne
    ]
    pelne_time_offs = build_time_offs(pending.member_id, week_start, nowe_time_off, tz)
    zmiany = still_writable(pelny.shifts, now)
    time_offs = still_writable(pelne_time_offs, now)
    pominiete = (len(pelny.shifts) - len(zmiany)) + (len(pelne_time_offs) - len(time_offs))
    return WeekSchedule(pelny.member_id, pelny.week_start, zmiany), time_offs, pominiete


def _odsiej_juz_zapisane(
    settings: Settings,
    snapshot: SnapshotGrafiku,
    pending: st.PendingReminder,
    schedule: WeekSchedule,
    time_offs: tuple[TimeOff, ...],
    tz: ZoneInfo,
) -> tuple[WeekSchedule, tuple[TimeOff, ...], tuple[int, ...]]:
    """Zajrzyj do Shifts TUŻ PRZED zapisem i odsiej dni, które ktoś już uzupełnił.

    Między prośbą o uzupełnienie a „tak" mijają DNI: od piątkowego przebiegu do terminu odpowiedzi
    w poniedziałek nad ranem, a prośba bota o potwierdzenie przesuwa ten termin jeszcze o dolną
    granicę kurtuazji. W tym czasie grafik bywa uzupełniany przez przełożonego albo przez
    samego pracownika w aplikacji. ``create_shift``/``create_time_off`` nie deduplikują, więc bez
    tego kroku po „tak" powstawał DRUGI komplet wpisów — nieodwracalnie, u klienta, z komunikatem
    „Zapisałem Twoje zmiany" na koniec.

    Detektor samouzupełnienia (``domkniecia.zamknij_samodzielnie_uzupelnione``) rozwiązywał tę
    samą sytuację, ale wyłącznie dla MILCZĄCYCH: wymaga ``ReadOutcome.NOTHING_NEW``. Kto odpisał
    „tak", ma ``HANDLED`` i szedł prosto w zapis — czyli ochrony nie miał dokładnie ten, kto zaraz
    coś zapisze.

    PRZEJŚCIOWO NIEUDANY ODCZYT NIE BLOKUJE ZAPISU. To świadome odstępstwo od zasady „brak dowodu
    ≠ dowód braku", którą stosujemy przy wygaszaniu: tam milczenie usługi zamieniało się
    w nieprawdziwy komunikat, a tu wstrzymanie zapisu kosztowałoby pracownika potwierdzenie, na
    które czekał, przy każdym mrugnięciu Graph. Degradujemy więc do zachowania sprzed poprawki
    (zapis bez weryfikacji), zostawiając ostrzeżenie w logu.

    Wyjątkiem jest ODCZYT UCIĘTY NA LIMICIE STRON (``GraphTruncatedReadError``): to jedyna awaria
    trwała i jedyna, która znaczy dosłownie „masz połowę danych". Odpuszczenie jej dałoby zapis
    w ciemno przy KAŻDYM potwierdzeniu, aż do końca życia instalacji — więc tu wstrzymujemy zapis
    i pozwalamy wyjątkowi lecieć wyżej.

    Dane biorą się ze WSPÓLNEGO ``SnapshotGrafiku`` przebiegu, nie z własnego odczytu per osoba:
    ``read_shifts`` pobiera całą kolekcję zespołu, więc dwadzieścia potwierdzeń dawało czterdzieści
    pełnych pobrań tuż przed nieodwracalnym zapisem — usługa sama ściągała na siebie dławienie
    w najgorszym momencie. Snapshot bywa więc o przebieg starszy niż „stan sprzed sekundy", co
    wobec okna 48 h, którego ten krok pilnuje, jest wymianą świadomą (patrz ``runtime.snapshot``).
    """
    try:
        dane = snapshot.dla_tygodnia(pending.week_start)
    except AuthExpiredError:
        raise  # utrata sesji dotyczy całej usługi, nie tego jednego zapisu
    except GraphTruncatedReadError:
        # JEDYNA awaria odczytu, która jest TRWAŁA i znaczy dosłownie „dane, których potrzebujesz,
        # są niepełne" — a skutkiem zapisu w ciemno jest drugi komplet wpisów, czyli dokładnie to,
        # przed czym stoi ten krok. Degradacja jest w porządku dla mrugnięcia sieci, ale nie tutaj:
        # `run_once` jest wobec tego wyjątku fail-closed z tego samego powodu. Wstrzymujemy zapis,
        # a że stan nie został jeszcze ruszony (`_odsiej_juz_zapisane` biegnie PRZED commitem),
        # kolejny tick przeczyta to samo „tak" i spróbuje ponownie.
        logger.error(
            "Odczyt grafiku %s ucięty na limicie stron — WSTRZYMUJĘ zapis zamiast dublować wpisy",
            etykiety.osoba(pending, settings),
        )
        raise
    if dane is None:
        logger.warning(
            "Nie udało się sprawdzić aktualnego grafiku %s przed zapisem — zapisuję bez weryfikacji",
            etykiety.osoba(pending, settings),
        )
        return schedule, time_offs, ()

    pokryte = shift_weekdays(pending.member_id, dane.zmiany, tz) | dane.wolne_dni.get(
        pending.member_id, frozenset()
    )
    zostaje, wolne, odsiane = drop_already_covered(
        schedule.shifts, time_offs, covered=pokryte, tz=tz
    )
    if odsiane:
        logger.info(
            "Pomijam dni już obecne w grafiku %s: %s", etykiety.osoba(pending, settings), sorted(odsiane)
        )
    return WeekSchedule(schedule.member_id, schedule.week_start, zostaje), wolne, odsiane


def _apply_schedule(
    client: GraphClient,
    ctx: TeamContext,
    pending: st.PendingReminder,
    schedule: WeekSchedule,
    time_offs: tuple[TimeOff, ...],
) -> None:
    """Zapisz ustalony grafik i czas wolny do Shifts (zespół z `ctx`, nie z globalnych ustawień).

    ``sharedShift``/``sharedTimeOff`` publikują wpis od razu (potwierdzone smoke-testem), więc
    osobny ``share`` jest zbędny; pracownik i tak dostaje potwierdzenie na czacie.

    Pętla zapisu jest WYŁĄCZONA z sufitu czasu przebiegu (``client.bez_limitu_czasu``). Powód:
    status jest już ``APPLYING``, więc przerwanie po trzecim z pięciu ``create_shift`` zostawia
    u klienta pół tygodnia, o którym nikt nie wie, które dni obejmuje. Limit powstał po to, żeby
    przebieg nie zjadał godzin na ODCZYTACH (do ``_MAX_PAGES`` stron), a nie po to, by rozrywać
    zapis w połowie — a liczba zapisów to najwyżej kilkanaście wpisów jednego tygodnia.
    """
    # Nigdy nie zapisz nic cudzą tożsamością (obejmuje zmiany i czas wolny).
    ensure_single_owner(pending.member_id, schedule, time_offs)
    with client.bez_limitu_czasu():
        for shift in schedule.shifts:
            client.create_shift(ctx.team_id, shift)
        for time_off in time_offs:
            client.create_time_off(ctx.team_id, time_off)


def poll_replies(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    *,
    now: datetime | None = None,
    ignoruj_cisze: bool = False,
) -> PollOutcome:
    """Przetwórz odpowiedzi (interpretuj → potwierdź → po »tak« zapisz), POTEM wygaś ciche okna.

    Kolejność jest niezmiennikiem bezpieczeństwa, nie szczegółem: odczyt MUSI wyprzedzać
    wygaszanie, inaczej ktoś, kto właśnie odpisał, dostaje domknięcie „nie dostałem odpowiedzi"
    w tym samym przebiegu (ADR 0003).

    Każdy pending obsługiwany jest w izolacji (błąd jednego nie kładzie pozostałych), a stan
    zapisywany PRZED nieodwracalnymi skutkami (zapis do Shifts, wysyłka domknięcia) — semantyka
    „co najwyżej raz": pominięcie naprawia człowiek, zdublowanie jest nieodwracalne. Zwraca
    ``PollOutcome`` (liczba otwartych + ostatnia aktywność) sterujący adaptacyjnym odstępem.
    """
    if settings.dry_run:
        logger.info("[dry-run] pomijam listener odpowiedzi")
        return PollOutcome(0, None)

    now = now or datetime.now(_UTC)
    # `ignoruj_cisze` materializuje się jako ustawienia Z WYŁĄCZONĄ ciszą, a nie jako flaga wleczona
    # przez kolejne wywołania. Dwa powody: szew wysyłki zostaje regułą BEZ WYJĄTKU (a więc nadal jest
    # siatką na nowy punkt wysyłki), a cała ścieżka widzi jeden, spójny świat — bez tego `--once
    # --ignoruj-cisze` przechodził bramę pętli i padał dopiero na szwie, czyli operator dostawał
    # „nie udało się powiadomić" zamiast prośby wysłanej świadomie.
    #
    # Wyłączenie obejmuje ODPOWIADANIE, nie domykanie. Kroki 1.5 i 2 piszą do ludzi, którzy niczego
    # nie napisali — „dziękuję za samodzielne uzupełnienie" i „Nie dostałem odpowiedzi" — a całe
    # uzasadnienie tej flagi mówi o odpowiadaniu tym, którzy WŁAŚNIE napisali. Przy domyślnym
    # terminie (poniedziałek 05:00, czyli wewnątrz ciszy) operator idący sekwencją wdrożenia
    # uruchamiał `--poll-once` i zawiadamiał o wygaśnięciu milczących pracowników o piątej rano.
    # Dlatego zapamiętujemy PIERWOTNE okno i domknięcia zostają pod nim.
    okno_pierwotne = settings.okno_ciszy
    if ignoruj_cisze:
        settings = replace(settings, cisza_od_h=0, cisza_do_h=0)
    # Godziny ciszy ODKŁADAJĄ CAŁĄ pracę, nie tylko wysyłkę. Pominięcie samej wiadomości rozjechałoby
    # stan z tym, co widzi pracownik: stan jest utrwalany PRZED skutkiem, więc zostałaby prośba
    # o potwierdzenie, której nikt nie dostał, a po terminie „nie doczekałem się potwierdzenia" —
    # za ciszę BOTA. Odłożona porcja czeka nietknięta (watermark nie rusza), więc po ciszy ta sama
    # wiadomość zostanie przeczytana normalnie. `ignoruj_cisze` jest dla `--poll-once`: operator
    # odpowiada wtedy ludziom, którzy właśnie napisali, i milczenie byłoby gorsze (decyzja 4.1/3).
    if not wolno_pisac(now, settings.okno_ciszy):
        logger.info(
            "Godziny ciszy — odkładam obieg nasłuchu do %s",
            najblizsza_dozwolona(now, settings.okno_ciszy).isoformat(),
        )
        # Ta wartość jest NIEODRÓŻNIALNA od „nie ma otwartych spraw" (linia niżej) i jest bezpieczna
        # WYŁĄCZNIE dzięki kolejności warunków w `service._poll_delay`: sufit ciszy stoi tam PRZED
        # `open_count == 0`. Przebieg tygodniowy miał ten sam kształt i kosztował tydzień dla całego
        # zespołu (patrz `cisza.CiszaWstrzymalaPrzebieg` i **N20**) — tam odmowa dostała własny typ,
        # bo konsumentem był orkiestrator odhaczający termin. Tutaj konsument jest jeden i decyduje
        # o samym odstępie, więc zostaje kolejność warunków, ale zapisana jako reguła, nie zbieg.
        return PollOutcome(0, None)
    state = st.load_state(settings.state_path)
    open_items = [
        p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
    ]
    if not open_items:
        return PollOutcome(0, None)

    client.refresh_auth()
    me_id = client.get_me()
    tz = settings.tz
    ctx = settings.team_context  # jeden zespół dziś; per-pending kontekst wepnie się tu (ADR 0001)
    # Jeden odczyt grafiku na tydzień na CAŁY przebieg — współdzielony przez sprawdzenie świeżości
    # przed zapisem (krok 1) i wykrywanie samouzupełnienia (krok 1.5). Powstaje tutaj, bo tu kończy
    # się jego życie: snapshot przeżywający przebieg przestałby być sprawdzeniem świeżości.
    # Kanał do operatora wstrzykiwany tak samo jak w `GraphClient`: snapshot ma zostać narzędziem
    # odczytu i nie znać konfiguracji, ale jego porażka jest decyzją o przyjęciu ryzyka
    # nieodwracalnej szkody — a taka decyzja nie może kończyć się w logu.
    snapshot = SnapshotGrafiku(
        client, ctx, tz,
        ostrzegaj=lambda tytul, tresc: operator.alert(settings, tytul, tresc),
    )

    # 1. NAJPIERW odczytaj i przetwórz odpowiedzi. Świeża odpowiedź przesuwa watermark, więc krok 2
    #    nie zamknie okna komuś, kto właśnie odpisał — brak wyścigu na krawędzi okna odpowiedzi.
    #    Wynik zapamiętujemy PER OSOBA: jeden bool na cały przebieg gubił informację o tym, kogo
    #    faktycznie udało się przeczytać, a bez niej krok 2 wygaszał także tych, których właśnie
    #    obsłużono albo których czatu nie dało się odczytać.
    outcomes: dict[str, ReadOutcome] = {}
    for pending in open_items:
        try:
            outcomes[pending.member_id] = _process_pending(
                settings, client, llm, ctx, pending, me_id, tz, state, now, snapshot
            )
        except AuthExpiredError:
            raise  # utrata tokenu zatrzymuje usługę — nie myl jej z awarią jednej odpowiedzi
        except PrzebiegPrzekroczylCzasError:
            # Wyczerpany czas dotyczy CAŁEGO obiegu. Połknięty tutaj oznaczałby, że każdy kolejny
            # pending dostaje `UNKNOWN` i obieg kończy się „normalnie" — a wtedy limit zamienia
            # jedną głośną awarię w ciszę rozłożoną na wszystkich rozmówców.
            raise
        except LlmNiedostepnyError:
            # To samo dla granicy modelu: skoro interpretacja nie działa, brnięcie przez resztę
            # listy kosztuje tylko kolejne nieudane żądania, a obieg kończyłby się sukcesem.
            # Wyżej (`runtime.service`) liczy się obiegi z rzędu i po progu woła operatora.
            raise
        except st.StateWriteError:
            # Kontrakt tego wyjątku (patrz `state.StateWriteError`) mówi wprost: wychodzi z CAŁEGO
            # przebiegu. Połknięty tutaj był groźny podwójnie — obieg brnął dalej bez możliwości
            # utrwalenia czegokolwiek, a kolejna osoba, której zapis się powiódł, utrwalała przy
            # okazji zmiany statusu poprzedniej, dokonane w pamięci przed nieudanym zapisem.
            raise
        except Exception:
            # Izolacja per-osoba — błąd jednej odpowiedzi nie blokuje pozostałych.
            outcomes[pending.member_id] = ReadOutcome.UNKNOWN
            logger.exception("Nie udało się obsłużyć odpowiedzi dla %s", etykiety.osoba(pending, settings))

    # 1.5. Kto MILCZY na czacie od dłuższej chwili, mógł uzupełnić grafik SAM w Shifts. Zaglądamy
    #    tam dopiero po odczycie czatu (odpowiedź ma pierwszeństwo: sprawdzamy tylko NOTHING_NEW)
    #    i tylko gdy bot już czeka (``ready_for_self_fill_check``). Krok PRZED wygaszaniem, więc
    #    niezmiennik brzmi: odpowiedź > sprawdzenie grafiku > wygaszenie — samouzupełniony dostaje
    #    podziękowanie, nie „nie dostałem odpowiedzi", i wypada z kandydatów do EXPIRED niżej.
    #    Grafik bierzemy ze wspólnego snapshotu przebiegu, więc dla tygodnia sprawdzonego już przy
    #    zapisie (krok 1) nie ma tu żadnego nowego pobrania. Skutek uboczny do zapamiętania: kto
    #    uzupełnił grafik W TRAKCIE tego przebiegu, zostanie zauważony dopiero w następnym — czyli
    #    z opóźnieniem podziękowania, nigdy z błędnym zapisem. Kierunek bezpieczny.
    kandydaci = [
        p
        for p in open_items
        if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
        and outcomes.get(p.member_id) is ReadOutcome.NOTHING_NEW
        and ready_for_self_fill_check(p, now, settings.self_fill_check_min_idle_s)
    ]
    if kandydaci:
        # BEZ alertu o „zapisie bez weryfikacji": ten odczyt nie poprzedza żadnego zapisu —
        # kandydaci to osoby MILCZĄCE, a krok kończy się podziękowaniem albo niczym. Alert
        # obiecywałby operatorowi szkodę, która nie ma jak się wydarzyć, i uczyłby go ignorować
        # kanał, którym przychodzą te prawdziwe (patrz `snapshot.dla_tygodnia`). Nic nie ginie:
        # gdy w tym samym przebiegu ścieżka ZAPISU sięgnie po ten tydzień, ostrzeżenie odpali się
        # wtedy — `_zaalarmowane` odkłada alert od danych właśnie po to.
        tygodnie = snapshot.dla_tygodni(
            {p.week_start for p in kandydaci}, alert_przy_porazce=False
        )
        samodzielni = [
            p
            for p in kandydaci
            if (dane := tygodnie.get(p.week_start)) is not None
            and member_filled_week(
                p.member_id, dane.zmiany, dane.wolne_dni.get(p.member_id, frozenset())
            )
        ]
        if samodzielni:
            zamknij_samodzielnie_uzupelnione(
                settings, client, state, samodzielni, tz, now, okno_domkniec=okno_pierwotne,
            )

    # 2. Wygaś te, które PO odczycie wciąż są otwarte, minął im termin I MAMY NA TO DOWÓD: udany
    #    odczyt, który nic nie przyniósł. Domyślne UNKNOWN dla braku wpisu w `outcomes` to
    #    zabezpieczenie, a NIE opis osiągalnej dziś ścieżki (każdy wpis ze `still_open` przeszedł
    #    przez pętlę wyżej): bezpieczna wartość domyślna nie może zależeć od tego, czy ktoś kiedyś
    #    doda tam wcześniejsze wyjście z pętli. Milczenie usługi nie jest milczeniem pracownika.
    #    Commit EXPIRED PRZED wysyłką domknięcia — semantyka „co najwyżej raz" (jak przy zapisie).
    still_open = [
        p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
    ]
    #    Samo ORZECZENIE wygaśnięcia liczymy zawsze; o tym, czy wolno je teraz wykonać (i napisać
    #    o nim człowiekowi, który nic nie napisał), rozstrzyga szew `domkniecia.do_domkniecia`
    #    z PIERWOTNYM oknem ciszy — także wtedy, gdy `--poll-once` ciszę świadomie pominął.
    newly_expired = [
        p
        for p in still_open
        if should_expire(
            p,
            now,
            settings.okno_odpowiedzi,
            read=outcomes.get(p.member_id, ReadOutcome.UNKNOWN),
        )
    ]
    # Rozdział po statusie, bo powody są RÓŻNE i każdy komunikat musi być prawdziwy: kto nie
    # odpisał w ogóle, słyszy „nie dostałem odpowiedzi"; kto odpisał, ale nie potwierdził —
    # „nie doczekałem się potwierdzenia". Podział PRZED `zamknij_bez_zapisu`, bo ono nadpisuje status.
    bez_odpowiedzi = [p for p in newly_expired if p.status == st.AWAITING_REPLY]
    bez_potwierdzenia = [p for p in newly_expired if p.status == st.AWAITING_CONFIRM]
    if bez_odpowiedzi:
        zamknij_bez_zapisu(
            settings, client, state, bez_odpowiedzi, EXPIRED_TEXT, now, "brak odpowiedzi",
            okno_domkniec=okno_pierwotne,
        )
    if bez_potwierdzenia:
        zamknij_bez_zapisu(
            settings, client, state, bez_potwierdzenia, NO_CONFIRM_TEXT, now,
            "brak potwierdzenia", okno_domkniec=okno_pierwotne,
        )

    # Ostatnia aktywność liczona z wciąż otwartych (po przetworzeniu): świeża odpowiedź skróci
    # następny odstęp, cisza go wydłuży (patrz ``_poll_delay``/``next_poll_delay``).
    progressed = any(outcome is ReadOutcome.HANDLED for outcome in outcomes.values())
    active = [p for p in still_open if p.status != st.EXPIRED]
    return PollOutcome(len(active), now if progressed else _latest_activity(active))


def _commit(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    wiadomosci: Sequence[tuple[str, str]],
    *,
    status: str = "",
    awaiting_yes: bool | None = None,
) -> None:
    """Utrwal stan RAZEM z przesunięciem watermarku i zmianą statusu — oba naraz albo wcale.

    Watermark NIE może być przesuwany z góry, przed przetworzeniem odpowiedzi: ``pending`` jest
    tym samym obiektem, który trzyma słownik ``state``, więc ``save_state`` wywołane przy obsłudze
    INNEJ osoby zserializowałoby też zaawansowany watermark tej, której obsługa właśnie padła.
    Jej odpowiedź stałaby się trwale niewidoczna (``incoming_after`` odsiewa wszystko sprzed
    watermarku), a po 48 h dostałaby nieprawdziwe „nie dostałem odpowiedzi". Wiązanie obu zapisów
    w jednym kroku sprawia, że nieudane przetworzenie zostawia watermark nietknięty i kolejny tick
    zobaczy tę odpowiedź ponownie.

    ``wiadomosci`` to WSZYSTKIE wiadomości pracownika obsłużone w tym kroku — pary
    ``(createdDateTime, treść)`` od najstarszej. Trafiają do pamięci rozmowy DOKŁADNIE tu, razem
    z watermarkiem: pamięć rośnie tylko po sukcesie i tym samym „co najwyżej raz" co watermark.
    Watermark ustawiamy na znacznik OSTATNIEJ z nich. ``advance_memory`` wylicza nowy stan
    z niezmienionej pamięci (nie akumuluje na miejscu), a pętla po stałej liście jest
    deterministyczna, więc ponowienie tej samej porcji nie dubluje wpisów.

    ``status`` przechodzi TĘDY, a nie przez przypisanie u wołającego, z dokładnie tego samego
    powodu co watermark — i to była luka, której poprawka watermarku nie objęła. ``pending`` jest
    obiektem WSPÓŁDZIELONYM ze słownikiem ``state``, więc status ustawiony przed nieudanym
    zapisem zostawał w pamięci procesu; wystarczyło, że kolejna osoba w tym samym przebiegu
    zakończyła się powodzeniem, a jej ``save_state`` utrwalał przy okazji cudze ``APPLYING`` —
    dla zapisu, którego nigdy nie zaczęto. ``APPLYING`` jest terminalny i nigdy nie wznawiany,
    więc uzgodnienie pracownika przepadało, a operator dostawał przy starcie alert kierujący go
    do grafiku, w którym nic się nie zmieniło.

    ``awaiting_yes`` przechodzi tędy z DOKŁADNIE tego samego powodu co ``status`` — jest bramką do
    nieodwracalnego zapisu, więc nie może zostać w pamięci procesu po nieudanym utrwaleniu stanu.
    ``None`` znaczy „nie dotykaj": ścieżki domykające temat terminalnie nie mają o tej fladze nic
    do powiedzenia i nie powinny udawać, że mają.

    Pusta porcja nie jest już cichym niewypałem: gdy przychodzi ze statusem albo ze zmianą flagi
    (domknięcia i wycofania bez nowych wiadomości), stan i tak trzeba utrwalić.
    """
    if status:
        pending.status = status
    if awaiting_yes is not None:
        pending.awaiting_yes = awaiting_yes
    if not wiadomosci:
        if not status and awaiting_yes is None:
            logger.debug("Nic do utrwalenia dla %s — pusta porcja i brak zmiany statusu",
                         etykiety.osoba(pending, settings))
            return
        st.save_state(settings.state_path, state)
        return
    pamiec, kotwica = pending.employee_memory, pending.memory_started_at
    for iso, tresc in wiadomosci:
        pamiec, kotwica = advance_memory(pamiec, kotwica, iso, tresc)
    pending.employee_memory, pending.memory_started_at = pamiec, kotwica
    pending.watermark = wiadomosci[-1][0]
    pending.fail_count = 0  # ta porcja obsłużona — licznik prób startuje od zera
    # Na ścieżkach domykających temat (`_apply_confirmed_yes` ustawia APPLYING PRZED tym
    # wywołaniem) dopisana wyżej pamięć zostanie odsiana przy samym zapisie — patrz
    # `state._do_zapisu`. To nie przeoczenie: obiekt w pamięci procesu ma zostać spójny
    # dla kodu, który jeszcze decyduje, co napisać pracownikowi.
    st.save_state(settings.state_path, state)


def _oznacz_wyslane(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    sent_at: str,
) -> None:
    """Zapisz, że bot właśnie odezwał się w OTWARTYM temacie — od tej chwili biegnie kurtuazja.

    Wołane PO udanej wysyłce, a nie przed, i to jest bezpieczny kierunek: znacznik wyłącznie
    ODDALA termin odpowiedzi (``lifecycle.termin_odpowiedzi`` bierze maksimum). Proces ubity między
    wysyłką a tym zapisem zostawia znacznik na poprzedniej wartości, czyli degraduje się do
    samego terminu kalendarzowego — nigdy do czegoś gorszego. Odwrotna kolejność (zapis przed
    wysyłką) dawałaby kurtuazję liczoną od wiadomości, której pracownik nigdy nie dostał.

    Znacznik bierzemy z czasu SERWERA (odpowiedź Graph), spójnie z ``watermark`` — porównujemy je
    ze sobą, więc muszą pochodzić z tego samego zegara. Fallback na czas lokalny, gdyby Graph go
    nie zwrócił.
    """
    pending.bot_last_message_at = sent_at or to_graph_iso(datetime.now(_UTC))
    st.save_state(settings.state_path, state)


def _process_pending(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    ctx: TeamContext,
    pending: st.PendingReminder,
    me_id: str,
    tz: ZoneInfo,
    state: dict[str, st.PendingReminder],
    now: datetime,
    snapshot: SnapshotGrafiku,
) -> ReadOutcome:
    """Dispatcher jednej odpowiedzi: czyste »tak« → zapis; wszystko inne → interpretacja.

    Zwraca wynik odczytu — ``HANDLED`` jest sygnałem dla ``_poll_delay``, żeby zresetować backoff
    do odstępu bazowego (rozmowa trwa, nie ma po co czekać do sufitu), a ``NOTHING_NEW`` jest
    JEDYNĄ przesłanką uprawniającą do wygaszenia (patrz ``lifecycle.should_expire``). Wyjątek
    oznacza ``UNKNOWN`` i jest nadawany w miejscu wywołania.
    """
    nowe = incoming_after(client.list_chat_messages(pending.chat_id), me_id, pending.watermark)
    if not nowe:
        return ReadOutcome.NOTHING_NEW
    # Cała porcja nowych wiadomości, od najstarszej. Decyzję podejmujemy z OSTATNIEJ, ale
    # wcześniejsze z tej samej porcji wchodzą do kontekstu i do pamięci — inaczej pracownik piszący
    # w dwóch dymkach byłby interpretowany wyłącznie z drugiego (patrz ``incoming_after``).
    wiadomosci = [(str(m.get("createdDateTime", "")), message_text(m)) for m in nowe]
    watermark = wiadomosci[-1][0]
    # Kontekst wieloturowy: WCZEŚNIEJSZE wiadomości pracownika (bez bieżącej), z uwzględnieniem
    # stałego okna 1 h. Liczone z NIEzmienionej pamięci — bieżąca porcja dojdzie dopiero w
    # ``_commit``, więc nie trafi do własnej historii, a nieudana obsługa nie zostawi duplikatu.
    history = history_for_llm(pending.employee_memory, pending.memory_started_at, watermark)
    history += [tresc for _iso, tresc in wiadomosci[:-1]]

    # Czyste „tak" po prośbie O POTWIERDZENIE → zapis. „Ok, ale nie będzie mnie w
    # czwartek" / „tak, ale w piątek 10-20" (potwierdzenie + poprawka) trafia do reinterpretacji,
    # żeby nie zapisać starej propozycji mimo prośby o zmianę.
    #
    # Bramką jest `pending.awaiting_yes`, nie sam status. Do 0.2.13 wystarczał `AWAITING_CONFIRM`,
    # a ten status ZOSTAJE po każdej wymianie odczytowej: gałąź `unclear` commitowała bez statusu.
    # Sekwencja „propozycja → pytanie o cokolwiek → »tak«" trafiała więc w szybką ścieżkę i ZAPISYWAŁA
    # do Shifts. Dziś mało groźne, bo propozycja się w międzyczasie nie zmieniła — ale mechanizm
    # istniał, a każda umiejętność odczytowa czyni go realnym: człowiek pyta „ile mam godzin",
    # dostaje odpowiedź, pisze „tak". Do czego?
    #
    # Kontrola statusu NIE zniknęła — przeniosła się o poziom wyżej: `poll_replies` przetwarza
    # wyłącznie wpisy `AWAITING_REPLY`/`AWAITING_CONFIRM` (`open_items`), więc flaga zostawiona na
    # wpisie terminalnym nie ma jak otworzyć zapisu. Mimo to każda ścieżka domykająca temat ją
    # kasuje: pozycja D5 planu (wznowienie rozmowy) zdejmie tę własność, a wtedy różnica przestanie
    # być teoretyczna.
    #
    # Dlaczego flaga, a nie cofnięcie statusu na `AWAITING_REPLY` (tak brzmiała pozycja B4 planu):
    # status ma DRUGIEGO konsumenta — rozstrzyga, który komunikat domknięcia dostanie pracownik
    # (`EXPIRED_TEXT` kontra `NO_CONFIRM_TEXT`). Cofnięcie go kazałoby wysłać „Nie dostałem
    # odpowiedzi" komuś, kto właśnie napisał, czyli naprawiłoby jeden defekt, tworząc drugi —
    # z tej samej rodziny, którą `NO_CONFIRM_TEXT` powstał, żeby zamknąć (`messages.py`).
    #
    # Warunek liczy CAŁĄ porcję ŁĄCZNIE, nie samą ostatnią wiadomość. Poprawka i potwierdzenie
    # przychodzą zwykle w dwóch dymkach („a w piątek jednak 10-20", chwilę później „ok"), a przy
    # odstępie odpytywania rosnącym do `poll_max_interval_s` obie wpadają w jedną porcję. Ocena
    # wyłącznie ostatniej z nich zapisywała wtedy `pending.resolved` sprzed poprawki i kwitowała to
    # komunikatem „Zapisałem Twoje zmiany" — czyli trzy szkody naraz: zgubiona odpowiedź,
    # nieodwracalny zapis niezgodny z wolą pracownika i komunikat twierdzący, że wszystko się
    # udało. To ta sama klasa błędu, którą `incoming_after` naprawił po stronie interpretacji.
    #
    # Sklejamy porcję w jeden tekst, zamiast sprawdzać każdy dymek osobno: `is_pure_affirmation`
    # wymaga, by padło słowo potwierdzenia, więc „ok" + „dzięki" per wiadomość dawałoby False na
    # drugim dymku i każde uprzejme potwierdzenie kosztowałoby wywołanie modelu. Sklejone „ok
    # dzięki" to nadal czyste potwierdzenie, a „a w piątek jednak 10-20 ok" — już nie.
    # Kierunek pozostaje ten sam co w `is_pure_affirmation`: cokolwiek poza potwierdzeniem →
    # reinterpretacja, bo dodatkowe wywołanie modelu jest odwracalne, a zapis nie.
    try:
        if pending.awaiting_yes and is_pure_affirmation(
            " ".join(tresc for _iso, tresc in wiadomosci)
        ):
            _apply_confirmed_yes(
                settings, client, ctx, pending, tz, state, wiadomosci, now, snapshot
            )
        else:
            _interpret_and_confirm(
                settings, client, llm, ctx, pending, tz, state, wiadomosci, history, now, snapshot
            )
    except AuthExpiredError:
        raise  # utrata tokenu dotyczy całej usługi, nie tej jednej wiadomości
    except PrzebiegPrzekroczylCzasError:
        # PRZED `_record_failure`: ta rozmowa niczym nie zawiniła, skończył się czas obiegu.
        # Doliczenie porażki byłoby karą za cudzy problem — po `_MAX_PENDING_FAILURES` obiegach
        # przerwanych limitem pracownik zostałby porzucony, choć nikt nawet nie przeczytał jego
        # odpowiedzi.
        raise
    except LlmNiedostepnyError:
        # Z dokładnie tego samego powodu: usługa modelu nie działa dla NIKOGO, więc dopisanie tej
        # rozmowie porażki (a po trzech — „Nie do końca zrozumiałem" i przesunięcie watermarku)
        # obwiniałoby pracownika o cudzą awarię i kasowałoby jego odpowiedź bezpowrotnie.
        raise
    except (GraphTruncatedReadError, st.StateWriteError):
        # Trzeci przypadek tej samej klasy — i najbardziej mylący, bo oba wyjątki istnieją PO TO,
        # żeby powiedzieć „dane są niepełne" / „nie umiem utrwalić stanu". Doliczone do licznika
        # porażek kończyły się po trzech obiegach odrzuceniem potwierdzenia pracownika i wysłanym
        # mu „Nie do końca zrozumiałem" — czyli awaria infrastruktury była komunikowana jako jego
        # niejasna odpowiedź, a jego »tak« znikało za przesuniętym watermarkiem. Sufit stronicowania
        # jest przy tym awarią TRWAŁĄ (kolekcja rośnie i nigdy nie maleje), więc trzy obiegi to
        # kwestia minut.
        raise
    except Exception:
        _record_failure(settings, client, state, pending, wiadomosci, now)
        raise  # wyżej loguje ślad — tu tylko decydujemy, czy próbować jeszcze raz
    return ReadOutcome.HANDLED


_MAX_PENDING_FAILURES = 3


def _record_failure(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    wiadomosci: Sequence[tuple[str, str]],
    now: datetime,
) -> None:
    """Policz nieudaną obsługę tej porcji; po ``_MAX_PENDING_FAILURES`` odpuść ją świadomie.

    Watermark rośnie dopiero po UDANEJ obsłudze — to celowe (patrz ``_commit``), bo chroni przed
    zgubieniem odpowiedzi przy awarii przejściowej. Ale przy błędzie DETERMINISTYCZNYM (ten sam
    tekst → ten sam wyjątek) ta sama wiadomość wraca w każdym ticku aż do wygaśnięcia okna:
    dziesiątki wywołań modelu, a na koniec pracownik, który odpisał, dostaje „nie dostałem
    odpowiedzi".

    Po kilku próbach przesuwamy więc watermark i prosimy o doprecyzowanie. Kilka, a nie jedna,
    bo awarie przejściowe (5xx z Graph, chwilowy timeout modelu) muszą mieć szansę się naprawić.
    """
    pending.fail_count += 1
    if pending.fail_count < _MAX_PENDING_FAILURES:
        st.save_state(settings.state_path, state)  # licznik utrwalony, watermark NIETKNIĘTY
        return

    logger.error(
        "Nie udało się obsłużyć odpowiedzi %s po %d próbach — pomijam tę wiadomość "
        "i proszę o doprecyzowanie",
        etykiety.osoba(pending, settings),
        pending.fail_count,
    )
    # Odpuszczenie porcji znaczy „nie wiem, co ten człowiek powiedział" — a wtedy jego późniejsze
    # samo „ok" tym bardziej nie wiadomo czego dotyczy. Bramka zapisu MUSI się więc zamknąć:
    # inaczej sekwencja „prośba o potwierdzenie → trzy nieudane obiegi → »ok«" trafia w szybką
    # ścieżkę i zapisuje do Shifts komplet, którego pracownik nie potwierdził (odtworzone sondą:
    # jedna zmiana w grafiku i „Zapisałem Twoje zmiany" po samym „ok"). Do 0.2.13 zamykało ją
    # cofnięcie statusu NIŻEJ — od B4 bramką jest flaga, a status odpowiada już tylko za to,
    # który komunikat domknięcia dostanie pracownik. To jest ta klasa pułapki, w której poprawka
    # nie psuje strażnika, tylko odbiera mu przedmiot ochrony: strażnik zostaje na miejscu i nadal
    # wygląda, jakby stał.
    #
    # Status wraca do AWAITING_REPLY, gdy pending czekał na potwierdzenie: pracownik ma usłyszeć
    # „Nie dostałem odpowiedzi", a nie „nie potwierdziłeś" — prośby, na którą miałby odpowiedzieć,
    # nikt skutecznie nie obsłużył. `resolved` ZOSTAJE jako baza (`propose.baza_interpretacji`),
    # więc uzgodnienia z rozmowy nie przepadają: wróci po nie reinterpretacja przy kolejnej
    # wiadomości.
    wraca_do_odpowiedzi = st.AWAITING_REPLY if pending.status == st.AWAITING_CONFIRM else ""
    # Odpuszczamy tę porcję — watermark rusza, więc rejestrujemy ją też w pamięci (raz),
    # spójnie z „co najwyżej raz". Wcześniejsze próby (1./2.) NIE ruszały watermarku ani pamięci.
    _commit(settings, state, pending, wiadomosci, status=wraca_do_odpowiedzi, awaiting_yes=False)
    # Commit PRZED wysyłką (jak wszędzie): nieudana wysyłka nie może cofnąć decyzji o odpuszczeniu,
    # bo wróciłaby dokładnie ta pętla, którą właśnie przerywamy.
    try:
        _oznacz_wyslane(
            settings, state, pending,
            do_pracownika(settings, client, pending.chat_id, to_html(UNCLEAR_TEXT), teraz=now),
        )
    # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
    except NIE_POLYKAJ:
        raise
    except Exception:
        logger.exception("Nie udało się poprosić %s o doprecyzowanie", etykiety.osoba(pending, settings))


def _apply_confirmed_yes(
    settings: Settings,
    client: GraphClient,
    ctx: TeamContext,
    pending: st.PendingReminder,
    tz: ZoneInfo,
    state: dict[str, st.PendingReminder],
    wiadomosci: Sequence[tuple[str, str]],
    now: datetime,
    snapshot: SnapshotGrafiku,
) -> None:
    """Czyste »tak« na etapie potwierdzenia → nieodwracalny zapis (commit stanu PRZED zapisem).

    ``wiadomosci`` to obsłużona porcja z tym »tak« — trafia do pamięci rozmowy przez ``_commit``
    mimo braku wywołania modelu (fast-path), żeby historia była kompletna, gdyby rozmowa toczyła
    się dalej.
    """
    # Co zostało do zapisania, liczymy PRZED commitem: to czysta operacja, a jej pusty wynik
    # znaczy zupełnie co innego niż udany zapis i musi dać inny status oraz inny komunikat.
    schedule, time_offs, minione = _build_writable(pending, tz, ctx.scheduling_group_id, now)
    if not schedule.shifts and not time_offs:
        # `awaiting_yes=False` mimo statusu terminalnego: N38 mówi „każda ścieżka domykająca temat",
        # bez wyjątku dla tej jednej. Dziś flaga zostawiona tutaj niczego nie otwiera (wpisy
        # `EXPIRED` wypadają z `open_items`), ale pozycja D5 planu ten filtr zdejmuje — a wtedy
        # różnica między „wszystkie ścieżki" a „wszystkie poza jedną" jest zapisem do Shifts.
        _commit(settings, state, pending, wiadomosci, status=st.EXPIRED, awaiting_yes=False)
        # Ta wysyłka NIE podlega `send_expiry_message`: to odpowiedź na jawne „tak" pracownika,
        # a milczenie po potwierdzeniu jest gorsze niż samo domknięcie. Własny `try` — awaria
        # wysyłki nie może lecieć wyżej jako „nie udało się obsłużyć odpowiedzi": stan jest już
        # utrwalony i terminalny, więc ponowienia i tak nie będzie.
        try:
            do_pracownika(settings, client, pending.chat_id, to_html(STALE_WEEK_TEXT), teraz=now)
        # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
        except NIE_POLYKAJ:
            raise
        except Exception:
            logger.exception(
                "Domknięto temat %s (miniony tydzień), ale nie udało się wysłać wiadomości",
                etykiety.osoba(pending, settings),
            )
        logger.info("Nic już do zapisania dla %s — tydzień docelowy minął", etykiety.osoba(pending, settings))
        return

    # Świeżość PRZED commitem, z tego samego powodu co wyżej: to odczyt, a jego pusty wynik znaczy
    # „ktoś już to uzupełnił", czyli inny status i inny komunikat niż udany zapis.
    schedule, time_offs, juz_w_grafiku = _odsiej_juz_zapisane(
        settings, snapshot, pending, schedule, time_offs, tz
    )
    if not schedule.shifts and not time_offs:
        # Cały potwierdzony komplet jest już w grafiku. To NIE jest wygaśnięcie ani miniony tydzień
        # — temat domyka się sukcesem, więc dostaje status i podziękowanie ścieżki samouzupełnienia.
        _commit(
            settings, state, pending, wiadomosci, status=st.SELF_FILLED, awaiting_yes=False,
        )
        # NIE podziękowanie ze ścieżki samouzupełnienia: pracownik przed chwilą potwierdził
        # konkretne godziny, a nie zapisano z nich nic. Musi usłyszeć KTÓRE dni i dlaczego.
        try:
            do_pracownika(
                settings, client, pending.chat_id,
                to_html(build_nic_do_zapisania_text(juz_w_grafiku)), teraz=now,
            )
        # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
        except NIE_POLYKAJ:
            raise
        except Exception:
            logger.exception(
                "Grafik %s był już uzupełniony, ale nie udało się o tym napisać",
                etykiety.osoba(pending, settings),
            )
        logger.info(
            "Nic do zapisania dla %s — dni %s są już w grafiku",
            etykiety.osoba(pending, settings),
            sorted(juz_w_grafiku),
        )
        return

    # Status APPLYING, nie APPLIED: commit MUSI wyprzedzić zapis (inaczej awaria po POST-cie daje
    # drugi komplet wpisów), ale „zapisane" wolno powiedzieć dopiero o zapisie POTWIERDZONYM.
    # Dopóki oba stany dzieliły jedną nazwę, nieudany zapis był nieodróżnialny od udanego: wpis
    # szedł do podsumowania jako „zapisany grafik", więc obietnica „pominięcie naprawia człowiek"
    # nie miała jak do tego człowieka dotrzeć — pracownik widział „uzupełnij ręcznie", menedżer
    # dziurę w grafiku, a raport tygodniowy twierdził, że wszystko się udało.
    _commit(settings, state, pending, wiadomosci, status=st.APPLYING, awaiting_yes=False)
    try:
        _apply_schedule(client, ctx, pending, schedule, time_offs)
    except CrossUserWriteError as blad:
        # Tripwire bezpieczeństwa — to NIE zwykła awaria sieci: odrzucono próbę zapisu grafiku
        # cudzą tożsamością. Loguj głośno (CRITICAL, ze śladem), osobno od transientnych 5xx,
        # i zgłoś kanałem, który ktoś czyta — log w instalacji bez monitoringu to cisza.
        logger.critical(
            "NARUSZENIE: odrzucono zapis grafiku cudzą tożsamością dla %s",
            etykiety.osoba(pending, settings),
            exc_info=True,
        )
        operator.alert(
            settings,
            "NARUSZENIE: zapis grafiku cudzą tożsamością",
            f"Odrzucono zapis dla {etykiety.osoba(pending, settings)}. {blad}",
            waga=alerts.KRYTYCZNY,
        )
        powiadom_o_nieudanym_zapisie(settings, client, pending, now)
        return
    except AuthExpiredError:
        # Utrata sesji dotyczy CAŁEJ usługi, nie tego jednego zapisu — ten sam kontrakt, co
        # w `_odsiej_juz_zapisane` (:195), `poll_replies` (:346) i `_process_pending` (:623).
        # Bez tej gałęzi `except Exception` niżej łapał ją PRZED tamtymi strażnikami: martwy
        # token był raportowany jako zwykła awaria Shifts, pracownik dostawał nieprawdziwe
        # „uzupełnij ręcznie", a ścieżka alert + `AUTH_FAILURE_EXIT_DELAY_S` + restart do
        # `--login` nie ruszała. Token zostawał martwy do najbliższego pulsu, czyli do doby.
        #
        # `logger.critical` PRZED `raise`, bo wyżej wyjątek nie niesie już informacji, KOGO
        # i którego tygodnia dotyczył przerwany zapis — a status jest w tym momencie `APPLYING`,
        # czyli terminalny i nigdy niewznawiany. Bez tej linii operator dostaje alert o utracie
        # sesji i żadnego wskazania, w czyim grafiku szukać dziury.
        logger.critical(
            "Utracono sesję przy zapisie grafiku dla %s (tydzień od %s) — zapis przerwany "
            "w stanie APPLYING, wymaga ręcznego sprawdzenia",
            etykiety.osoba(pending, settings),
            pending.week_start,
        )
        raise
    except Exception as blad:
        logger.exception("Zapis grafiku dla %s nie powiódł się", etykiety.osoba(pending, settings))
        operator.alert(
            settings,
            "Nieudany zapis grafiku po potwierdzeniu",
            f"{etykiety.osoba(pending, settings)} potwierdził zmiany, ale zapis do Shifts padł: "
            f"{operator.tresc_publiczna(blad)}. Grafik wymaga ręcznego uzupełnienia.",
        )
        powiadom_o_nieudanym_zapisie(settings, client, pending, now)
        return
    # Zapis POTWIERDZONY — dopiero teraz wolno nazwać go „zapisanym" w stanie i w raporcie.
    _commit(settings, state, pending, (), status=st.APPLIED)
    # Potwierdzenie idzie osobno: nieudana wysyłka potwierdzenia to NIE błąd
    # zapisu, więc nie wysyłaj mylnego „uzupełnij ręcznie" (zmiany są już w Shifts).
    # Zapis CZĘŚCIOWY dostaje własny tekst: „zapisałem Twoje zmiany" byłoby nieprawdą wobec dni,
    # które odpadły, a pracownik nie miałby jak się o tej dziurze dowiedzieć.
    tresc = build_applied_text(minione=minione, juz_w_grafiku=juz_w_grafiku)
    try:
        do_pracownika(settings, client, pending.chat_id, to_html(tresc), teraz=now)
        logger.info(
            "Zapisano grafik dla %s (pominięte: zakończonych %d, już w grafiku %d)",
            etykiety.osoba(pending, settings),
            minione,
            len(juz_w_grafiku),
        )
    # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
    except NIE_POLYKAJ:
        raise
    except Exception:
        logger.exception(
            "Zapisano grafik dla %s, ale nie udało się wysłać potwierdzenia", etykiety.osoba(pending, settings)
        )


def _interpret_and_confirm(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    ctx: TeamContext,
    pending: st.PendingReminder,
    tz: ZoneInfo,
    state: dict[str, st.PendingReminder],
    wiadomosci: Sequence[tuple[str, str]],
    history: list[str],
    now: datetime,
    snapshot: SnapshotGrafiku,
) -> None:
    """Interpretuj odpowiedź: confirm/modify → poproś o »tak«; decline/unclear → zamknij.

    ``history`` to wcześniejsze wiadomości pracownika (kontekst wieloturowy) — przekazywana do
    modelu; obsłużona porcja dojdzie do pamięci dopiero w ``_commit`` (nie trafia do własnej
    historii). Decyzję podejmujemy z OSTATNIEJ wiadomości porcji; wcześniejsze są już w ``history``.
    """
    text = wiadomosci[-1][1]
    week_start = date.fromisoformat(pending.week_start)
    # Gotowiec (»jak w zeszłym tygodniu«) i BAZA (to, co zapiszemy bez dalszych poprawek) to dwie
    # różne rzeczy od chwili pierwszej poprawki. Interpretacja idzie od bazy — inaczej druga
    # poprawka nanosiłaby się na oryginał (patrz `propose.baza_interpretacji`). Gotowiec zostaje
    # dostępny narzędziem, żeby „niech zostanie jak było" nadal miało do czego wrócić.
    gotowiec = build_schedule(
        pending.member_id, week_start, pending.proposal, tz, ctx.scheduling_group_id
    )
    proposal = build_schedule(
        pending.member_id,
        week_start,
        baza_interpretacji(pending.proposal, pending.resolved, pending.resolved_time_off),
        tz,
        ctx.scheduling_group_id,
    )
    # Narzędzia interpretera: WYŁĄCZNIE odczyt i wyłącznie w zakresie tego przypomnienia.
    # Zakres (czyj grafik, który tydzień) ustala ten kod — schematy narzędzi nie mają pola na
    # identyfikator pracownika, więc odpowiedź nie ma jak sięgnąć po cudzy grafik.
    reader = SnapshotGrafikReader(
        snapshot, member_id=pending.member_id, week_start=week_start, tz=tz
    )
    decision = interpret_reply(
        proposal,
        text,
        tz=tz,
        group_id=ctx.scheduling_group_id,
        llm=llm,
        history=history,
        uzgodnione_wolne=opisz_uzgodnione_wolne(pending.resolved_time_off),
        ctx=agent_tools.KontekstNarzedzi(
            reader=reader,
            proponowany=opisz_zmiany(gotowiec.shifts, tz),
            bazowy=opisz_zmiany(proposal.shifts, tz),
            week_start=week_start,
            # Data z `now` przebiegu, nie z zegara systemowego: to ona rozstrzyga „jutro" i „za
            # dwa tygodnie" w narzędziu kalendarza. W produkcji obie wartości są tożsame, ale bez
            # tego test ze sterowanym czasem miałby niedeterministyczny punkt odniesienia.
            dzis=now.astimezone(tz).date(),
        ),
    )
    if decision.action in ("confirm", "modify") and decision.schedule is not None:
        # Rozstrzygnij powody czasu wolnego TERAZ (przed potwierdzeniem), żeby wiadomość obiecała
        # dokładnie to, co zostanie zapisane, i nie zgubić dnia po cichu przy zapisie.
        resolved_time_off: list[dict[str, Any]] = []
        if decision.time_off:
            # Powody z tego samego cache'u, z którego korzystało narzędzie `shifts_read` —
            # jedna wiadomość pracownika to najwyżej jedno pobranie powodów z Graph.
            reasons = reader.powody_zespolu()
            resolved_time_off = resolve_time_off(list(decision.time_off), reasons)
            if len(resolved_time_off) < len(decision.time_off):
                logger.warning(
                    "Pominięto część dni wolnych dla %s — brak powodów czasu wolnego w zespole",
                    etykiety.osoba(pending, settings),
                )
        if decision.schedule.is_empty and not resolved_time_off:
            # Nic konkretnego do zapisania (np. urlop, ale zespół nie ma żadnych powodów czasu
            # wolnego) — nie obiecuj pustego zapisu, poproś o doprecyzowanie.
            _commit(settings, state, pending, wiadomosci, awaiting_yes=False)
            _oznacz_wyslane(
                settings, state, pending,
                do_pracownika(
                    settings, client, pending.chat_id,
                    to_html(build_unclear_text("brak_powodu_wolnego")), teraz=now,
                ),
            )
            return
        pending.resolved = schedule_to_intervals(decision.schedule, tz)
        pending.resolved_time_off = resolved_time_off
        _commit(
            settings, state, pending, wiadomosci,
            status=st.AWAITING_CONFIRM, awaiting_yes=True,
        )
        confirm = build_confirm_text(
            decision.schedule, resolved_time_off, tz, pominiete=decision.pominiete
        )
        # Okno na potwierdzenie MUSI biec od chwili, w której o nie poproszono — inaczej pending
        # obsłużony po przestoju wygasa w kolejnym cyklu (patrz `lifecycle._anchor`).
        # Commit idzie PRZED wysyłką (słusznie — nieudana wysyłka nie może cofnąć decyzji),
        # ale skoro prośby nie zobaczył NIKT, stan nie może twierdzić, że czekamy na „tak".
        # Zostawiony `AWAITING_CONFIRM` był trwały: watermark już się przesunął, a licznik
        # porażek wyzerował, więc żaden kolejny obieg tego nie naprawiał. Późniejsze samo
        # „ok" — choćby o czymś zupełnie innym — trafiało wtedy w szybką ścieżkę
        # i zapisywało do Shifts komplet, którego pracownik nigdy nie widział.
        # `resolved` ZOSTAJE jako baza (`propose.baza_interpretacji`), więc uzgodnienia
        # z rozmowy nie przepadają — wróci po nie reinterpretacja przy kolejnej wiadomości.
        #
        # NAPRAWA STANU obowiązuje przy KAŻDEJ awarii wysyłki, także tej, która propaguje
        # (`wysylka.NIE_POLYKAJ`): o tym, czy pracownik zobaczył prośbę, nie decyduje typ
        # awarii. Wcześniej oba propagujące wyjścia mijały `_commit`, zostawiając otwartą
        # bramkę zapisu przy prośbie, której nikt nie dostał — czyli dokładnie stan, przed
        # którym broni ta gałąź. Stąd `finally`, a nie `isinstance` w jednej gałęzi: tamten
        # kształt był poprawny, ale NIEWIDOCZNY dla strażnika czytającego nazwy wyjątków,
        # więc to miejsce wyglądało dla niego na połykające utratę sesji.
        naprawiono = False
        try:
            _oznacz_wyslane(
                settings, state, pending,
                do_pracownika(settings, client, pending.chat_id, to_html(confirm), teraz=now),
            )
            naprawiono = True  # wysyłka się udała — nie ma czego naprawiać
        except NIE_POLYKAJ:
            raise
        except Exception:
            logger.exception(
                "Nie udało się poprosić %s o potwierdzenie — wracam do oczekiwania na odpowiedź",
                etykiety.osoba(pending, settings),
            )
        finally:
            if not naprawiono:
                _commit(
                    settings, state, pending, (),
                    status=st.AWAITING_REPLY, awaiting_yes=False,
                )
    elif decision.action == "decline":
        _commit(settings, state, pending, wiadomosci, status=st.DECLINED, awaiting_yes=False)
        try:
            do_pracownika(settings, client, pending.chat_id, to_html(DECLINED_TEXT), teraz=now)
        # Utrata sesji i wysyłka z pominiętą bramką ciszy propagują — patrz `wysylka.NIE_POLYKAJ`.
        except NIE_POLYKAJ:
            raise
        except Exception:
            # Jedyna wysyłka domykająca temat, która nie miała własnego `try`. Wyjątek leciał do
            # `_record_failure`, a ten podbijał licznik prób na wpisie JUŻ TERMINALNYM i logował
            # mylące „nie udało się obsłużyć odpowiedzi" — dla odpowiedzi, która została obsłużona.
            logger.exception(
                "Zamknięto temat dla %s (odmowa), ale nie udało się go o tym powiadomić",
                etykiety.osoba(pending, settings),
            )
    else:
        _commit(settings, state, pending, wiadomosci, awaiting_yes=False)
        # Prośba o doprecyzowanie KONKRETNEJ rzeczy (enum z `agent.schema`, nie tekst modelu):
        # pracownik, który nie wie, co było niejasne, odpisuje to samo i okno wygasa.
        _oznacz_wyslane(
            settings, state, pending,
            do_pracownika(
                settings, client, pending.chat_id,
                to_html(build_unclear_text(decision.powod_niejasnosci)), teraz=now,
            ),
        )
