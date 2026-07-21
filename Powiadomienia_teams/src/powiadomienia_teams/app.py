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
from powiadomienia_teams.domain.models import Member
from powiadomienia_teams.graph.auth import (
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
    UNCLEAR_TEXT,
    WRITE_FAILED_TEXT,
    build_confirm_text,
    build_nudge_text,
    build_summary_text,
    to_html,
)
from powiadomienia_teams.reminders.detect import members_without_shifts
from powiadomienia_teams.reminders.guards import CrossUserWriteError, ensure_single_owner
from powiadomienia_teams.reminders.lifecycle import is_expired, prune_terminal
from powiadomienia_teams.reminders.propose import proposal_from_last_week
from powiadomienia_teams.reminders.replies import (
    is_pure_affirmation,
    message_text,
    newest_incoming,
)
from powiadomienia_teams.reminders.timeoff import resolve_time_off
from powiadomienia_teams.scheduler.backoff import next_poll_delay
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


def run_once(settings: Settings, client: GraphClient, *, now: datetime) -> list[Member]:
    """Jeden przebieg powiadomień: wykryj luki, zbuduj propozycje, wyślij (lub loguj w dry-run)."""
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
    # Urlop w docelowym tygodniu = grafik uzupełniony (patrz `members_without_shifts`).
    next_time_off = client.read_time_off(
        ctx.team_id, target_monday.astimezone(_UTC), target_end.astimezone(_UTC)
    )
    missing = list(members_without_shifts(members, next_shifts, next_time_off))
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
        proposal = proposal_from_last_week(
            member.user_id, prior_shifts, target_monday.date(), tz=tz
        )
        text = build_nudge_text(member, proposal, week_label, tz)
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
        sent_iso = sent_at or to_graph_iso(datetime.now(_UTC))
        state[member.user_id] = st.PendingReminder(
            member_id=member.user_id,
            member_name=member.display_name,
            chat_id=chat_id,
            week_start=week_start_iso,
            status=st.AWAITING_REPLY,
            watermark=sent_iso,
            nudged_at=sent_iso,  # niezmienny czas nudge'a — baza okna odpowiedzi
            proposal=schedule_to_intervals(proposal, tz),
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


def _apply_schedule(
    client: GraphClient, ctx: TeamContext, pending: st.PendingReminder, tz: ZoneInfo
) -> None:
    """Zapisz ustalony grafik i czas wolny do Shifts (zespół z `ctx`, nie z globalnych ustawień).

    ``sharedShift``/``sharedTimeOff`` publikują wpis od razu (potwierdzone smoke-testem), więc
    osobny ``share`` jest zbędny; pracownik i tak dostaje potwierdzenie na czacie. Powody czasu
    wolnego są już rozstrzygnięte (``reason_id`` w stanie z etapu potwierdzenia) — tu żadnego
    odczytu z Graph, żeby nie poszerzać okna awarii po ustawieniu APPLIED.
    """
    week_start = date.fromisoformat(pending.week_start)
    schedule = build_schedule(
        pending.member_id, week_start, pending.resolved, tz, ctx.scheduling_group_id
    )
    # Powody czasu wolnego rozstrzygnięte już przy potwierdzeniu — tu tylko budujemy wpisy
    # (bez odczytu z Graph w sekcji krytycznej po APPLIED).
    time_offs = build_time_offs(pending.member_id, week_start, pending.resolved_time_off, tz)
    # Nigdy nie zapisz nic cudzą tożsamością (obejmuje zmiany i czas wolny).
    ensure_single_owner(pending.member_id, schedule, time_offs)
    for shift in schedule.shifts:
        client.create_shift(ctx.team_id, shift)
    for time_off in time_offs:
        client.create_time_off(ctx.team_id, time_off)


def poll_replies(
    settings: Settings, client: GraphClient, llm: LlmClient, *, now: datetime | None = None
) -> PollOutcome:
    """Wygaś ciche okna, potem przetwórz odpowiedzi: interpretuj → potwierdź → (po »tak«) zapisz.

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
    open_items = [
        p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
    ]
    if not open_items:
        return PollOutcome(0, None)

    client.refresh_auth()
    me_id = client.get_me()
    tz = settings.tz
    ctx = settings.team_context  # jeden zespół dziś; per-pending kontekst wepnie się tu (ADR 0001)

    # 1. NAJPIERW odczytaj i przetwórz odpowiedzi. Świeża odpowiedź przesuwa watermark, więc krok 2
    #    nie zamknie okna komuś, kto właśnie odpisał — brak wyścigu na krawędzi okna odpowiedzi.
    progressed = False
    for pending in open_items:
        try:
            progressed |= _process_pending(settings, client, llm, ctx, pending, me_id, tz, state)
        except AuthExpiredError:
            raise  # utrata tokenu zatrzymuje usługę — nie myl jej z awarią jednej odpowiedzi
        except Exception:
            # Izolacja per-osoba — błąd jednej odpowiedzi nie blokuje pozostałych.
            logger.exception("Nie udało się obsłużyć odpowiedzi dla %s", pending.member_name)

    # 2. Wygaś te, które PO odczycie wciąż są otwarte i minęło ich okno (bez świeżej aktywności).
    #    Commit EXPIRED PRZED wysyłką domknięcia — semantyka „co najwyżej raz" (jak przy zapisie).
    still_open = [
        p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
    ]
    newly_expired = [p for p in still_open if is_expired(p, now, settings.reply_window_hours)]
    if newly_expired:
        for pending in newly_expired:
            pending.status = st.EXPIRED
        st.save_state(settings.state_path, state)
        if settings.send_expiry_message:
            _notify_expired(client, newly_expired)

    # Ostatnia aktywność liczona z wciąż otwartych (po przetworzeniu): świeża odpowiedź skróci
    # następny odstęp, cisza go wydłuży (patrz ``_poll_delay``/``next_poll_delay``).
    active = [p for p in still_open if p.status != st.EXPIRED]
    return PollOutcome(len(active), now if progressed else _latest_activity(active))


def _notify_expired(client: GraphClient, expired: list[st.PendingReminder]) -> None:
    """Wyślij uprzejme domknięcie osobom z wygasłym oknem (stan EXPIRED już utrwalony).

    Izolacja per-osoba; nieudana wysyłka jest tylko logowana — status jest już terminalny, więc
    ani nie ponowimy zapisu, ani nie zdublujemy wiadomości przy kolejnym przebiegu.
    """
    for pending in expired:
        try:
            client.send_chat_message(pending.chat_id, to_html(EXPIRED_TEXT))
            logger.info("Zamknięto okno odpowiedzi dla %s (brak odpowiedzi)", pending.member_name)
        except Exception:
            logger.exception("Nie udało się wysłać domknięcia do %s", pending.member_name)


def _commit(
    settings: Settings,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    watermark: str,
) -> None:
    """Utrwal stan RAZEM z przesunięciem watermarku — jedyne miejsce, gdzie watermark rośnie.

    Watermark NIE może być przesuwany z góry, przed przetworzeniem odpowiedzi: ``pending`` jest
    tym samym obiektem, który trzyma słownik ``state``, więc ``save_state`` wywołane przy obsłudze
    INNEJ osoby zserializowałoby też zaawansowany watermark tej, której obsługa właśnie padła.
    Jej odpowiedź stałaby się trwale niewidoczna (``newest_incoming`` odsiewa wszystko sprzed
    watermarku), a po 48 h dostałaby nieprawdziwe „nie dostałem odpowiedzi". Wiązanie obu zapisów
    w jednym kroku sprawia, że nieudane przetworzenie zostawia watermark nietknięty i kolejny tick
    zobaczy tę odpowiedź ponownie.
    """
    pending.watermark = watermark
    pending.fail_count = 0  # ta wiadomość obsłużona — licznik prób startuje od zera
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
) -> bool:
    """Dispatcher jednej odpowiedzi: czyste »tak« → zapis; wszystko inne → interpretacja.

    Zwraca ``True``, gdy była nowa wiadomość do obsłużenia — sygnał dla ``_poll_delay``, żeby
    zresetować backoff do odstępu bazowego (rozmowa trwa, nie ma po co czekać do sufitu).
    """
    incoming = newest_incoming(client.list_chat_messages(pending.chat_id), me_id, pending.watermark)
    if incoming is None:
        return False
    watermark = str(incoming.get("createdDateTime", ""))
    text = message_text(incoming)

    # Czyste „tak" w stanie oczekiwania na potwierdzenie → zapis. „Ok, ale nie będzie mnie w
    # czwartek" / „tak, ale w piątek 10-20" (potwierdzenie + poprawka) trafia do reinterpretacji,
    # żeby nie zapisać starej propozycji mimo prośby o zmianę.
    try:
        if pending.status == st.AWAITING_CONFIRM and is_pure_affirmation(text):
            _apply_confirmed_yes(settings, client, ctx, pending, tz, state, watermark)
        else:
            _interpret_and_confirm(settings, client, llm, ctx, pending, text, tz, state, watermark)
    except AuthExpiredError:
        raise  # utrata tokenu dotyczy całej usługi, nie tej jednej wiadomości
    except Exception:
        _record_failure(settings, client, state, pending, watermark)
        raise  # wyżej loguje ślad — tu tylko decydujemy, czy próbować jeszcze raz
    return True


_MAX_PENDING_FAILURES = 3


def _record_failure(
    settings: Settings,
    client: GraphClient,
    state: dict[str, st.PendingReminder],
    pending: st.PendingReminder,
    watermark: str,
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
    _commit(settings, state, pending, watermark)  # przesuwa watermark i zeruje licznik
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
) -> None:
    """Czyste »tak« na etapie potwierdzenia → nieodwracalny zapis (commit stanu PRZED zapisem)."""
    pending.status = st.APPLIED
    _commit(settings, state, pending, watermark)  # commit PRZED zapisem — brak dubli przy awarii
    try:
        _apply_schedule(client, ctx, pending, tz)
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
    except Exception:
        logger.exception("Zapis grafiku dla %s nie powiódł się", pending.member_name)
        client.send_chat_message(pending.chat_id, to_html(WRITE_FAILED_TEXT))
        return
    # Zapis się POWIÓDŁ — potwierdzenie idzie osobno: nieudana wysyłka potwierdzenia to NIE błąd
    # zapisu, więc nie wysyłaj mylnego „uzupełnij ręcznie" (zmiany są już w Shifts).
    try:
        client.send_chat_message(pending.chat_id, to_html(APPLIED_TEXT))
        logger.info("Zapisano grafik dla %s", pending.member_name)
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
) -> None:
    """Interpretuj odpowiedź: confirm/modify → poproś o »tak«; decline/unclear → zamknij."""
    proposal = build_schedule(
        pending.member_id,
        date.fromisoformat(pending.week_start),
        pending.proposal,
        tz,
        ctx.scheduling_group_id,
    )
    decision = interpret_reply(proposal, text, tz=tz, group_id=ctx.scheduling_group_id, llm=llm)
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
            _commit(settings, state, pending, watermark)
            client.send_chat_message(pending.chat_id, to_html(UNCLEAR_TEXT))
            return
        pending.resolved = schedule_to_intervals(decision.schedule, tz)
        pending.resolved_time_off = resolved_time_off
        pending.status = st.AWAITING_CONFIRM
        _commit(settings, state, pending, watermark)
        confirm = build_confirm_text(decision.schedule, resolved_time_off, tz)
        client.send_chat_message(pending.chat_id, to_html(confirm))
    elif decision.action == "decline":
        pending.status = st.DECLINED
        _commit(settings, state, pending, watermark)
        client.send_chat_message(pending.chat_id, to_html(DECLINED_TEXT))
    else:
        _commit(settings, state, pending, watermark)
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
) -> None:
    """Uruchom ``run_once``, ponawiając transientne błędy z narastającym backoffem, zanim odpuścisz.

    Utrata tokenu (``AuthExpiredError``) i brak uprawnień (``GraphPermissionError``) nie są
    transientne — propagują od razu, bo kolejna próba nie naprawi cofniętej zgody ani utraconej
    roli. Idempotencja ``run_once`` (pomija już-wysłane w tym tygodniu) sprawia, że ponowienie
    nie dubluje powiadomień.
    """
    for attempt in range(1, attempts + 1):
        # Puls PRZED każdą próbą: przebieg z ponowieniami i dławieniem Graph potrafi trwać minuty,
        # a bez odświeżenia healthcheck zgłosiłby „niezdrowy" dla usługi, która właśnie pracuje.
        _touch_heartbeat(settings)
        try:
            run_once(settings, client, now=now)
            return
        except (AuthExpiredError, GraphPermissionError):
            raise
        except Exception:
            if attempt >= attempts:
                raise
            wait = backoff_s * attempt
            logger.warning(
                "Przebieg powiadomień nieudany (próba %d/%d) — ponawiam za %ds",
                attempt, attempts, wait,
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
                f"{nieudane} nieudane próby z rzędu. Ostatni błąd: {blad}",
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


def _handle_auth_loss(settings: Settings, blad: Exception, sleep: Callable[[float], None]) -> None:
    """Zgłoś utratę sesji i odczekaj, zanim proces się zakończy.

    Alert idzie webhookiem, NIE przez Teams: wiadomość na Teams wymaga tego samego tokenu, który
    właśnie przestał działać. Opóźnienie przed wyjściem jest konieczne, bo `restart: unless-stopped`
    podniósłby proces natychmiast — martwy token zamieniłby się w restart co sekundę zamiast
    w spokojne czekanie na `--login`, po którym usługa wraca sama.
    """
    logger.critical("Utracono uwierzytelnienie — zatrzymuję usługę. Zaloguj się: `--login`. (%s)",
                    blad)
    _alert(settings, "Utracono uwierzytelnienie", str(blad), waga=alerts.KRYTYCZNY)
    if settings.auth_failure_exit_delay_s > 0:
        logger.info("Czekam %ds przed wyjściem (ogranicza pętlę restartów).",
                    settings.auth_failure_exit_delay_s)
        sleep(float(settings.auth_failure_exit_delay_s))


def _safe_run_once(
    settings: Settings,
    client: GraphClient,
    now: datetime,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Przebieg z ponowieniem; zwraca czy się POWIÓDŁ. Utrata tokenu zatrzymuje usługę.

    Wynik jest istotny dla orkiestracji: nieudanego przebiegu nie wolno odhaczyć jako obsłużonego,
    bo wtedy okno łaski nie dałoby drugiej szansy i tydzień przepadłby po jednym dławieniu Graph.
    """
    try:
        # `sleep` MUSI iść dalej: to pętla ponowień faktycznie usypia (30 s + 60 s), więc bez
        # przekazania parametru wstrzyknięcie atrapy nic nie daje i testy śpią naprawdę.
        _run_once_with_retry(settings, client, now=now, sleep=sleep)
        return True
    except AuthExpiredError as blad:
        _handle_auth_loss(settings, blad, sleep)
        raise
    except Exception as blad:
        logger.exception("Przebieg powiadomień nie powiódł się mimo ponowień")
        _alert(settings, "Przebieg powiadomień nie powiódł się",
               f"Mimo ponowień: {blad}. Nikt nie dostał prośby w tym tygodniu.")
        return False


def _przebieg_i_podsumowanie(
    settings: Settings,
    client: GraphClient,
    now: datetime,
    sleep: Callable[[float], None],
) -> bool:
    """Przebieg RAZEM z podsumowaniem — nierozłącznie. Zwraca, czy przebieg się powiódł.

    Podsumowanie jest „dead man's switchem": brak wiadomości w piątek wieczorem to jedyny sygnał
    awarii w instalacji bez monitoringu. Gdy stało tylko po przebiegu ZAPLANOWANYM, tydzień po
    restarcie hosta wyglądał jak awaria — nadrobienie wysyłało prośby, a administrator nie
    dostawał nic. Związanie obu czynności w jednym miejscu sprawia, że nie da się ich rozdzielić.
    """
    udany = _safe_run_once(settings, client, now=now, sleep=sleep)
    _send_summary(settings, client, _kolejny_termin(settings, datetime.now(_UTC)))
    return udany


def run_forever(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Pętla: nadrób zaległy przebieg, do terminu obsługuj odpowiedzi, w terminie wyślij nowe."""
    last_run_term: datetime | None = None  # termin już obsłużony w TEJ sesji (dedup nadrobień)
    # Start liczy się jako świeżo potwierdzona sesja (`_ensure_authenticated` właśnie ją sprawdził).
    stan_pulsu = StanPulsu(datetime.now(_UTC) + timedelta(hours=settings.heartbeat_interval_h))
    powitanie_wyslane = False
    while True:
        now = datetime.now(_UTC)
        # Nadrobienie: zaplanowany termin właśnie minął (okno łaski) → wykonaj przebieg teraz
        # (idempotentnie), z czasem TERMINU jako odniesieniem tygodnia (nie „teraz").
        # Pomijamy termin obsłużony już w tej sesji: po zaplanowanym przebiegu `previous_run(now)`
        # wskazuje ten sam termin, więc bez znacznika byłby zbędny podwójny odczyt z Graph co cykl.
        # Po restarcie znacznik znika — realna zaległość (awaria po terminie) i tak się nadrobi.
        catchup_term = _catchup_due(settings, now)
        if catchup_term is not None and catchup_term != last_run_term:
            logger.info(
                "Nadrabiam zaległy przebieg powiadomień (okno łaski %dh).",
                settings.catchup_grace_hours,
            )
            if _przebieg_i_podsumowanie(settings, client, catchup_term, sleep):
                last_run_term = catchup_term  # odhaczamy WYŁĄCZNIE udany przebieg
        termin = _kolejny_termin(settings, now)
        # Pobudka może wypaść WCZEŚNIEJ niż termin: gdy zaległy przebieg wciąż czeka w oknie łaski,
        # wracamy tu za `_PONOWIENIE_PRZEBIEGU_S`, żeby dać mu drugą szansę. Bez tego kilkunasto-
        # minutowe dławienie Graph w piątek o 16:00 kosztowałoby cały tygodniowy cykl.
        zalegly = _catchup_due(settings, now)
        pobudka = termin
        if zalegly is not None and zalegly != last_run_term:
            pobudka = min(termin, now + timedelta(seconds=_PONOWIENIE_PRZEBIEGU_S))
        logger.info("Następny przebieg powiadomień: %s", termin.isoformat())
        if not powitanie_wyslane:
            # Potwierdzenie powrotu po reboocie hosta — bez tego restart usługi jest niewidoczny.
            _alert(settings, "Usługa wystartowała",
                   f"Nasłuch aktywny. Najbliższy przebieg: {termin.isoformat()}", waga=alerts.INFO)
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
            except Exception:
                # Błąd listenera nie może zabić pętli.
                logger.exception("Listener odpowiedzi zawiódł")
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
                _spij_z_pulsem(settings, min(remaining, _poll_delay(settings, outcome, now_dt)),
                               sleep)
        _touch_heartbeat(settings)
        # Przebieg wykonujemy TYLKO po dojściu do terminu. Wcześniejsza pobudka oznacza ponowienie
        # zaległego przebiegu — obsłuży je `_catchup_due` na górze pętli.
        if datetime.now(_UTC) >= termin and _przebieg_i_podsumowanie(
            settings, client, datetime.now(_UTC), sleep
        ):
            last_run_term = termin  # odhaczamy WYŁĄCZNIE udany przebieg


_AUTH_CHECK_ATTEMPTS = 3
_AUTH_CHECK_BACKOFF_S = 5


def _ensure_authenticated(
    settings: Settings,
    provider_factory: Callable[[Settings], Callable[[], str]] = build_token_provider,
    *,
    attempts: int = _AUTH_CHECK_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
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
                    attempts, blad,
                )
                raise SystemExit(1) from None
            wait = _AUTH_CHECK_BACKOFF_S * attempt
            logger.warning(
                "Nie udało się sprawdzić uwierzytelnienia (próba %d/%d) — ponawiam za %ds",
                attempt, attempts, wait,
            )
            sleep(wait)

    # Dotarliśmy tu wyłącznie przez `break`, czyli po AuthExpiredError.
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
        "--login", action="store_true",
        help="jednorazowe interaktywne logowanie (device-code) i wyjście",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    settings = Settings.from_env()
    try:
        settings.validate()
    except ConfigError as blad:
        # Błąd konfiguracji to pomyłka operatora, nie awaria programu. Ma dać JEDNO czytelne zdanie
        # w logu usługi, a nie ślad stosu, w którym trzeba wyławiać ostatnią linię — na serwerze
        # czyta to człowiek przez `docker compose logs`, często pod presją czasu.
        # Kod 2 odróżnia „źle skonfigurowane" od „padło w trakcie pracy" (1).
        logger.critical("Błąd konfiguracji: %s", blad)
        raise SystemExit(2) from None

    if args.login:
        login_interactive(settings)
        return

    if settings.dry_run:
        logger.info("Tryb DRY-RUN — nic nie zostanie wysłane ani zapisane.")

    # Blokada jednej instancji: dwa procesy piszące ten sam stan obeszłyby idempotencję zapisu do
    # Shifts (np. usługa + ręczne --poll-once). Zwalnia się przy zakończeniu procesu.
    try:
        lock = acquire_single_instance_lock(settings.state_path)
    except AlreadyRunningError as exc:
        logger.critical("%s", exc)
        raise SystemExit(1) from None

    with lock:
        # Budowa dostawcy i sprawdzenie tokenu RAZEM — obie czynności odpytują sieć, więc obie
        # muszą podlegać tym samym ponowieniom (patrz `_ensure_authenticated`).
        provider = _ensure_authenticated(settings)
        llm: LlmClient = AnthropicLlm(settings.anthropic_api_key, model=settings.llm_model)
        with httpx.Client(timeout=30) as http:
            client = GraphClient(http, provider)
            if args.once:
                _polecenie_jednorazowe(lambda: run_once(settings, client, now=datetime.now(_UTC)))
            elif args.poll_once:
                _polecenie_jednorazowe(lambda: poll_replies(settings, client, llm))
            else:
                run_forever(settings, client, llm)


if __name__ == "__main__":
    main()
