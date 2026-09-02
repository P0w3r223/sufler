"""Punkt składania i obieg powiadomień (Etapy 3–4).

Bezpieczniki:
- ``settings.dry_run`` (domyślnie włączony) — nic nie jest wysyłane ani zapisywane; przebieg
  tylko loguje. Realne działanie wymaga ``POWIADOMIENIA_DRY_RUN=false``.
- Zapis zmian następuje dopiero po jawnym „tak” pracownika (spirit ADR 0006).
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from powiadomienia_teams import alerts
from powiadomienia_teams import state as st
from powiadomienia_teams.agent.anthropic_llm import AnthropicLlm
from powiadomienia_teams.agent.interpreter import (
    LlmClient,
    build_schedule,
    build_time_offs,
    interpret_reply,
    schedule_to_intervals,
)
from powiadomienia_teams.config import ConfigError, Settings, TeamContext
from powiadomienia_teams.domain.models import Member, Shift, TimeOff, WeekSchedule
from powiadomienia_teams.graph.auth import (
    AmbiguousAccountError,
    AuthExpiredError,
    build_token_provider,
    login_interactive,
)
from powiadomienia_teams.graph.client import GraphClient, GraphPermissionError
from powiadomienia_teams.graph.mapping import parse_graph_datetime, to_graph_iso
from powiadomienia_teams.messages import (
    APPLIED_TEXT,
    DECLINED_TEXT,
    EXPIRED_TEXT,
    NO_CONFIRM_TEXT,
    PARTIAL_APPLIED_TEXT,
    STALE_WEEK_TEXT,
    UNCLEAR_TEXT,
    WRITE_FAILED_TEXT,
    build_confirm_text,
    build_nudge_text,
    build_self_filled_text,
    build_summary_text,
    to_html,
)
from powiadomienia_teams.reminders.detect import (
    member_filled_week,
    members_without_shifts,
    off_weekdays_by_member,
)
from powiadomienia_teams.reminders.guards import CrossUserWriteError, ensure_single_owner
from powiadomienia_teams.reminders.lifecycle import (
    ReadOutcome,
    past_hard_ceiling,
    prune_terminal,
    ready_for_self_fill_check,
    should_expire,
    still_writable,
)
from powiadomienia_teams.reminders.propose import proposal_from_last_week
from powiadomienia_teams.reminders.replies import (
    advance_memory,
    history_for_llm,
    is_pure_affirmation,
    message_text,
    newest_incoming,
)
from powiadomienia_teams.reminders.timeoff import resolve_time_off
from powiadomienia_teams.scheduler.backoff import next_poll_delay
from powiadomienia_teams.scheduler.send_window import in_send_window, next_send_window
from powiadomienia_teams.scheduler.weekly import next_run, previous_run, week_windows
from powiadomienia_teams.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)

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


def _wolno_inicjowac(settings: Settings, moment: datetime) -> bool:
    """Czy w ``moment`` wolno wysłać wiadomość, którą bot ZACZYNA sam (godziny ciszy).

    Dotyczy cotygodniowej prośby, domknięcia po wygaśnięciu okna i podziękowania za samodzielne
    uzupełnienie grafiku. Odpowiedź na wiadomość pracownika NIE przechodzi przez tę bramkę — on
    właśnie napisał i czeka; cisza byłaby wtedy gorsza od wiadomości o 22:00.

    W trybie próbnym zawsze ``True``: nic stąd nie wychodzi, a blokowanie przebiegu po godzinach
    odbierałoby operatorowi możliwość sprawdzenia wdrożenia o dowolnej porze.
    """
    if settings.dry_run:
        return True
    return in_send_window(
        moment,
        settings.tz,
        start_hour=settings.send_window_start_hour,
        end_hour=settings.send_window_end_hour,
        weekdays=settings.send_window_weekdays,
    )


def _najblizsze_okno(settings: Settings, moment: datetime) -> datetime:
    """Najbliższa chwila, w której wolno wysłać wiadomość inicjowaną przez bota."""
    return next_send_window(
        moment,
        settings.tz,
        start_hour=settings.send_window_start_hour,
        end_hour=settings.send_window_end_hour,
        weekdays=settings.send_window_weekdays,
    )


def run_once(
    settings: Settings,
    client: GraphClient,
    *,
    now: datetime,
    zegar: Callable[[], datetime] | None = None,
) -> list[Member]:
    """Jeden przebieg powiadomień: wykryj luki, zbuduj propozycje, wyślij (lub loguj w dry-run).

    ``now`` to odniesienie TYGODNIA docelowego — przy nadrobieniu jest to miniony termin, więc
    stoi w miejscu przez cały przebieg. ``zegar`` oddaje BIEŻĄCY czas i jest wołany osobno przed
    każdą wysyłką, bo dławienie Graph potrafi rozciągnąć przebieg poza okno ciszy. Te dwa czasy
    rozjeżdżają się celowo i dlatego są osobnymi parametrami; ten sam podział ma już
    ``_przebieg_i_podsumowanie``.

    Bez ``zegar`` przebieg czyta zegar systemowy — czyli zachowanie produkcyjne. Wstrzyknięcie go
    jest jedyną drogą, żeby sonda przebiegu nie zależała od DNIA I GODZINY swojego uruchomienia:
    domyślne okno wysyłki to pn-pt 8-18, więc bez tego szwu osiem sond padało w każdy weekend,
    a `pytest` bramkuje budowanie obrazu (`Dockerfile`).
    """
    teraz = zegar if zegar is not None else (lambda: datetime.now(_UTC))
    tz = settings.tz
    ctx = settings.team_context  # jeden zespół dziś; pętla po wielu wepnie się tutaj (ADR 0001)
    client.refresh_auth()
    me_id = client.get_me()
    # Konto bota NIGDY nie jest kandydatem do zagadnięcia. Bez tego bot pisze sam do siebie:
    # jest pełnoprawnym członkiem zespołu, więc `members_without_shifts` widzi je jak każdego
    # innego (potwierdzone na żywo — „Virtual WorkMate" trafiło na listę braków). Filtr w KODZIE,
    # nie tylko w `ONLY_USER_IDS`, bo pusta lista odbiorców oznacza „wszyscy" i wtedy konfiguracja
    # nie chroni przed niczym.
    members = [m for m in client.list_members(ctx.team_id) if m.user_id != me_id]

    prior_monday, target_monday, target_end = week_windows(now, tz)

    next_shifts = client.read_shifts(
        ctx.team_id, target_monday.astimezone(_UTC), target_end.astimezone(_UTC)
    )
    next_time_off = client.read_time_off(
        ctx.team_id, target_monday.astimezone(_UTC), target_end.astimezone(_UTC)
    )
    # Dni urlopu per osoba w docelowym tygodniu (liczone w strefie zespołu, target_monday lokalne).
    # Jedno źródło prawdy dla: detekcji (pełny tydzień wolny → pomiń), propozycji (nie proponuj
    # pracy w dniu wolnym) i treści (wspomnij o dniach wolnych). Urlop CZĘŚCIOWY nie wycisza już
    # prośby — piszemy o pozostałe dni.
    off_by_member = off_weekdays_by_member(next_time_off, target_monday, tz)
    missing = list(members_without_shifts(members, next_shifts, off_by_member))
    if settings.only_user_ids:  # tryb pilotażowy — ogranicz do wskazanych osób
        missing = [m for m in missing if m.user_id in settings.only_user_ids]
    prior_shifts = client.read_shifts(
        ctx.team_id, prior_monday.astimezone(_UTC), target_monday.astimezone(_UTC)
    )
    week_start_iso = target_monday.date().isoformat()
    week_label = f"{target_monday:%d.%m}–{(target_end - timedelta(days=1)):%d.%m}"

    state = st.load_state(settings.state_path)
    # Tygodniowe GC: usuń dawne wpisy terminalne (applied/declined/expired), by stan nie puchł.
    state = prune_terminal(state, now, settings.reply_window_hours)
    if not settings.dry_run and missing:
        # Zapisywalność stanu sprawdzana PRZED PIERWSZĄ wysyłką w przebiegu. Kolejność „wyślij,
        # potem utrwal" (niżej) jest bezpieczna tylko wtedy, gdy utrwalanie działa: przy pełnym
        # wolumenie wiadomość wychodziła, `save_state` padał, `_run_once_with_retry` ponawiał cały
        # przebieg — i ta sama osoba dostawała prośbę przy każdej próbie, a przez okno łaski
        # kilkadziesiąt razy. Odtworzone przy ENOSPC. Wyjątek jest NIEPONAWIALNY
        # (`StateWriteError`).
        st.ensure_writable(settings.state_path)
    sent = 0
    for member in missing:
        existing = state.get(member.user_id)
        # Idempotencja przebiegu: JEDNA prośba na osobę na TEN tydzień. Pomijamy każdy istniejący
        # wpis na bieżący tydzień — nie tylko otwarty (ponowienie po transientnym błędzie albo
        # nadrobienie nie wyśle drugi raz tej samej prośby), ale też TERMINALNY DECLINED/EXPIRED:
        # osoba już odmówiła albo jej okno minęło, więc restart/nadrobienie w tym samym tygodniu nie
        # może nadpisać jej stanu i zaczepić ponownie (sprzeczne z „kończę przypominanie"). Osoba z
        # udanym zapisem ma już zmianę w grafiku i nie występuje w `missing`.
        if existing is not None and existing.week_start == week_start_iso:
            continue
        if not settings.dry_run and not _wolno_inicjowac(settings, teraz()):
            # Okno sprawdzamy PRZED KAŻDĄ wysyłką, nie raz na przebieg: dławienie Graph potrafi
            # rozciągnąć przebieg na kilkadziesiąt minut, a wtedy prośby wychodziły już po ciszy.
            # Przerwanie jest bezpieczne — wysłani mają pending, reszta poczeka na otwarcie okna.
            raise OknoWysylkiZamknieteError(
                f"Okno wysyłki zamknęło się w trakcie przebiegu — {len(missing) - sent} osób "
                f"dostanie prośbę przy najbliższym otwarciu"
            )
        member_off = off_by_member.get(member.user_id, frozenset())
        proposal = proposal_from_last_week(
            member.user_id, prior_shifts, target_monday.date(), tz=tz, skip_weekdays=member_off
        )
        text = build_nudge_text(member, proposal, week_label, tz, off_weekdays=member_off)
        if settings.dry_run:
            logger.info(
                "[dry-run] powiadomienie do %s <%s>:\n%s", member.display_name, member.email, text
            )
            continue
        try:
            chat_id = client.create_or_get_chat(me_id, member.user_id)
            # Watermark = czas SERWERA wysłanego przypomnienia: listener bierze pod uwagę TYLKO
            # odpowiedzi po nudge'u, a nie stare wiadomości z czatu ani (przy przesuniętym lokalnym
            # zegarze) samą odpowiedź. Fallback na czas lokalny, gdyby Graph nie zwrócił znacznika.
            sent_at = client.send_chat_message(chat_id, to_html(text))
        except AuthExpiredError:
            raise  # utrata tokenu dotyczy wszystkich — zatrzymaj cały przebieg, nie jedną osobę
        except Exception:
            # Izolacja per-osoba: awaria jednej wysyłki nie blokuje reszty. Bez pendingu, więc
            # kolejny przebieg (nadrobienie/następny termin) spróbuje ponownie tej osoby.
            logger.exception(
                "Nie udało się powiadomić %s — pomijam, ponowię w kolejnym przebiegu",
                member.display_name,
            )
            continue
        # Fallback, gdy Graph nie zwrócił znacznika: realny „teraz", NIE `now`. Przy nadrobieniu
        # (`_catchup_due`) `now` to PRZESZŁY termin — użycie go cofnęłoby watermark przed faktyczny
        # czas wysyłki, przez co listener mógłby wziąć wcześniejszą wiadomość z czatu za odpowiedź.
        sent_iso = sent_at or to_graph_iso(teraz())
        state[member.user_id] = st.PendingReminder(
            member_id=member.user_id,
            member_name=member.display_name,
            chat_id=chat_id,
            week_start=week_start_iso,
            status=st.AWAITING_REPLY,
            watermark=sent_iso,
            nudged_at=sent_iso,  # niezmienny czas nudge'a — baza okna odpowiedzi
            proposal=schedule_to_intervals(proposal, tz),
            # Dni już objęte urlopem w Graphie: przy zapisie NIE tworzymy dla nich drugiego
            # timeOff, gdyby pracownik powtórzył je w odpowiedzi (`create_time_off` nie
            # deduplikuje).
            known_time_off_weekdays=sorted(member_off),
        )
        # Zapis PO KAŻDEJ wysyłce: awaria w połowie nie gubi już-wysłanych pendingów (ich odpowiedzi
        # będą czytane), a ponowienie pominie ich dzięki sprawdzeniu wyżej („co najmniej raz").
        st.save_state(settings.state_path, state)
        logger.info("Wysłano powiadomienie do %s", member.display_name)
        sent += 1

    if not settings.dry_run:
        st.save_state(settings.state_path, state)  # utrwal prune nawet gdy nic nie wysłano
    logger.info(
        "Przebieg zakończony: %d osób do powiadomienia; %s",
        len(missing),
        "dry-run (nic nie wysłano)" if settings.dry_run else f"wysłano {sent}",
    )
    return missing


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
    """
    # Nigdy nie zapisz nic cudzą tożsamością (obejmuje zmiany i czas wolny).
    ensure_single_owner(pending.member_id, schedule, time_offs)
    for shift in schedule.shifts:
        client.create_shift(ctx.team_id, shift)
    for time_off in time_offs:
        client.create_time_off(ctx.team_id, time_off)


def poll_replies(
    settings: Settings, client: GraphClient, llm: LlmClient, *, now: datetime | None = None
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
    state = st.load_state(settings.state_path)
    # Zaległe wiadomości z godzin ciszy PRZED wyjściem po pustej liście: czekają na wpisach już
    # TERMINALNYCH, więc `open_items` ich nie widzi, a to jedyny cykl, który biega dość często,
    # by trafić w otwarcie okna.
    _wyslij_odlozone(settings, client, state, now)
    open_items = [p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)]
    if not open_items:
        return PollOutcome(0, None)

    client.refresh_auth()
    me_id = client.get_me()
    tz = settings.tz
    ctx = settings.team_context  # jeden zespół dziś; per-pending kontekst wepnie się tu (ADR 0001)

    # 1. NAJPIERW odczytaj i przetwórz odpowiedzi. Świeża odpowiedź przesuwa watermark, więc krok 2
    #    nie zamknie okna komuś, kto właśnie odpisał — brak wyścigu na krawędzi okna odpowiedzi.
    #    Wynik zapamiętujemy PER OSOBA: jeden bool na cały przebieg gubił informację o tym, kogo
    #    faktycznie udało się przeczytać, a bez niej krok 2 wygaszał także tych, których właśnie
    #    obsłużono albo których czatu nie dało się odczytać.
    outcomes: dict[str, ReadOutcome] = {}
    for pending in open_items:
        try:
            outcomes[pending.member_id] = _process_pending(
                settings, client, llm, ctx, pending, me_id, tz, state, now
            )
        except AuthExpiredError:
            raise  # utrata tokenu zatrzymuje usługę — nie myl jej z awarią jednej odpowiedzi
        except Exception:
            # Izolacja per-osoba — błąd jednej odpowiedzi nie blokuje pozostałych.
            outcomes[pending.member_id] = ReadOutcome.UNKNOWN
            logger.exception("Nie udało się obsłużyć odpowiedzi dla %s", pending.member_name)

    _policz_nierozstrzygniete(settings, state, open_items, outcomes)

    # 1.5. Kto MILCZY na czacie od dłuższej chwili, mógł uzupełnić grafik SAM w Shifts. Zaglądamy
    #    tam dopiero po odczycie czatu (odpowiedź ma pierwszeństwo: sprawdzamy tylko NOTHING_NEW)
    #    i tylko gdy bot już czeka (``ready_for_self_fill_check``). Krok PRZED wygaszaniem, więc
    #    niezmiennik brzmi: odpowiedź > sprawdzenie grafiku > wygaszenie — samouzupełniony dostaje
    #    podziękowanie, nie „nie dostałem odpowiedzi", i wypada z kandydatów do EXPIRED niżej.
    #    Jeden odczyt Shifts na TYDZIEŃ na cały przebieg (zwraca grafik całego zespołu).
    kandydaci = [
        p
        for p in open_items
        if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
        and outcomes.get(p.member_id) is ReadOutcome.NOTHING_NEW
        and ready_for_self_fill_check(p, now, settings.self_fill_check_min_idle_s)
    ]
    if kandydaci:
        snapshot = _filled_weeks_snapshot(client, ctx, {p.week_start for p in kandydaci}, tz)
        samodzielni = [
            p
            for p in kandydaci
            if (dane := snapshot.get(p.week_start)) is not None
            and member_filled_week(p.member_id, dane[0], dane[1].get(p.member_id, frozenset()))
        ]
        if samodzielni:
            _close_self_filled(settings, client, state, samodzielni, tz, now)

    # 2. Wygaś te, które PO odczycie wciąż są otwarte, minął im termin I MAMY NA TO DOWÓD: udany
    #    odczyt, który nic nie przyniósł. Domyślne UNKNOWN dla braku wpisu w `outcomes` to
    #    zabezpieczenie, a NIE opis osiągalnej dziś ścieżki (każdy wpis ze `still_open` przeszedł
    #    przez pętlę wyżej): bezpieczna wartość domyślna nie może zależeć od tego, czy ktoś kiedyś
    #    doda tam wcześniejsze wyjście z pętli. Milczenie usługi nie jest milczeniem pracownika.
    #    Commit EXPIRED PRZED wysyłką domknięcia — semantyka „co najwyżej raz" (jak przy zapisie).
    still_open = [p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)]
    newly_expired = [
        p
        for p in still_open
        if should_expire(
            p,
            now,
            settings.reply_window_hours,
            read=outcomes.get(p.member_id, ReadOutcome.UNKNOWN),
        )
    ]
    # Rozdział po statusie, bo powody są RÓŻNE i każdy komunikat musi być prawdziwy: kto nie
    # odpisał w ogóle, słyszy „nie dostałem odpowiedzi"; kto odpisał, ale nie potwierdził —
    # „nie doczekałem się potwierdzenia". Podział PRZED `_close`, bo ono nadpisuje status.
    bez_odpowiedzi = [p for p in newly_expired if p.status == st.AWAITING_REPLY]
    bez_potwierdzenia = [p for p in newly_expired if p.status == st.AWAITING_CONFIRM]
    if bez_odpowiedzi:
        _close(settings, client, state, bez_odpowiedzi, EXPIRED_TEXT, "brak odpowiedzi", now)
    if bez_potwierdzenia:
        _close(
            settings, client, state, bez_potwierdzenia, NO_CONFIRM_TEXT, "brak potwierdzenia", now
        )

    # 3. TWARDY SUFIT: wpisy, którym normalna droga wygaszania jest trwale zamknięta (odczyt czatu
    #    pada w kółko, więc dowodu nigdy nie będzie), muszą kiedyś zejść ze stanu. Zamykamy je
    #    CICHO i z alertem — patrz `_close_bez_dowodu` i `lifecycle.past_hard_ceiling`.
    zablokowane = [
        p
        for p in state.values()
        if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
        and past_hard_ceiling(p, now, settings.reply_window_hours)
    ]
    if zablokowane:
        _close_bez_dowodu(settings, state, zablokowane)

    # Ostatnia aktywność liczona z wciąż otwartych (po przetworzeniu): świeża odpowiedź skróci
    # następny odstęp, cisza go wydłuży (patrz ``_poll_delay``/``next_poll_delay``).
    progressed = any(outcome is ReadOutcome.HANDLED for outcome in outcomes.values())
    active = [p for p in still_open if p.status != st.EXPIRED]
    return PollOutcome(len(active), now if progressed else _latest_activity(active))


_MAX_CYKLI_UNKNOWN = 20  # po tylu cyklach bez rozstrzygnięcia z rzędu alarmujemy operatora


def _policz_nierozstrzygniete(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    open_items: list[st.PendingReminder],
    outcomes: dict[str, ReadOutcome],
) -> None:
    """Zlicz cykle zakończone ``UNKNOWN`` i zaalarmuj, gdy wpis grzęźnie na odczycie czatu.

    Bez tego licznika awaria ``list_chat_messages`` była NIEWIDOCZNA dla eksploatacji: leci przed
    obsługą wiadomości, więc ``fail_count`` (liczony w ``_record_failure``) nie rósł, a
    ``should_expire`` słusznie odmawiał wygaszenia bez dowodu. Wpis wisiał otwarty w nieskończoność,
    co tydzień blokując ponowny nudge dla tej osoby — i nikt się o tym nie dowiadywał.

    Alert idzie DOKŁADNIE RAZ na serię (warunek na równość), jak w ``_puls_sesji``: trwała awaria
    nie ma prawa zatkać jedynego kanału niezależnego od Graph.
    """
    zaalarmowane: list[st.PendingReminder] = []
    zmienione = False
    for pending in open_items:
        if outcomes.get(pending.member_id, ReadOutcome.UNKNOWN) is not ReadOutcome.UNKNOWN:
            zmienione = zmienione or pending.unknown_count != 0
            pending.unknown_count = 0
            continue
        pending.unknown_count += 1
        zmienione = True
        if pending.unknown_count == _MAX_CYKLI_UNKNOWN:
            zaalarmowane.append(pending)
    if zmienione:
        # Zapis TYLKO przy realnej zmianie: cicha rozmowa budzi listener nawet co 10 s, a stan
        # leży na wolumenie — bezwarunkowy zapis co pobudkę byłby czystym zużyciem dysku.
        st.save_state(settings.state_path, state)
    if zaalarmowane:
        nazwy = ", ".join(sorted(p.member_name for p in zaalarmowane))
        logger.error(
            "Nie udało się rozstrzygnąć odczytu czatu %d razy z rzędu dla: %s",
            _MAX_CYKLI_UNKNOWN,
            nazwy,
        )
        _alert(
            settings,
            "Nie da się odczytać czatu przypomnienia",
            f"{_MAX_CYKLI_UNKNOWN} cykli z rzędu bez rozstrzygnięcia dla: {nazwy}. Dopóki trwa, "
            f"te osoby nie dostaną ani domknięcia, ani nowego przypomnienia.",
        )


def _close(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    closed: list[st.PendingReminder],
    text: str,
    powod: str,
    now: datetime,
) -> None:
    """Zamknij tematy terminalnie: status EXPIRED utrwalony PRZED wysyłką (»co najwyżej raz«)."""
    for pending in closed:
        pending.status = st.EXPIRED
    st.save_state(settings.state_path, state)
    if settings.send_expiry_message:
        _notify_closed(settings, client, state, closed, text, powod, now)


def _notify_closed(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    closed: list[st.PendingReminder],
    text: str,
    powod: str,
    now: datetime,
) -> None:
    """Wyślij uprzejme domknięcie osobom z zamkniętym tematem (stan EXPIRED już utrwalony).

    Izolacja per-osoba; nieudana wysyłka jest tylko logowana — status jest już terminalny, więc
    ani nie ponowimy zapisu, ani nie zdublujemy wiadomości przy kolejnym przebiegu. Treść jest
    parametrem, bo powody domknięcia są różne i KAŻDY komunikat musi być prawdziwy: „nie dostałem
    odpowiedzi" wolno napisać tylko temu, kto faktycznie nie odpisał.

    Domknięcie jest wiadomością INICJOWANĄ przez bota, więc podlega godzinom ciszy: poza oknem
    wysyłki ląduje w ``odlozona_wiadomosc`` i wychodzi przy najbliższym otwarciu okna. Status
    terminalny utrwalamy niezależnie od pory — cisza przesuwa wysyłkę, nie obieg.
    """
    html = to_html(text)
    odlozone = 0
    for pending in closed:
        if not _wolno_inicjowac(settings, now):
            pending.odlozona_wiadomosc = html
            odlozone += 1
            continue
        try:
            client.send_chat_message(pending.chat_id, html)
            logger.info("Zamknięto temat dla %s (%s)", pending.member_name, powod)
        except AuthExpiredError:
            # Utrata sesji dotyczy całej usługi, nie tej jednej wiadomości. Bez tego wyjątku
            # przebieg, w którym WSZYSTKIE tematy były domykane, kończyłby się cicho, a utrata
            # tokenu wyszłaby dopiero z pulsu — do 24 h później. Status jest już utrwalony,
            # więc wyjście tutaj niczego nie psuje.
            raise
        except Exception:
            logger.exception("Nie udało się wysłać domknięcia do %s", pending.member_name)
    if odlozone:
        st.save_state(settings.state_path, state)
        logger.info(
            "Odłożono %d domknięć poza oknem wysyłki — wyjdą %s",
            odlozone,
            _najblizsze_okno(settings, now).isoformat(),
        )


def _close_bez_dowodu(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    zablokowane: list[st.PendingReminder],
) -> None:
    """Zamknij wpisy, które przebiły twardy sufit wieku — CICHO, za to z alertem do operatora.

    Bez wiadomości do pracownika, bo nadal nie mamy dowodu, że milczał: sufit dotyczy wpisów,
    których czatu trwale nie da się odczytać (usunięty czat, konto poza tenantem, `chat_id` po
    starej instalacji). Wysłanie tam „nie dostałem odpowiedzi" byłoby zgadywaniem, a wysłanie
    czegokolwiek i tak najpewniej padnie. Alarmujemy operatora — to on ma to rozpoznać.
    """
    for pending in zablokowane:
        pending.status = st.EXPIRED
        logger.error(
            "Wpis %s (%s) przebił twardy sufit wieku bez rozstrzygniętego odczytu — zamykam cicho",
            pending.member_name,
            pending.member_id,
        )
    st.save_state(settings.state_path, state)
    _alert(
        settings,
        "Przypomnienia zablokowane na odczycie czatu",
        f"{len(zablokowane)} wpisów zamknięto po przekroczeniu twardego sufitu wieku bez "
        f"udanego odczytu czatu. Pracownicy NIE dostali wiadomości — sprawdź, czy czaty i konta "
        f"nadal istnieją: {', '.join(sorted(p.member_name for p in zablokowane))}",
    )


def _wyslij_odlozone(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    now: datetime,
) -> None:
    """Doślij wiadomości odłożone przez godziny ciszy, gdy okno wysyłki znów jest otwarte.

    Czekanie ma własny sufit (ten sam co ``past_hard_ceiling``): wiadomość, której nie udało się
    wysłać przez kilka dni, nie jest już uprzejmym domknięciem, tylko zagadką dla pracownika —
    i trzymałaby wpis poza zasięgiem ``prune_terminal`` w nieskończoność.

    Kolejka jest ZDEJMOWANA PRZED wysyłką i od razu utrwalana — ta sama zasada „co najwyżej raz",
    którą stosuje reszta modułu, i lustrzana do bramki w ``run_once``. Odwrotna kolejność
    (kasowanie flagi po udanej wysyłce, jeden zapis na końcu) dawała pętlę bez ucieczki: przy
    niezapisywalnym stanie flaga zostawała na dysku, ``StateWriteError`` leciał do pętli nasłuchu,
    ta wracała po 10 s i wysyłała to samo — ~360 wiadomości na godzinę do jednej osoby.

    Utrata odłożonego domknięcia jest tu świadomie tańsza niż jego powtórzenie: to uprzejmość,
    nie zapis do grafiku, a status terminalny i tak jest już utrwalony.
    """
    czekajace = [p for p in state.values() if p.odlozona_wiadomosc]
    if not czekajace:
        return
    przeterminowane = {
        p.member_id for p in czekajace if past_hard_ceiling(p, now, settings.reply_window_hours)
    }
    wolno = _wolno_inicjowac(settings, now)
    porzucone = [p for p in czekajace if p.member_id in przeterminowane]
    gotowe = [p for p in czekajace if p.member_id not in przeterminowane] if wolno else []
    if not porzucone and not gotowe:
        return

    # Bramka jak przed pierwszą wysyłką w `run_once`: bez zapisywalnego stanu nie wolno wysłać nic,
    # bo nie będzie czym odnotować, że już poszło.
    st.ensure_writable(settings.state_path)
    for pending in porzucone:
        logger.warning(
            "Porzucam odłożoną wiadomość do %s — czekała dłużej niż twardy sufit",
            pending.member_name,
        )
        pending.odlozona_wiadomosc = ""
    # Treść zabieramy PRZED wyczyszczeniem flagi — kolejka schodzi ze stanu w tym samym commicie.
    do_wyslania = [(p, p.odlozona_wiadomosc) for p in gotowe]
    for pending in gotowe:
        pending.odlozona_wiadomosc = ""
    st.save_state(settings.state_path, state)  # commit PRZED wysyłką

    if not do_wyslania:
        return
    client.refresh_auth()
    for pending, html in do_wyslania:
        try:
            client.send_chat_message(pending.chat_id, html)
            logger.info("Wysłano odłożoną wiadomość do %s", pending.member_name)
        except AuthExpiredError:
            raise  # utrata sesji dotyczy całej usługi (jak w `_notify_closed`)
        except Exception:
            logger.exception(
                "Nie udało się wysłać odłożonej wiadomości do %s — nie ponawiam "
                "(uprzejme domknięcie, nie zapis; ponowienie groziłoby serią)",
                pending.member_name,
            )


def _filled_weeks_snapshot(
    client: GraphClient,
    ctx: TeamContext,
    week_starts: set[str],
    tz: ZoneInfo,
) -> dict[str, tuple[tuple[Shift, ...], dict[str, frozenset[int]]] | None]:
    """Odczytaj grafik (zmiany + urlopy) każdego UNIKALNEGO tygodnia RAZ na przebieg.

    Klucz = ``week_start`` (ISO poniedziałek). Wartość = (zmiany zespołu w tym tygodniu, mapa
    dni urlopu per osoba z ``off_weekdays_by_member``) albo ``None``, gdy odczyt padł — brak
    dowodu, więc krok wyżej NIE zamknie nikogo w tym cyklu (ta sama filozofia „brak dowodu ≠
    dowód braku" co ``should_expire``). Utrata sesji propaguje się dalej — dotyczy całej usługi,
    nie jednego tygodnia.
    """
    snapshot: dict[str, tuple[tuple[Shift, ...], dict[str, frozenset[int]]] | None] = {}
    for ws in week_starts:
        try:
            monday = datetime.fromisoformat(ws).replace(tzinfo=tz)  # lokalna północ poniedziałku
            end = monday + timedelta(days=7)
            shifts = client.read_shifts(ctx.team_id, monday.astimezone(_UTC), end.astimezone(_UTC))
            time_off = client.read_time_off(
                ctx.team_id, monday.astimezone(_UTC), end.astimezone(_UTC)
            )
            snapshot[ws] = (shifts, off_weekdays_by_member(time_off, monday, tz))
        except AuthExpiredError:
            raise
        except Exception:
            logger.exception("Nie udało się odczytać grafiku tygodnia %s (Shifts)", ws)
            snapshot[ws] = None
    return snapshot


def _close_self_filled(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    closed: list[st.PendingReminder],
    tz: ZoneInfo,
    now: datetime,
) -> None:
    """Zamknij tematy osób, które SAME uzupełniły grafik: status SELF_FILLED utrwalony PRZED
    wysyłką.

    Wzorzec „co najwyżej raz" jak w ``_close``: najpierw commit terminalnego statusu (jeden
    zapis dla wszystkich), potem podziękowania. Podziękowanie leci BEZWARUNKOWO (nie zależy od
    ``send_expiry_message``, inaczej niż wygaśnięcie) — reaguje na działanie pracownika, więc
    milczenie byłoby gorsze niż uprzejme domknięcie (jak przy ``STALE_WEEK_TEXT``).

    Podziękowanie zaczyna bot (pracownik nic do niego nie napisał — uzupełnił grafik w Shifts),
    więc podlega godzinom ciszy: poza oknem czeka w ``odlozona_wiadomosc``. Status terminalny
    utrwalamy niezależnie od pory.
    """
    for pending in closed:
        pending.status = st.SELF_FILLED
    st.save_state(settings.state_path, state)
    odlozone = 0
    for pending in closed:
        monday = datetime.fromisoformat(pending.week_start).replace(tzinfo=tz)
        week_label = f"{monday:%d.%m}–{(monday + timedelta(days=6)):%d.%m}"
        html = to_html(build_self_filled_text(week_label))
        if not _wolno_inicjowac(settings, now):
            pending.odlozona_wiadomosc = html
            odlozone += 1
            continue
        try:
            client.send_chat_message(pending.chat_id, html)
            logger.info(
                "Zamknięto temat dla %s (grafik uzupełniony samodzielnie)", pending.member_name
            )
        except AuthExpiredError:
            raise  # utrata sesji dotyczy całej usługi, nie tej wiadomości (jak _notify_closed)
        except Exception:
            logger.exception("Nie udało się wysłać podziękowania do %s", pending.member_name)
    if odlozone:
        st.save_state(settings.state_path, state)
        logger.info(
            "Odłożono %d podziękowań poza oknem wysyłki — wyjdą %s",
            odlozone,
            _najblizsze_okno(settings, now).isoformat(),
        )


def _commit(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    watermark: str,
    reply_text: str | None = None,
) -> None:
    """Utrwal stan RAZEM z przesunięciem watermarku — jedyne miejsce, gdzie watermark rośnie.

    Watermark NIE może być przesuwany z góry, przed przetworzeniem odpowiedzi: ``pending`` jest
    tym samym obiektem, który trzyma słownik ``state``, więc ``save_state`` wywołane przy obsłudze
    INNEJ osoby zserializowałoby też zaawansowany watermark tej, której obsługa właśnie padła.
    Jej odpowiedź stałaby się trwale niewidoczna (``newest_incoming`` odsiewa wszystko sprzed
    watermarku), a po 48 h dostałaby nieprawdziwe „nie dostałem odpowiedzi". Wiązanie obu zapisów
    w jednym kroku sprawia, że nieudane przetworzenie zostawia watermark nietknięty i kolejny tick
    zobaczy tę odpowiedź ponownie.

    ``reply_text`` (gdy podany) to treść WŁAŚNIE obsłużonej wiadomości pracownika — dopisujemy ją
    do pamięci rozmowy DOKŁADNIE tu, razem z watermarkiem: pamięć rośnie tylko po sukcesie i tym
    samym „co najwyżej raz" co watermark. ``advance_memory`` wylicza nowy stan z niezmienionej
    pamięci (nie akumuluje na miejscu), więc ponowienie tej samej wiadomości nie dubluje wpisu.
    """
    pending.watermark = watermark
    pending.fail_count = 0  # ta wiadomość obsłużona — licznik prób startuje od zera
    if reply_text is not None:
        pending.employee_memory, pending.memory_started_at = advance_memory(
            pending.employee_memory, pending.memory_started_at, watermark, reply_text
        )
    st.save_state(settings.state_path, state)


@dataclass(frozen=True)
class _Migawka:
    """Stan pendingu sprzed commitu — do cofnięcia, gdy wysyłka PO commicie nie doszła.

    Commit przed wysyłką jest regułą tego modułu („co najwyżej raz"), ale ma sens tylko dla
    skutków NIEODWRACALNYCH (zapis do Shifts). Dla wiadomości, która nie doszła, utrwalony stan
    jest po prostu nieprawdziwy — i to na nim opiera się późniejsze domknięcie rozmowy.
    """

    status: str
    watermark: str
    employee_memory: tuple[str, ...]
    memory_started_at: str
    fail_count: int
    resolved: tuple[dict[str, Any], ...]
    resolved_time_off: tuple[dict[str, Any], ...]

    @classmethod
    def z_pendingu(cls, pending: st.PendingReminder) -> _Migawka:
        return cls(
            status=pending.status,
            watermark=pending.watermark,
            employee_memory=tuple(pending.employee_memory),
            memory_started_at=pending.memory_started_at,
            fail_count=pending.fail_count,
            resolved=tuple(pending.resolved),
            resolved_time_off=tuple(pending.resolved_time_off),
        )

    def przywroc(self, pending: st.PendingReminder) -> None:
        pending.status = self.status
        pending.watermark = self.watermark
        pending.employee_memory = list(self.employee_memory)
        pending.memory_started_at = self.memory_started_at
        pending.fail_count = self.fail_count
        pending.resolved = list(self.resolved)
        pending.resolved_time_off = list(self.resolved_time_off)


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
) -> ReadOutcome:
    """Dispatcher jednej odpowiedzi: czyste »tak« → zapis; wszystko inne → interpretacja.

    Zwraca wynik odczytu — ``HANDLED`` jest sygnałem dla ``_poll_delay``, żeby zresetować backoff
    do odstępu bazowego (rozmowa trwa, nie ma po co czekać do sufitu), a ``NOTHING_NEW`` jest
    JEDYNĄ przesłanką uprawniającą do wygaszenia (patrz ``lifecycle.should_expire``). Wyjątek
    oznacza ``UNKNOWN`` i jest nadawany w miejscu wywołania.
    """
    # Nadawca musi być DOKŁADNIE tą osobą, o której grafik pytamy (`pending.member_id`) — sam
    # warunek „nie bot" wpuszczał do interpretacji wiadomości systemowe i wpisy innych tożsamości,
    # a ich treść mogła skończyć w GRAFIKU pracownika.
    incoming = newest_incoming(
        client.list_chat_messages(pending.chat_id), me_id, pending.member_id, pending.watermark
    )
    if incoming is None:
        return ReadOutcome.NOTHING_NEW
    watermark = str(incoming.get("createdDateTime", ""))
    text = message_text(incoming)
    # Kontekst wieloturowy: WCZEŚNIEJSZE wiadomości pracownika (bez bieżącej), z uwzględnieniem
    # stałego okna 1 h. Liczone z NIEzmienionej pamięci — bieżąca wiadomość dojdzie dopiero w
    # ``_commit``, więc nie trafi do własnej historii, a nieudana obsługa nie zostawi duplikatu.
    history = history_for_llm(pending.employee_memory, pending.memory_started_at, watermark)

    # Czyste „tak" w stanie oczekiwania na potwierdzenie → zapis. „Ok, ale nie będzie mnie w
    # czwartek" / „tak, ale w piątek 10-20" (potwierdzenie + poprawka) trafia do reinterpretacji,
    # żeby nie zapisać starej propozycji mimo prośby o zmianę.
    try:
        if pending.status == st.AWAITING_CONFIRM and is_pure_affirmation(text):
            _apply_confirmed_yes(settings, client, ctx, pending, tz, state, watermark, now, text)
        else:
            _interpret_and_confirm(
                settings, client, llm, ctx, pending, text, tz, state, watermark, history
            )
    except AuthExpiredError:
        raise  # utrata tokenu dotyczy całej usługi, nie tej jednej wiadomości
    except Exception:
        _record_failure(settings, client, state, pending, watermark, text)
        raise  # wyżej loguje ślad — tu tylko decydujemy, czy próbować jeszcze raz
    return ReadOutcome.HANDLED


_MAX_PENDING_FAILURES = 3


def _record_failure(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    watermark: str,
    text: str,
) -> None:
    """Policz nieudaną obsługę tej wiadomości; po ``_MAX_PENDING_FAILURES`` odpuść ją świadomie.

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
        pending.member_name,
        pending.fail_count,
    )
    # Odpuszczamy tę wiadomość — watermark rusza, więc rejestrujemy ją też w pamięci (raz),
    # spójnie z „co najwyżej raz". Wcześniejsze próby (1./2.) NIE ruszały watermarku ani pamięci.
    # Przesuwa watermark i zeruje licznik.
    _commit(settings, state, pending, watermark, reply_text=text)
    # Commit PRZED wysyłką (jak wszędzie): nieudana wysyłka nie może cofnąć decyzji o odpuszczeniu,
    # bo wróciłaby dokładnie ta pętla, którą właśnie przerywamy.
    try:
        client.send_chat_message(pending.chat_id, to_html(UNCLEAR_TEXT))
    except Exception:
        logger.exception("Nie udało się poprosić %s o doprecyzowanie", pending.member_name)


def _apply_confirmed_yes(
    settings: Settings,
    client: GraphClient,
    ctx: TeamContext,
    pending: st.PendingReminder,
    tz: ZoneInfo,
    state: dict[str, st.PendingReminder],
    watermark: str,
    now: datetime,
    text: str,
) -> None:
    """Czyste »tak« na etapie potwierdzenia → nieodwracalny zapis (commit stanu PRZED zapisem).

    ``text`` to treść tego »tak« — trafia do pamięci rozmowy przez ``_commit`` mimo braku
    wywołania modelu (fast-path), żeby historia była kompletna, gdyby rozmowa toczyła się dalej.
    """
    # Co zostało do zapisania, liczymy PRZED commitem: to czysta operacja, a jej pusty wynik
    # znaczy zupełnie co innego niż udany zapis i musi dać inny status oraz inny komunikat.
    schedule, time_offs, pominiete = _build_writable(pending, tz, ctx.scheduling_group_id, now)
    if not schedule.shifts and not time_offs:
        pending.status = st.EXPIRED
        _commit(settings, state, pending, watermark, reply_text=text)
        # Ta wysyłka NIE podlega `send_expiry_message`: to odpowiedź na jawne „tak" pracownika,
        # a milczenie po potwierdzeniu jest gorsze niż samo domknięcie. Własny `try` — awaria
        # wysyłki nie może lecieć wyżej jako „nie udało się obsłużyć odpowiedzi": stan jest już
        # utrwalony i terminalny, więc ponowienia i tak nie będzie.
        try:
            client.send_chat_message(pending.chat_id, to_html(STALE_WEEK_TEXT))
        except Exception:
            logger.exception(
                "Domknięto temat %s (miniony tydzień), ale nie udało się wysłać wiadomości",
                pending.member_name,
            )
        logger.info("Nic już do zapisania dla %s — tydzień docelowy minął", pending.member_name)
        return

    pending.status = st.APPLIED
    # commit PRZED zapisem — brak dubli przy awarii; text → pamięć (fast-path bez modelu)
    _commit(settings, state, pending, watermark, reply_text=text)
    try:
        _apply_schedule(client, ctx, pending, schedule, time_offs)
    except CrossUserWriteError:
        # Tripwire bezpieczeństwa — to NIE zwykła awaria sieci: odrzucono próbę zapisu grafiku
        # cudzą tożsamością. Loguj głośno (CRITICAL, ze śladem), osobno od transientnych 5xx.
        logger.critical(
            "NARUSZENIE: odrzucono zapis grafiku cudzą tożsamością dla %s",
            pending.member_name,
            exc_info=True,
        )
        client.send_chat_message(pending.chat_id, to_html(WRITE_FAILED_TEXT))
        return
    except AuthExpiredError:
        # Utrata sesji dotyczy CAŁEJ usługi, nie zapisu tej jednej osoby. W gałęzi ogólnej log
        # mówił „Zapis grafiku nie powiódł się" (zła przyczyna — nikt nie szukał wtedy `--login`),
        # a zaraz po nim szła wysyłka „uzupełnij ręcznie", która i tak padała na tym samym tokenie.
        # Status jest już APPLIED i utrwalony, więc zatrzymanie tutaj niczego nie psuje.
        logger.error(
            "Utracono sesję przy zapisie grafiku dla %s — zatrzymuję usługę", pending.member_name
        )
        raise
    except Exception:
        logger.exception("Zapis grafiku dla %s nie powiódł się", pending.member_name)
        client.send_chat_message(pending.chat_id, to_html(WRITE_FAILED_TEXT))
        return
    # Zapis się POWIÓDŁ — potwierdzenie idzie osobno: nieudana wysyłka potwierdzenia to NIE błąd
    # zapisu, więc nie wysyłaj mylnego „uzupełnij ręcznie" (zmiany są już w Shifts).
    # Zapis CZĘŚCIOWY dostaje własny tekst: „zapisałem Twoje zmiany" byłoby nieprawdą wobec dni,
    # które odpadły, a pracownik nie miałby jak się o tej dziurze dowiedzieć.
    tresc = PARTIAL_APPLIED_TEXT if pominiete else APPLIED_TEXT
    try:
        client.send_chat_message(pending.chat_id, to_html(tresc))
        logger.info(
            "Zapisano grafik dla %s (wpisów pominiętych jako zakończone: %d)",
            pending.member_name,
            pominiete,
        )
    except Exception:
        logger.exception(
            "Zapisano grafik dla %s, ale nie udało się wysłać potwierdzenia", pending.member_name
        )


def _interpret_and_confirm(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    ctx: TeamContext,
    pending: st.PendingReminder,
    text: str,
    tz: ZoneInfo,
    state: dict[str, st.PendingReminder],
    watermark: str,
    history: list[str],
) -> None:
    """Interpretuj odpowiedź: confirm/modify → poproś o »tak«; decline/unclear → zamknij.

    ``history`` to wcześniejsze wiadomości pracownika (kontekst wieloturowy) — przekazywana do
    modelu; bieżąca ``text`` dojdzie do pamięci dopiero w ``_commit`` (nie trafia do własnej
    historii).
    """
    proposal = build_schedule(
        pending.member_id,
        date.fromisoformat(pending.week_start),
        pending.proposal,
        tz,
        ctx.scheduling_group_id,
    )
    decision = interpret_reply(
        proposal, text, tz=tz, group_id=ctx.scheduling_group_id, llm=llm, history=history
    )
    if decision.action in ("confirm", "modify") and decision.schedule is not None:
        # Rozstrzygnij powody czasu wolnego TERAZ (przed potwierdzeniem), żeby wiadomość obiecała
        # dokładnie to, co zostanie zapisane, i nie zgubić dnia po cichu przy zapisie.
        resolved_time_off: list[dict[str, Any]] = []
        if decision.time_off:
            reasons = client.list_time_off_reasons(ctx.team_id)
            resolved_time_off = resolve_time_off(list(decision.time_off), reasons)
            if len(resolved_time_off) < len(decision.time_off):
                logger.warning(
                    "Pominięto część dni wolnych dla %s — brak powodów czasu wolnego w zespole",
                    pending.member_name,
                )
        if decision.schedule.is_empty and not resolved_time_off:
            # Nic konkretnego do zapisania (np. urlop, ale zespół nie ma żadnych powodów czasu
            # wolnego) — nie obiecuj pustego zapisu, poproś o doprecyzowanie.
            _commit(settings, state, pending, watermark, reply_text=text)
            client.send_chat_message(pending.chat_id, to_html(UNCLEAR_TEXT))
            return
        migawka = _Migawka.z_pendingu(pending)
        pending.resolved = schedule_to_intervals(decision.schedule, tz)
        pending.resolved_time_off = resolved_time_off
        pending.status = st.AWAITING_CONFIRM
        _commit(settings, state, pending, watermark, reply_text=text)
        confirm = build_confirm_text(decision.schedule, resolved_time_off, tz)
        try:
            client.send_chat_message(pending.chat_id, to_html(confirm))
        except Exception:
            # Nie udało się ZAPYTAĆ o potwierdzenie — wpisu nie wolno zostawić w AWAITING_CONFIRM.
            # Po 48 h dostałby domknięcie „nie doczekałem się potwierdzenia", czyli zarzut o
            # milczenie wobec pytania, którego pracownik nigdy nie zobaczył. Cofamy cały commit
            # (status, watermark, pamięć, licznik prób), więc kolejny cykl przeczyta tę samą
            # odpowiedź jeszcze raz; po `_MAX_PENDING_FAILURES` nieudanych próbach `_record_failure`
            # domknie pętlę PRAWDZIWYM komunikatem — prośbą o doprecyzowanie.
            migawka.przywroc(pending)
            st.save_state(settings.state_path, state)
            raise
    elif decision.action == "decline":
        pending.status = st.DECLINED
        _commit(settings, state, pending, watermark, reply_text=text)
        client.send_chat_message(pending.chat_id, to_html(DECLINED_TEXT))
    else:
        _commit(settings, state, pending, watermark, reply_text=text)
        client.send_chat_message(pending.chat_id, to_html(UNCLEAR_TEXT))


def _poll_delay(settings: Settings, outcome: PollOutcome | None, now: datetime) -> float:
    """Odstęp do następnego odpytania: błąd → bazowy (spróbuj wkrótce); brak otwartych → limit;
    otwarte → adaptacyjny backoff od ostatniej aktywności (cisza wydłuża, odpowiedź skraca)."""
    if outcome is None:
        return float(settings.poll_interval_s)  # transientny błąd — ponów wkrótce
    if outcome.open_count == 0:
        return float(settings.poll_max_interval_s)  # nic otwartego → rzadkie sprawdzanie
    return next_poll_delay(
        now=now,
        last_activity=outcome.last_activity or now,
        base_s=float(settings.poll_interval_s),
        max_s=float(settings.poll_max_interval_s),
    )


_RUN_RETRY_ATTEMPTS = 3
_RUN_RETRY_BACKOFF_S = 30


def _run_once_with_retry(
    settings: Settings,
    client: GraphClient,
    *,
    now: datetime,
    attempts: int = _RUN_RETRY_ATTEMPTS,
    backoff_s: int = _RUN_RETRY_BACKOFF_S,
    sleep: Callable[[float], None] = time.sleep,
    zegar: Callable[[], datetime] | None = None,
) -> None:
    """Uruchom ``run_once``, ponawiając transientne błędy z narastającym backoffem, zanim odpuścisz.

    Utrata tokenu (``AuthExpiredError``) i brak uprawnień (``GraphPermissionError``) nie są
    transientne — propagują od razu, bo kolejna próba nie naprawi cofniętej zgody ani utraconej
    roli. Idempotencja ``run_once`` (pomija już-wysłane w tym tygodniu) sprawia, że ponowienie
    nie dubluje powiadomień.

    ``StateWriteError`` też propaguje od razu — i to jest tu najważniejszy warunek. Idempotencja
    ``run_once`` stoi WYŁĄCZNIE na pliku stanu, więc awaria jego zapisu jest awarią samej ochrony
    przed duplikatem: ponowienie nie „spróbuje jeszcze raz", tylko wyśle prośbę tej samej osobie
    po raz kolejny (przy ENOSPC odtworzono trzy wiadomości w jednym wywołaniu, a przez okno łaski
    kilkadziesiąt). Awaria utrwalania musi zatrzymać wysyłkę, nie mnożyć ją przez liczbę prób.
    """
    for attempt in range(1, attempts + 1):
        # Puls PRZED każdą próbą: przebieg z ponowieniami i dławieniem Graph potrafi trwać minuty,
        # a bez odświeżenia healthcheck zgłosiłby „niezdrowy" dla usługi, która właśnie pracuje.
        _touch_heartbeat(settings)
        try:
            run_once(settings, client, now=now, zegar=zegar)
            return
        except (
            AuthExpiredError,
            GraphPermissionError,
            st.StateWriteError,
            OknoWysylkiZamknieteError,
        ):
            raise
        except Exception:
            if attempt >= attempts:
                raise
            wait = backoff_s * attempt
            logger.warning(
                "Przebieg powiadomień nieudany (próba %d/%d) — ponawiam za %ds",
                attempt,
                attempts,
                wait,
            )
            sleep(wait)


def _kolejny_termin(settings: Settings, now: datetime) -> datetime:
    """Najbliższy zaplanowany termin przebiegu (opakowanie na ``next_run`` z ustawieniami)."""
    return next_run(
        now,
        tz=settings.tz,
        weekday=settings.run_weekday,
        hour=settings.run_hour,
        minute=settings.run_minute,
    )


_PULS_PONOWIENIE_S = 900  # odstęp między próbami po nieudanym pulsie (15 min)
_PULS_PROG_ALERTU = 3  # po tylu nieudanych próbach z rzędu powiadamiamy operatora


@dataclass
class StanPulsu:
    """Kiedy wypada następna próba pulsu i ile z rzędu się nie powiodło.

    Semantyka „kiedy następna", a nie „kiedy ostatnia udana", jest tu celowa: po nieudanej próbie
    musimy odsunąć kolejną o własny odstęp, niezależny od tempa pobudek pętli. Pobudki potrafią
    następować co 10 s (otwarta rozmowa), więc przy znaczniku „ostatnia udana" trwała awaria sieci
    dawała próbę i alert PRZY KAŻDEJ POBUDCE — do kilkuset alertów na godzinę, co zatyka jedyny
    kanał niezależny od AAD dokładnie wtedy, gdy jest najbardziej potrzebny.
    """

    nastepny: datetime
    nieudane: int = 0


def _puls_sesji(settings: Settings, client: GraphClient, stan: StanPulsu) -> StanPulsu:
    """Sprawdź ważność sesji nie częściej niż co ``heartbeat_interval_h``; zwróć nowy stan pulsu.

    Bez pulsu utrata sesji w poniedziałek wychodzi dopiero w piątek o 16:00 — czyli w chwili, gdy
    przebieg miał się odbyć i nikt nie zdąży już zareagować. Puls kosztuje jedno ciche odświeżenie
    MSAL na dobę i NIE odpytuje Graph.

    Alert idzie DOKŁADNIE RAZ, po ``_PULS_PROG_ALERTU`` nieudanych próbach z rzędu — pojedyncze
    mrugnięcie sieci nie zasługuje na alarm, a trwała awaria nie ma prawa go powtarzać. Powrót
    sprawności też jest zgłaszany, żeby operator wiedział, że nie musi już nic robić.
    Utrata sesji propaguje wyżej: to nie jest błąd przejściowy.
    """
    teraz = datetime.now(_UTC)
    if teraz < stan.nastepny:
        return stan
    try:
        client.refresh_auth()
    except AuthExpiredError:
        raise
    except Exception as blad:
        nieudane = stan.nieudane + 1
        logger.warning("Puls sesji nie powiódł się (%d. raz z rzędu): %s", nieudane, blad)
        if nieudane == _PULS_PROG_ALERTU:
            _alert(
                settings,
                "Puls sesji nie powiódł się",
                f"{nieudane} nieudane próby z rzędu. Ostatni błąd: {_tresc_publiczna(blad)}",
            )
        return StanPulsu(teraz + timedelta(seconds=_PULS_PONOWIENIE_S), nieudane)
    if stan.nieudane >= _PULS_PROG_ALERTU:
        _alert(settings, "Puls sesji wrócił", "Uwierzytelnienie znów działa.", waga=alerts.INFO)
    logger.info("Puls sesji: uwierzytelnienie nadal ważne.")
    return StanPulsu(teraz + timedelta(hours=settings.heartbeat_interval_h), 0)


def _catchup_due(settings: Settings, now: datetime) -> datetime | None:
    """Czas MINIONEGO terminu, jeśli minął niedawno (w oknie łaski) — inaczej ``None``.

    Sygnał do nadrobienia zaległego przebiegu (np. po awarii/restarcie tuż po terminie), zamiast
    czekania cały tydzień. NIE sprawdzamy tu, czy przebieg „już był" — nadrobienie polega na
    IDEMPOTENCJI ``run_once`` (pomija osoby z otwartym pendingiem na ten tydzień), więc powtórka
    jest bezpieczna. Zwracamy czas TERMINU (nie „teraz"), by ``run_once`` liczył tydzień docelowy od
    terminu — restart po północy nie przesunie tygodnia o 7 dni. ``catchup_grace_hours=0`` wyłącza.
    """
    if settings.catchup_grace_hours <= 0:
        return None
    prev = previous_run(
        now,
        tz=settings.tz,
        weekday=settings.run_weekday,
        hour=settings.run_hour,
        minute=settings.run_minute,
    )
    if timedelta(0) <= now - prev <= timedelta(hours=settings.catchup_grace_hours):
        return prev
    return None


def _alert(settings: Settings, tytul: str, tresc: str, *, waga: str = alerts.BLAD) -> None:
    """Wyślij alert kanałem niezależnym od Graph (best-effort — patrz ``alerts.send_alert``)."""
    alerts.send_alert(settings.alert_webhook_url, tytul, tresc, waga=waga)


_PULS_CO_S = 60.0  # jak często odświeżamy plik pulsu w trakcie czekania
_PONOWIENIE_PRZEBIEGU_S = 1800  # po nieudanym przebiegu wróć po 30 min, o ile trwa okno łaski


def _spij_z_pulsem(settings: Settings, sekundy: float, sleep: Callable[[float], None]) -> None:
    """Śpij podaną liczbę sekund, odświeżając puls co ``_PULS_CO_S``.

    Wiek pliku pulsu ma mówić „czy proces żyje", a nie „jak często odpytujemy Graph". Bez cięcia
    snu na kawałki puls bił co najwyżej raz na godzinę (sufit nasłuchu), więc próg healthchecku
    musiałby wynosić 2 h — czyli stojąca pętla byłaby wykrywana dopiero po dwóch godzinach.

    Ta sama funkcja jest wstrzykiwana jako ``sleep`` klienta Graph, więc puls bije TAKŻE w trakcie
    czekania na dławienie. Zostawiamy to świadomie: czekanie zgodne z ``Retry-After`` jest pracą,
    nie zawisem, a proces w tym stanie odpowie na SIGTERM i wznowi obieg. Zastrzeżenie z przeglądu
    („healthcheck orzeka zdrowy dla procesu, który od godzin nic nie robi") celowało jednak
    w rzeczywisty problem: budżet dławienia liczony PER ŻĄDANIE pozwalał jednemu odczytowi
    kolekcji czekać 50 × 900 s. Naprawa jest po stronie klienta (jeden deadline na całą operację,
    ``graph.client._MAX_RETRY_BUDGET_S``), a nie przez wyciszenie pulsu: najdłuższa możliwa cisza
    zeszła do 900 s, czyli progu ``health_max_age_s``. Puls, który przestałby bić w czasie
    dławienia, dawałby „unhealthy" dla usługi działającej zgodnie z projektem — a Docker i tak nie
    restartuje kontenerów „unhealthy", więc jedynym skutkiem byłby fałszywy alarm.
    """
    pozostalo = sekundy
    while pozostalo > 0:
        _touch_heartbeat(settings)
        krok = min(pozostalo, _PULS_CO_S)
        sleep(krok)
        pozostalo -= krok
    _touch_heartbeat(settings)


def _touch_heartbeat(settings: Settings) -> None:
    """Odśwież znacznik czasu pliku pulsu — źródło prawdy dla HEALTHCHECK obrazu.

    Wołane przy KAŻDEJ pobudce pętli, więc wiek pliku odpowiada temu, jak dawno usługa naprawdę
    coś robiła. Bez tego `docker compose ps` pokazuje „Up" także dla procesu, który stoi.
    """
    try:
        sciezka = settings.heartbeat_path
        sciezka.parent.mkdir(parents=True, exist_ok=True)
        sciezka.touch()
    except OSError:
        # Puls jest diagnostyką, nie funkcją — jego awaria nie może zatrzymać powiadomień.
        logger.warning("Nie udało się odświeżyć pliku pulsu", exc_info=True)


def _send_summary(settings: Settings, client: GraphClient, nastepny_przebieg: datetime) -> None:
    """Wyślij administratorowi podsumowanie stanu po przebiegu (sygnał życia usługi).

    W trybie próbnym TYLKO loguje. Podsumowanie idzie na Teams do konkretnego człowieka, więc
    podlega tej samej obietnicy co powiadomienia dla pracowników: „nic nie zostanie wysłane".
    Alerty webhookiem to inna kategoria i celowo działają także w dry-run — dotyczą stanu samej
    usługi, lecą na endpoint należący do operatora i muszą dać się przetestować przed startem.
    """
    if not settings.admin_user_id:
        return
    try:
        statusy = Counter(p.status for p in st.load_state(settings.state_path).values())
        tresc = build_summary_text(
            oczekuje=statusy[st.AWAITING_REPLY],
            do_potwierdzenia=statusy[st.AWAITING_CONFIRM],
            zapisane=statusy[st.APPLIED],
            odmowy=statusy[st.DECLINED],
            wygasle=statusy[st.EXPIRED],
            samodzielne=statusy[st.SELF_FILLED],
            nastepny_przebieg=nastepny_przebieg.astimezone(settings.tz).strftime("%Y-%m-%d %H:%M"),
        )
        if settings.dry_run:
            logger.info("[dry-run] podsumowanie do administratora:\n%s", tresc)
            return
        chat_id = client.create_or_get_chat(client.get_me(), settings.admin_user_id)
        client.send_chat_message(chat_id, to_html(tresc))
    except Exception:
        # Podsumowanie to raport, nie praca — jego awaria nie może przewrócić usługi.
        logger.exception("Nie udało się wysłać podsumowania do administratora")


def _tresc_publiczna(blad: Exception) -> str:
    """Treść błędu nadająca się na kanał ZEWNĘTRZNY (webhook alertów).

    Webhook bywa poza organizacją — Power Automate, Slack, dowolny endpoint operatora — więc nie
    wolno mu podawać danych osobowych. ``AmbiguousAccountError`` niesie w pełnym komunikacie
    ADRESY E-MAIL kont z cache tokenu; na webhook idzie sama liczba kont, a adresy zostają w logu
    usługi (kanał wewnętrzny). Wyjątki bez własnej wersji publicznej lecą bez zmian.
    """
    publiczny = getattr(blad, "publiczny", "")
    return str(publiczny) if publiczny else str(blad)


def _handle_auth_loss(
    settings: Settings,
    blad: Exception,
    sleep: Callable[[float], None],
    *,
    zwloka: bool = True,
) -> None:
    """Zgłoś utratę sesji i odczekaj, zanim proces się zakończy.

    Alert idzie webhookiem, NIE przez Teams: wiadomość na Teams wymaga tego samego tokenu, który
    właśnie przestał działać. Opóźnienie przed wyjściem jest konieczne, bo `restart: unless-stopped`
    podniósłby proces natychmiast — martwy token zamieniłby się w restart co sekundę zamiast
    w spokojne czekanie na `--login`, po którym usługa wraca sama.

    ``zwloka=False`` dla poleceń jednorazowych: `--once`/`--poll-once` uruchamia CZŁOWIEK przy
    wdrożeniu i czeka na wynik w terminalu. Nie ma tam pętli restartów, którą trzeba wyhamować,
    więc dziesięciominutowa cisza (`auth_failure_exit_delay_s`) była tylko zawieszonym terminalem —
    operator widział „nic się nie dzieje" zamiast komunikatu, który już padł w logu.
    """
    logger.critical(
        "Utracono uwierzytelnienie — zatrzymuję usługę. Zaloguj się: `--login`. (%s)", blad
    )
    _alert(settings, "Utracono uwierzytelnienie", _tresc_publiczna(blad), waga=alerts.KRYTYCZNY)
    if zwloka and settings.auth_failure_exit_delay_s > 0:
        logger.info(
            "Czekam %ds przed wyjściem (ogranicza pętlę restartów).",
            settings.auth_failure_exit_delay_s,
        )
        sleep(float(settings.auth_failure_exit_delay_s))


def _safe_run_once(
    settings: Settings,
    client: GraphClient,
    now: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
    zegar: Callable[[], datetime] | None = None,
) -> bool:
    """Przebieg z ponowieniem; zwraca czy się POWIÓDŁ. Utrata tokenu zatrzymuje usługę.

    Wynik jest istotny dla orkiestracji: nieudanego przebiegu nie wolno odhaczyć jako obsłużonego,
    bo wtedy okno łaski nie dałoby drugiej szansy i tydzień przepadłby po jednym dławieniu Graph.
    """
    try:
        # `sleep` MUSI iść dalej: to pętla ponowień faktycznie usypia (30 s + 60 s), więc bez
        # przekazania parametru wstrzyknięcie atrapy nic nie daje i testy śpią naprawdę.
        _run_once_with_retry(settings, client, now=now, sleep=sleep, zegar=zegar)
        return True
    except OknoWysylkiZamknieteError:
        raise  # nie awaria: godziny ciszy przerwały przebieg — decyduje o tym wołający
    except AuthExpiredError as blad:
        _handle_auth_loss(settings, blad, sleep)
        raise
    except Exception as blad:
        logger.exception("Przebieg powiadomień nie powiódł się mimo ponowień")
        _alert(
            settings,
            "Przebieg powiadomień nie powiódł się",
            f"Mimo ponowień: {blad}. Nikt nie dostał prośby w tym tygodniu.",
        )
        return False


@dataclass(frozen=True)
class ZalegloscPrzebiegu:
    """Zaległy przebieg czekający na wykonanie: KTÓRY termin, KIEDY próbować, DO KIEDY próbować.

    Trzy pola, bo mierzą trzy różne rzeczy i wcześniej ich zlanie kosztowało tydzień:

    - ``termin`` — odniesienie TYGODNIA docelowego, nigdy „teraz" (odłożenie przez weekend nie może
      przesunąć planowanego tygodnia o siedem dni).
    - ``nastepna_proba`` — najwcześniejsza chwila kolejnej próby. Osobne pole, bo przy otwartym
      oknie ``_najblizsze_okno`` zwraca „teraz", więc bez niego nieudany przebieg ruszałby
      natychmiast, bez oddechu, w pętli.
    - ``wygasa`` — kiedy odpuszczamy (budżet okna łaski). ``None`` znaczy „odliczanie ZAWIESZONE",
      bo trwają godziny ciszy: cisza nie może zużywać budżetu prób. Wcześniej łaska liczona od
      PIERWOTNEGO terminu wygasała w środku nocy, więc przebieg odłożony z piątku 19:00 dostawał
      w poniedziałek trzy próby zamiast dwunastu — ścieżka odłożona, wprowadzona po to, żeby
      tydzień nie przepadał, była SŁABIEJ chroniona niż zwykła.
    """

    termin: datetime
    nastepna_proba: datetime
    wygasa: datetime | None


class OknoWysylkiZamknieteError(RuntimeError):
    """Okno wysyłki zamknęło się W TRAKCIE przebiegu — reszta próśb czeka na kolejne otwarcie.

    Przebieg z dławieniem Graph potrafi trwać kilkadziesiąt minut, więc sprawdzenie okna raz, na
    starcie, wypuszczało wiadomości długo po zamknięciu — dokładnie tego, przed czym godziny ciszy
    mają chronić. Przerwanie jest bezpieczne dzięki idempotencji ``run_once``: osoby już zagadnięte
    mają pending na ten tydzień i zostaną pominięte, reszta dostanie prośbę przy najbliższym
    otwarciu okna. NIE jest to błąd — orkiestracja tłumaczy go na ``WynikPrzebiegu.ODLOZONY``.
    """


class WynikPrzebiegu(Enum):
    """Co stało się z przebiegiem powiadomień — trzy stany, nie dwa.

    ``ODLOZONY`` musi być odróżnialny od ``NIEUDANY``, bo naprawy są przeciwstawne: nieudany
    przebieg ponawiamy szybko, w granicach okna łaski (awaria bywa chwilowa), a odłożony CZEKA
    na otwarcie okna wysyłki — czasem dłużej niż okno łaski, przez cały weekend. Zlanie obu
    w jedno ``False`` sprawiało, że okno łaski (6 h od piątku 16:00) kończyło się o 22:00,
    a okno wysyłki otwierało dopiero w poniedziałek — tydzień przepadał po cichu.
    """

    UDANY = "udany"
    NIEUDANY = "nieudany"
    ODLOZONY = "odlozony"


def _przebieg_i_podsumowanie(
    settings: Settings,
    client: GraphClient,
    now: datetime,
    sleep: Callable[[float], None],
    *,
    teraz: datetime | None = None,
    zegar: Callable[[], datetime] | None = None,
) -> WynikPrzebiegu:
    """Przebieg RAZEM z podsumowaniem — nierozłącznie. Zwraca, czy przebieg się powiódł.

    Podsumowanie jest „dead man's switchem": brak wiadomości w piątek wieczorem to jedyny sygnał
    awarii w instalacji bez monitoringu. Gdy stało tylko po przebiegu ZAPLANOWANYM, tydzień po
    restarcie hosta wyglądał jak awaria — nadrobienie wysyłało prośby, a administrator nie
    dostawał nic. Związanie obu czynności w jednym miejscu sprawia, że nie da się ich rozdzielić.

    ``now`` to odniesienie TYGODNIA (przy nadrobieniu — miniony termin), a ``teraz`` to bieżąca
    chwila, po której orzekamy o godzinach ciszy. Przy nadrobieniu te dwie wartości są RÓŻNE
    i mylenie ich znaczyłoby sprawdzanie pory doby sprzed kilku godzin.

    ``zegar`` jest ODDZIELNY od ``teraz`` i jedzie dalej, do ``run_once``. Wygląda na duplikat
    tylko dopóki się go nie rozdzieli: ``teraz`` rozstrzyga JEDNO pytanie („czy w tej chwili wolno
    zacząć”), a ``zegar`` jest wołany raz przed KAŻDĄ wysyłką, bo dławienie Graph potrafi
    rozciągnąć przebieg poza okno ciszy. Podanie ``teraz`` jako stałego zegara zdjęłoby tę drugą
    bramkę — sonda przerwanego przebiegu przechodziła wtedy jako UDANY.
    """
    chwila = teraz if teraz is not None else datetime.now(_UTC)
    if not _wolno_inicjowac(settings, chwila):
        # Godziny ciszy: cotygodniowa prośba i podsumowanie to wiadomości INICJOWANE przez bota.
        # ODLOZONY (nie NIEUDANY) mówi pętli, że ma trzymać ten termin do OTWARCIA okna, zamiast
        # oddawać go oknu łaski, które w środku nocy wygaśnie.
        logger.info(
            "Poza oknem wysyłki (%02d:00–%02d:00, dni %s) — przebieg odłożony do %s.",
            settings.send_window_start_hour,
            settings.send_window_end_hour,
            ",".join(str(d) for d in settings.send_window_weekdays),
            _najblizsze_okno(settings, chwila).isoformat(),
        )
        return WynikPrzebiegu.ODLOZONY
    try:
        udany = _safe_run_once(settings, client, now=now, sleep=sleep, zegar=zegar)
    except OknoWysylkiZamknieteError as blad:
        # Okno zamknęło się w trakcie (dławienie Graph). Reszta prośb czeka — to odłożenie,
        # nie awaria, więc podsumowanie też poczeka na dokończony przebieg.
        logger.info("%s", blad)
        return WynikPrzebiegu.ODLOZONY
    _send_summary(settings, client, _kolejny_termin(settings, datetime.now(_UTC)))
    return WynikPrzebiegu.UDANY if udany else WynikPrzebiegu.NIEUDANY


def _zglos_odlozenie(
    settings: Settings,
    zaleglosc: ZalegloscPrzebiegu,
    wynik: WynikPrzebiegu,
    juz_zgloszony: datetime | None,
) -> datetime | None:
    """Powiadom operatora, że przebieg CZEKA na okno wysyłki — raz na termin.

    Odłożenie wstrzymuje też cotygodniowe podsumowanie dla administratora, czyli „dead man's
    switch" tej usługi. Bez tego alertu weekendowa cisza wyglądała identycznie jak awaria: brak
    podsumowania i ani słowa więcej. Kanał webhookowy jest niezależny od Graph, więc dociera także
    wtedy, gdy Teams milczy z naszej decyzji.
    """
    if wynik is not WynikPrzebiegu.ODLOZONY or juz_zgloszony == zaleglosc.termin:
        return juz_zgloszony
    _alert(
        settings,
        "Przebieg powiadomień odłożony do okna wysyłki",
        f"Termin {zaleglosc.termin.isoformat()} wypadł poza godzinami wysyłki. Prośby o grafik "
        f"i podsumowanie wyjdą {zaleglosc.nastepna_proba.isoformat()}. To NIE jest awaria.",
        waga=alerts.INFO,
    )
    return zaleglosc.termin


def _po_probie(
    settings: Settings, zaleglosc: ZalegloscPrzebiegu, wynik: WynikPrzebiegu, now: datetime
) -> ZalegloscPrzebiegu:
    """Kiedy i do kiedy ponawiać zaległy przebieg po próbie, która nie skończyła się sukcesem.

    ``ODLOZONY`` (godziny ciszy) ZAWIESZA odliczanie okna łaski i celuje w otwarcie okna: cisza
    to nie jest zużyta szansa, więc nie może konsumować budżetu prób.

    ``NIEUDANY`` odlicza łaskę normalnie, a gdy była zawieszona — startuje ją OD TERAZ, czyli od
    pierwszej próby, która w ogóle mogła się udać. Dzięki temu przebieg odłożony z piątku dostaje
    w poniedziałek tyle samo prób co przebieg, któremu nic nie stanęło na drodze.

    Kolejna próba nie wcześniej niż ``_PONOWIENIE_PRZEBIEGU_S`` i nie wcześniej niż otwarcie okna:
    przy otwartym oknie ``_najblizsze_okno`` zwraca „teraz", więc sam ten człon dawałby przebieg
    bez oddechu, w pętli.
    """
    okno = _najblizsze_okno(settings, now).astimezone(_UTC)
    if wynik is WynikPrzebiegu.ODLOZONY:
        return ZalegloscPrzebiegu(zaleglosc.termin, okno, None)
    return ZalegloscPrzebiegu(
        termin=zaleglosc.termin,
        nastepna_proba=max(now + timedelta(seconds=_PONOWIENIE_PRZEBIEGU_S), okno),
        wygasa=zaleglosc.wygasa or (now + timedelta(hours=settings.catchup_grace_hours)),
    )


def run_forever(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Pętla: nadrób zaległy przebieg, do terminu obsługuj odpowiedzi, w terminie wyślij nowe."""
    # Termin ROZSTRZYGNIĘTY w tej sesji: wykonany albo świadomie porzucony. Chroni przed
    # odtwarzaniem zaległości, którą już zamknęliśmy (`_catchup_due` widzi ten sam termin co cykl).
    last_run_term: datetime | None = None
    zaleglosc: ZalegloscPrzebiegu | None = None  # zaległy przebieg: co, kiedy i do kiedy próbować
    zgloszone_odlozenie: datetime | None = None  # termin, o którego odłożeniu już powiadomiliśmy
    # Start liczy się jako świeżo potwierdzona sesja (`_ensure_authenticated` właśnie ją sprawdził).
    stan_pulsu = StanPulsu(datetime.now(_UTC) + timedelta(hours=settings.heartbeat_interval_h))
    powitanie_wyslane = False
    zgloszona_awaria_stanu = False
    while True:
        now = datetime.now(_UTC)
        if zaleglosc is not None and zaleglosc.wygasa is not None and now >= zaleglosc.wygasa:
            # Budżet prób wyczerpany. GŁOŚNO, bo to jest ten moment, w którym tydzień przepada —
            # wcześniej kończyło się to samą ciszą w logu i nikt się nie dowiadywał.
            logger.error(
                "Porzucam zaległy przebieg z terminu %s — wyczerpany budżet ponowień (%d h).",
                zaleglosc.termin.isoformat(),
                settings.catchup_grace_hours,
            )
            _alert(
                settings,
                "Zaległy przebieg powiadomień przepadł",
                f"Termin {zaleglosc.termin.isoformat()} nie doszedł do skutku mimo ponowień. "
                f"Nikt nie dostał prośby o grafik na ten tydzień — trzeba to zrobić ręcznie.",
            )
            last_run_term = zaleglosc.termin
            zaleglosc = None
        # Nadrobienie: zaplanowany termin właśnie minął (okno łaski) → wykonaj przebieg
        # (idempotentnie), z czasem TERMINU jako odniesieniem tygodnia (nie „teraz").
        # Pomijamy termin rozstrzygnięty już w tej sesji: po zaplanowanym przebiegu
        # `previous_run(now)` wskazuje ten sam termin, więc bez znacznika byłby zbędny podwójny
        # odczyt z Graph co cykl. Po restarcie znacznik znika — realna zaległość i tak się nadrobi.
        catchup_term = _catchup_due(settings, now)
        if zaleglosc is None and catchup_term is not None and catchup_term != last_run_term:
            zaleglosc = ZalegloscPrzebiegu(
                termin=catchup_term,
                nastepna_proba=now,
                wygasa=catchup_term + timedelta(hours=settings.catchup_grace_hours),
            )
        if zaleglosc is not None and now >= zaleglosc.nastepna_proba:
            logger.info(
                "Nadrabiam zaległy przebieg powiadomień (termin %s).", zaleglosc.termin.isoformat()
            )
            wynik = _przebieg_i_podsumowanie(settings, client, zaleglosc.termin, sleep)
            # Zegar MUSI być odczytany ponownie: przebieg z ponowieniami i dławieniem Graph
            # (budżet 900 s na operację × 3 próby) trwa czasem dłużej niż `_PONOWIENIE_PRZEBIEGU_S`.
            # Na starym `now` `pobudka` wypadałaby wtedy w PRZESZŁOŚCI, więc pętla nasłuchu nie
            # wykonałaby ani jednego obiegu — bot milczałby przez całe okno łaski, mimo że żyje.
            now = datetime.now(_UTC)
            if wynik is WynikPrzebiegu.UDANY:
                last_run_term = zaleglosc.termin  # odhaczamy WYŁĄCZNIE udany przebieg
                zaleglosc = None
            else:
                zaleglosc = _po_probie(settings, zaleglosc, wynik, now)
                zgloszone_odlozenie = _zglos_odlozenie(
                    settings, zaleglosc, wynik, zgloszone_odlozenie
                )
        termin = _kolejny_termin(settings, now)
        # Pobudka może wypaść WCZEŚNIEJ niż termin: zaległy przebieg czeka na swoją kolejną próbę
        # (ponowienie awarii albo otwarcie okna wysyłki). Bez tego kilkunastominutowe dławienie
        # Graph w piątek o 16:00 kosztowałoby cały tygodniowy cykl.
        pobudka = termin
        if zaleglosc is not None:
            pobudka = min(termin, zaleglosc.nastepna_proba)
        logger.info("Następny przebieg powiadomień: %s", termin.isoformat())
        if not powitanie_wyslane:
            # Potwierdzenie powrotu po reboocie hosta — bez tego restart usługi jest niewidoczny.
            _alert(
                settings,
                "Usługa wystartowała",
                f"Nasłuch aktywny. Najbliższy przebieg: {termin.isoformat()}",
                waga=alerts.INFO,
            )
            powitanie_wyslane = True
        while datetime.now(_UTC) < pobudka:
            _touch_heartbeat(settings)
            outcome: PollOutcome | None = None
            try:
                outcome = poll_replies(settings, client, llm)
            except AuthExpiredError as blad:
                # Utrata tokenu to NIE transientny błąd — zatrzymaj się czysto po zgłoszeniu
                # alertu; `unless-stopped` podniesie usługę, gdy człowiek wykona `--login`.
                _handle_auth_loss(settings, blad, sleep)
                raise
            except st.StateWriteError as blad:
                # Awaria utrwalania NIE jest przejściowa i nie wolno jej ponawiać w tempie
                # `poll_interval_s`: to ten sam mechanizm, który w `run_once` mnożył wiadomości.
                # `PollOutcome(0, None)` zsuwa następną pobudkę na sufit (`poll_max_interval_s`),
                # więc pełny dysk daje jedną próbę na godzinę zamiast sześciu na minutę.
                outcome = PollOutcome(0, None)
                logger.critical("Nie da się utrwalić stanu — wstrzymuję nasłuch: %s", blad)
                if not zgloszona_awaria_stanu:
                    _alert(
                        settings,
                        "Nie da się utrwalić stanu przypomnień",
                        f"{blad}. Nasłuch chodzi w zwolnionym tempie i NIE wysyła nic, dopóki "
                        f"zapis nie wróci — inaczej groziłaby seria powtórzonych wiadomości.",
                        waga=alerts.KRYTYCZNY,
                    )
                    zgloszona_awaria_stanu = True
            except Exception:
                # Błąd listenera nie może zabić pętli.
                logger.exception("Listener odpowiedzi zawiódł")
            else:
                zgloszona_awaria_stanu = False  # zapis wrócił — kolejna awaria znów zaalarmuje
            # Puls w OSOBNYM bloku: gdy Graph jest niedostępny, `poll_replies` rzuca — a wtedy puls
            # w tym samym `try` nie wykonałby się ani razu, czyli przestałby działać dokładnie
            # w awarii, którą ma wykrywać.
            try:
                stan_pulsu = _puls_sesji(settings, client, stan_pulsu)
            except AuthExpiredError as blad:
                _handle_auth_loss(settings, blad, sleep)
                raise
            now_dt = datetime.now(_UTC)
            remaining = (pobudka - now_dt).total_seconds()
            if remaining > 0:
                _spij_z_pulsem(
                    settings, min(remaining, _poll_delay(settings, outcome, now_dt)), sleep
                )
        _touch_heartbeat(settings)
        # Przebieg wykonujemy TYLKO po dojściu do terminu. Wcześniejsza pobudka oznacza ponowienie
        # zaległego przebiegu — obsłuży je `_catchup_due` na górze pętli.
        if datetime.now(_UTC) >= termin:
            wynik = _przebieg_i_podsumowanie(settings, client, datetime.now(_UTC), sleep)
            if wynik is WynikPrzebiegu.UDANY:
                last_run_term = termin  # odhaczamy WYŁĄCZNIE udany przebieg
            else:
                # Zapamiętujemy TERMIN (nie „teraz"), żeby przy późniejszym wykonaniu tydzień
                # docelowy liczył się od terminu — odłożenie przez weekend nie może przesunąć
                # planowanego tygodnia o siedem dni. Ta sama droga dla awarii i dla odłożenia:
                # `_po_probie` nadaje im różne terminy kolejnej próby i różne budżety.
                teraz = datetime.now(_UTC)
                zaleglosc = _po_probie(
                    settings,
                    ZalegloscPrzebiegu(
                        termin, teraz, termin + timedelta(hours=settings.catchup_grace_hours)
                    ),
                    wynik,
                    teraz,
                )
                zgloszone_odlozenie = _zglos_odlozenie(
                    settings, zaleglosc, wynik, zgloszone_odlozenie
                )


_AUTH_CHECK_ATTEMPTS = 3
_AUTH_CHECK_BACKOFF_S = 5


def _ensure_authenticated(
    settings: Settings,
    provider_factory: Callable[[Settings], Callable[[], str]] = build_token_provider,
    *,
    attempts: int = _AUTH_CHECK_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    zwloka_przed_wyjsciem: bool = True,
) -> Callable[[], str]:
    """Sprawdź token na starcie — usługa nie może wejść w pętlę bez ważnego uwierzytelnienia.

    Brak ważnego tokenu: z terminalem → jednorazowe interaktywne logowanie; bez terminala (usługa)
    → instrukcja i wyjście ≠ 0, zamiast blokowania na device-code w środku pętli.

    Błąd INNY niż utrata tokenu (DNS, niedostępny ``login.microsoftonline.com``) jest transientny
    i musi być ponowiony: przy restarcie serwera kontener potrafi wstać, zanim sieć jest gotowa,
    a wyjście ≠ 0 przy `restart: unless-stopped` daje wtedy pętlę restartów zamiast spokojnego
    poczekania na sieć. Pętla ``run_forever`` jest na to odporna; ta ścieżka startowa nie była.

    BUDOWA dostawcy jest wewnątrz pętli ponowień, nie przed nią. ``msal.PublicClientApplication``
    odpytuje tenant (OIDC discovery) JUŻ PRZY KONSTRUKCJI, więc przy braku sieci wyjątek leci
    właśnie stamtąd — poza pętlą wywracał cały start śladem stosu, mimo że ponowienie by pomogło.
    Wyszło to dopiero przy uruchomieniu obrazu; testy wstrzykiwały gotowego dostawcę i nie mogły
    tego zobaczyć.
    """
    utracona: AuthExpiredError | None = None
    for attempt in range(1, attempts + 1):
        try:
            provider = provider_factory(settings)
            provider()
            return provider
        except AuthExpiredError as blad:
            utracona = blad
            break  # nie do naprawienia ponowieniem — niżej instrukcja `--login`
        except Exception as blad:
            if attempt >= attempts:
                logger.critical(
                    "Nie udało się przygotować uwierzytelnienia po %d próbach: %s. Sprawdź "
                    "łączność z login.microsoftonline.com oraz poprawność CLIENT_ID i TENANT_ID.",
                    attempts,
                    blad,
                )
                raise SystemExit(1) from None
            wait = _AUTH_CHECK_BACKOFF_S * attempt
            logger.warning(
                "Nie udało się sprawdzić uwierzytelnienia (próba %d/%d) — ponawiam za %ds",
                attempt,
                attempts,
                wait,
            )
            sleep(wait)

    # Dotarliśmy tu wyłącznie przez `break`, czyli po AuthExpiredError.
    if isinstance(utracona, AmbiguousAccountError):
        # Device-code NIE naprawia dwuznaczności — dołożyłby trzecie konto do cache. Człowiek musi
        # usunąć plik cache, a instrukcja jest już w treści wyjątku.
        logger.critical("%s", utracona)
        if not sys.stdin.isatty():
            _handle_auth_loss(settings, utracona, sleep, zwloka=zwloka_przed_wyjsciem)
        raise SystemExit(1)
    if sys.stdin.isatty():
        logger.info("Brak ważnego tokenu — uruchamiam jednorazowe logowanie device-code.")
        login_interactive(settings)
        return provider_factory(settings)
    # TA SAMA obsługa co przy utracie sesji w pętli: alert + odczekanie przed wyjściem. Wcześniej
    # ta gałąź miała własne `logger.critical` + `SystemExit(1)`, przez co pod `unless-stopped`
    # operator dostawał alert TYLKO w pierwszym cyklu — każdy kolejny restart kończył się tu po
    # cichu, a kontener wirował w tempie backoffu Dockera zamiast co `auth_failure_exit_delay_s`.
    _handle_auth_loss(
        settings,
        utracona or AuthExpiredError("brak ważnego uwierzytelnienia i brak terminala"),
        sleep,
        zwloka=zwloka_przed_wyjsciem,
    )
    raise SystemExit(1)


def _polecenie_jednorazowe(akcja: Callable[[], Any]) -> None:
    """Wykonaj `--once`/`--poll-once`, zamieniając awarię na czytelny komunikat zamiast śladu stosu.

    To są DOKŁADNIE te polecenia, które operator uruchamia podczas wdrożenia (kroki weryfikacyjne
    w `deploy/README-docker.md`). Pętla usługi ma własną obsługę przez `_safe_run_once`, ale
    ścieżka jednorazowa jej nie miała — nieutworzony grafik dawał 20 linii traceback, w których
    trzeba było szukać jednej istotnej. Przyczyna i tak jest już w logu: `_raise_for_status`
    zapisuje treść odpowiedzi Graph na poziomie ERROR.
    """
    try:
        akcja()
    except AuthExpiredError:
        raise  # ma własną, czytelną obsługę wyżej
    except Exception as blad:
        logger.critical("Polecenie nie powiodło się: %s: %s", type(blad).__name__, blad)
        raise SystemExit(1) from None


def _ustawienia_lub_wyjscie() -> Settings:
    """Zbuduj i sprawdź ustawienia; pomyłka operatora → jedno zdanie w logu i kod 2.

    ODCZYT otoczenia jest tu razem z walidacją, bo ``Settings.from_env`` też potrafi rzucić
    ``ConfigError`` — robi to każdy parser wartości (``_int``, ``_int_list`` i, od czasu
    fail-closed, ``_bool``). Gdy stał poza obsługą, literówka w `POWIADOMIENIA_DRY_RUN` dawała
    ślad stosu i kod 1, czyli „źle skonfigurowane" było nieodróżnialne od „padło w trakcie pracy" —
    a pod `restart: unless-stopped` kontener wirował zamiast czekać na poprawkę.
    """
    try:
        settings = Settings.from_env()
        settings.validate()
    except ConfigError as blad:
        # Błąd konfiguracji to pomyłka operatora, nie awaria programu. Ma dać JEDNO czytelne zdanie
        # w logu usługi, a nie ślad stosu, w którym trzeba wyławiać ostatnią linię — na serwerze
        # czyta to człowiek przez `docker compose logs`, często pod presją czasu.
        # Kod 2 odróżnia „źle skonfigurowane" od „padło w trakcie pracy" (1).
        logger.critical("Błąd konfiguracji: %s", blad)
        raise SystemExit(2) from None
    return settings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cotygodniowe przypomnienia o zmianach (Microsoft Shifts)."
    )
    parser.add_argument(
        "--once", action="store_true", help="jeden przebieg powiadomień teraz i wyjście"
    )
    parser.add_argument(
        "--poll-once", action="store_true", help="jedno sprawdzenie odpowiedzi i wyjście"
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="jednorazowe interaktywne logowanie (device-code) i wyjście",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    settings = _ustawienia_lub_wyjscie()

    if args.login:
        login_interactive(settings)
        return

    # Tryb logowany ZAWSZE, obiema gałęziami. Milczenie przy `dry_run=false` znaczyło „nie wiem,
    # czy operator wyłączył bezpiecznik świadomie, czy wpisał literówkę" — a różnica jest widoczna
    # dopiero w skrzynkach całego zespołu. Jedna linia na starcie daje ją w `docker compose logs`.
    if settings.dry_run:
        logger.info("Tryb DRY-RUN — nic nie zostanie wysłane ani zapisane.")
    else:
        logger.warning(
            "Tryb NA ŻYWO (POWIADOMIENIA_DRY_RUN=false) — wiadomości będą wysyłane do pracowników, "
            "a potwierdzone zmiany zapisywane do Shifts."
        )

    # Blokada jednej instancji: dwa procesy piszące ten sam stan obeszłyby idempotencję zapisu do
    # Shifts (np. usługa + ręczne --poll-once). Zwalnia się przy zakończeniu procesu.
    try:
        lock = acquire_single_instance_lock(settings.state_path)
    except AlreadyRunningError as exc:
        logger.critical("%s", exc)
        raise SystemExit(1) from None

    # Polecenia jednorazowe uruchamia człowiek i czeka na wynik — nie ma tu pętli restartów
    # `unless-stopped`, którą trzeba hamować `auth_failure_exit_delay_s`
    # (patrz `_handle_auth_loss`).
    jednorazowe = args.once or args.poll_once

    with lock:
        # Budowa dostawcy i sprawdzenie tokenu RAZEM — obie czynności odpytują sieć, więc obie
        # muszą podlegać tym samym ponowieniom (patrz `_ensure_authenticated`).
        provider = _ensure_authenticated(settings, zwloka_przed_wyjsciem=not jednorazowe)
        llm: LlmClient = AnthropicLlm(settings.anthropic_api_key, model=settings.llm_model)
        with httpx.Client(timeout=30) as http:
            # Czekanie na `Retry-After` (budżet do 900 s na żądanie) jest ŻYCIEM usługi, nie zawisem
            # — ale bez pulsu w środku snu healthcheck orzekłby „pętla stoi" dokładnie wtedy, gdy
            # klient cierpliwie czeka zgodnie z projektem.
            client = GraphClient(
                http, provider, sleep=lambda s: _spij_z_pulsem(settings, s, time.sleep)
            )
            if args.once:
                # Ta sama bramka co w pętli: `--once` wysyła cotygodniowe prośby, czyli wiadomości
                # inicjowane przez bota. Ręczne uruchomienie nie jest powodem, by pisać do zespołu
                # w nocy — a w trybie próbnym bramka i tak przepuszcza (patrz `_wolno_inicjowac`).
                if not _wolno_inicjowac(settings, datetime.now(_UTC)):
                    logger.warning(
                        "Poza oknem wysyłki — nic nie wysłano. Najbliższe okno: %s "
                        "(POWIADOMIENIA_SEND_WINDOW_*).",
                        _najblizsze_okno(settings, datetime.now(_UTC)).isoformat(),
                    )
                    return
                _polecenie_jednorazowe(lambda: run_once(settings, client, now=datetime.now(_UTC)))
            elif args.poll_once:
                _polecenie_jednorazowe(lambda: poll_replies(settings, client, llm))
            else:
                try:
                    run_forever(settings, client, llm)
                except AuthExpiredError:
                    # Alert, log CRITICAL i instrukcja poszły już z `_handle_auth_loss`. Ślad stosu
                    # przykryłby je w `docker compose logs`, a runbook każe operatorowi patrzeć
                    # właśnie tam — zatrzymujemy się tak samo czysto jak przy ConfigError.
                    raise SystemExit(1) from None


if __name__ == "__main__":
    main()
