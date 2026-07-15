"""Punkt składania i obieg powiadomień (Etapy 3–4).

Bezpieczniki:
- ``settings.dry_run`` (domyślnie włączony) — nic nie jest wysyłane ani zapisywane; przebieg
  tylko loguje. Realne działanie wymaga ``POWIADOMIENIA_DRY_RUN=false``.
- Zapis zmian następuje dopiero po jawnym „tak” pracownika (spirit ADR 0006).
"""
from __future__ import annotations

import argparse
import logging
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from powiadomienia_teams import state as st
from powiadomienia_teams.agent.anthropic_llm import AnthropicLlm
from powiadomienia_teams.agent.interpreter import (
    LlmClient,
    build_schedule,
    build_time_offs,
    interpret_reply,
    schedule_to_intervals,
)
from powiadomienia_teams.config import Settings
from powiadomienia_teams.domain.models import Member, TimeOff, WeekSchedule
from powiadomienia_teams.graph.auth import build_token_provider
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.messages import (
    APPLIED_TEXT,
    DECLINED_TEXT,
    UNCLEAR_TEXT,
    WRITE_FAILED_TEXT,
    build_confirm_text,
    build_nudge_text,
    to_html,
)
from powiadomienia_teams.reminders.detect import members_without_shifts
from powiadomienia_teams.reminders.propose import proposal_from_last_week
from powiadomienia_teams.reminders.replies import (
    is_pure_affirmation,
    message_text,
    newest_incoming,
)
from powiadomienia_teams.reminders.timeoff import resolve_time_off
from powiadomienia_teams.scheduler.weekly import next_run

logger = logging.getLogger(__name__)
_UTC = timezone.utc


class CrossUserWriteError(RuntimeError):
    """Próba zapisu zmiany dla innego pracownika niż adresat przypomnienia."""


def ensure_single_owner(
    member_id: str, schedule: WeekSchedule, time_offs: Iterable[TimeOff] = ()
) -> None:
    """Twarda granica: KAŻDY zapis (zmiana i czas wolny) musi należeć do adresata (`member_id`).

    Odpowiedź pracownika steruje wyłącznie dniami/godzinami i wolnym WŁASNEGO grafiku — nigdy
    tożsamością osoby (schemat wyjścia modelu nie ma pola użytkownika). Ten warunek egzekwuje
    ten niezmiennik na granicy nieodwracalnego zapisu, nawet gdyby przyszły refaktor przypadkiem
    przepuścił cudze `user_id`. Nie da się więc czyjąkolwiek odpowiedzią wpisać nic innej osobie.
    """
    foreign = sorted(
        {s.user_id for s in schedule.shifts if s.user_id != member_id}
        | {t.user_id for t in time_offs if t.user_id != member_id}
    )
    if foreign:
        raise CrossUserWriteError(
            f"Zapis odrzucony: wpisy dla {foreign} ≠ adresat {member_id!r}"
        )


def _this_week_monday(now: datetime, tz: tzinfo) -> datetime:
    local = now.astimezone(tz)
    return (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def week_windows(now: datetime, tz: ZoneInfo) -> tuple[datetime, datetime, datetime]:
    """Zwróć (prior_monday, target_monday, target_end) — lokalne północe tz-aware.

    Cel = przyszły tydzień (Pon–Nd). Gotowiec = tydzień bezpośrednio przed celem (ten, który
    właśnie się kończy) = ``[prior_monday, target_monday)``. Poprzednio brano tydzień o jeden
    za wcześnie — stąd pusty gotowiec przy niedzielnym przebiegu.
    """
    monday = _this_week_monday(now, tz)
    target_monday = monday + timedelta(days=7)
    target_end = target_monday + timedelta(days=7)
    return monday, target_monday, target_end


def run_once(settings: Settings, client: GraphClient, *, now: datetime) -> list[Member]:
    """Jeden przebieg powiadomień: wykryj luki, zbuduj propozycje, wyślij (lub loguj w dry-run)."""
    tz = settings.tz
    client.refresh_auth()
    me_id = client.get_me()
    members = client.list_members(settings.team_id)

    prior_monday, target_monday, target_end = week_windows(now, tz)

    next_shifts = client.read_shifts(
        settings.team_id, target_monday.astimezone(_UTC), target_end.astimezone(_UTC)
    )
    missing = list(members_without_shifts(members, next_shifts))
    if settings.only_user_ids:  # tryb pilotażowy — ogranicz do wskazanych osób
        missing = [m for m in missing if m.user_id in settings.only_user_ids]
    prior_shifts = client.read_shifts(
        settings.team_id, prior_monday.astimezone(_UTC), target_monday.astimezone(_UTC)
    )
    week_label = f"{target_monday:%d.%m}–{(target_end - timedelta(days=1)):%d.%m}"

    state = st.load_state(settings.state_path)
    sent = 0
    for member in missing:
        proposal = proposal_from_last_week(
            member.user_id, prior_shifts, target_monday.date(), tz=tz
        )
        text = build_nudge_text(member, proposal, week_label, tz)
        if settings.dry_run:
            logger.info(
                "[dry-run] powiadomienie do %s <%s>:\n%s", member.display_name, member.email, text
            )
            continue
        chat_id = client.create_or_get_chat(me_id, member.user_id)
        client.send_chat_message(chat_id, to_html(text))
        state[member.user_id] = st.PendingReminder(
            member_id=member.user_id,
            member_name=member.display_name,
            chat_id=chat_id,
            week_start=target_monday.date().isoformat(),
            status=st.AWAITING_REPLY,
            proposal=schedule_to_intervals(proposal, tz),
        )
        logger.info("Wysłano powiadomienie do %s", member.display_name)
        sent += 1

    if not settings.dry_run:
        st.save_state(settings.state_path, state)
    logger.info(
        "Przebieg zakończony: %d osób do powiadomienia; %s",
        len(missing),
        "dry-run (nic nie wysłano)" if settings.dry_run else f"wysłano {sent}",
    )
    return missing


def _apply_schedule(
    settings: Settings, client: GraphClient, pending: st.PendingReminder, tz: ZoneInfo
) -> None:
    """Zapisz ustalony grafik i czas wolny do Shifts.

    ``sharedShift``/``sharedTimeOff`` publikują wpis od razu (potwierdzone smoke-testem), więc
    osobny ``share`` jest zbędny; pracownik i tak dostaje potwierdzenie na czacie. Powody czasu
    wolnego są już rozstrzygnięte (``reason_id`` w stanie z etapu potwierdzenia) — tu żadnego
    odczytu z Graph, żeby nie poszerzać okna awarii po ustawieniu APPLIED.
    """
    week_start = date.fromisoformat(pending.week_start)
    schedule = build_schedule(
        pending.member_id, week_start, pending.resolved, tz, settings.scheduling_group_id
    )
    # Powody czasu wolnego rozstrzygnięte już przy potwierdzeniu — tu tylko budujemy wpisy
    # (bez odczytu z Graph w sekcji krytycznej po APPLIED).
    time_offs = build_time_offs(pending.member_id, week_start, pending.resolved_time_off, tz)
    # Nigdy nie zapisz nic cudzą tożsamością (obejmuje zmiany i czas wolny).
    ensure_single_owner(pending.member_id, schedule, time_offs)
    for shift in schedule.shifts:
        client.create_shift(settings.team_id, shift)
    for time_off in time_offs:
        client.create_time_off(settings.team_id, time_off)


def poll_replies(settings: Settings, client: GraphClient, llm: LlmClient) -> None:
    """Przetwórz odpowiedzi pracowników: interpretuj → potwierdź → (po »tak«) zapisz.

    Każdy pending obsługiwany jest w izolacji (błąd jednego nie kładzie pozostałych),
    a stan zapisywany PRZED nieodwracalnym zapisem do Shifts — semantyka „co najwyżej raz":
    pominięty zapis naprawia człowiek, zdublowana zmiana jest nieodwracalna.
    """
    if settings.dry_run:
        logger.info("[dry-run] pomijam listener odpowiedzi")
        return

    state = st.load_state(settings.state_path)
    open_items = [
        p for p in state.values() if p.status in (st.AWAITING_REPLY, st.AWAITING_CONFIRM)
    ]
    if not open_items:
        return

    client.refresh_auth()
    me_id = client.get_me()
    tz = settings.tz
    for pending in open_items:
        try:
            _process_pending(settings, client, llm, pending, me_id, tz, state)
        except Exception:
            # Izolacja per-osoba — błąd jednej odpowiedzi nie blokuje pozostałych.
            logger.exception("Nie udało się obsłużyć odpowiedzi dla %s", pending.member_name)


def _process_pending(
    settings: Settings,
    client: GraphClient,
    llm: LlmClient,
    pending: st.PendingReminder,
    me_id: str,
    tz: ZoneInfo,
    state: dict[str, st.PendingReminder],
) -> None:
    incoming = newest_incoming(client.list_chat_messages(pending.chat_id), me_id, pending.watermark)
    if incoming is None:
        return
    pending.watermark = str(incoming.get("createdDateTime", ""))
    text = message_text(incoming)

    # Czyste „tak" w stanie oczekiwania na potwierdzenie → zapis. „Ok, ale nie będzie mnie w
    # czwartek" / „tak, ale w piątek 10-20" (potwierdzenie + poprawka) trafia do reinterpretacji,
    # żeby nie zapisać starej propozycji mimo prośby o zmianę.
    pure_yes = pending.status == st.AWAITING_CONFIRM and is_pure_affirmation(text)
    if pure_yes:
        pending.status = st.APPLIED
        st.save_state(settings.state_path, state)  # commit PRZED zapisem — brak dubli przy awarii
        try:
            _apply_schedule(settings, client, pending, tz)
            client.send_chat_message(pending.chat_id, to_html(APPLIED_TEXT))
            logger.info("Zapisano grafik dla %s", pending.member_name)
        except CrossUserWriteError:
            # Tripwire bezpieczeństwa — to NIE zwykła awaria sieci: odrzucono próbę zapisu grafiku
            # cudzą tożsamością. Loguj głośno (CRITICAL, ze śladem), osobno od transientnych 5xx.
            logger.critical(
                "NARUSZENIE: odrzucono zapis grafiku cudzą tożsamością dla %s",
                pending.member_name,
                exc_info=True,
            )
            client.send_chat_message(pending.chat_id, to_html(WRITE_FAILED_TEXT))
        except Exception:
            logger.exception("Zapis grafiku dla %s nie powiódł się", pending.member_name)
            client.send_chat_message(pending.chat_id, to_html(WRITE_FAILED_TEXT))
        return

    proposal = build_schedule(
        pending.member_id,
        date.fromisoformat(pending.week_start),
        pending.proposal,
        tz,
        settings.scheduling_group_id,
    )
    decision = interpret_reply(
        proposal, text, tz=tz, group_id=settings.scheduling_group_id, llm=llm
    )
    if decision.action in ("confirm", "modify") and decision.schedule is not None:
        # Rozstrzygnij powody czasu wolnego TERAZ (przed potwierdzeniem), żeby wiadomość obiecała
        # dokładnie to, co zostanie zapisane, i nie zgubić dnia po cichu przy zapisie.
        resolved_time_off: list[dict[str, Any]] = []
        if decision.time_off:
            reasons = client.list_time_off_reasons(settings.team_id)
            resolved_time_off = resolve_time_off(list(decision.time_off), reasons)
            if len(resolved_time_off) < len(decision.time_off):
                logger.warning(
                    "Pominięto część dni wolnych dla %s — brak powodów czasu wolnego w zespole",
                    pending.member_name,
                )
        if decision.schedule.is_empty and not resolved_time_off:
            # Nic konkretnego do zapisania (np. urlop, ale zespół nie ma żadnych powodów czasu
            # wolnego) — nie obiecuj pustego zapisu, poproś o doprecyzowanie.
            st.save_state(settings.state_path, state)
            client.send_chat_message(pending.chat_id, to_html(UNCLEAR_TEXT))
            return
        pending.resolved = schedule_to_intervals(decision.schedule, tz)
        pending.resolved_time_off = resolved_time_off
        pending.status = st.AWAITING_CONFIRM
        st.save_state(settings.state_path, state)
        confirm = build_confirm_text(decision.schedule, resolved_time_off, tz)
        client.send_chat_message(pending.chat_id, to_html(confirm))
    elif decision.action == "decline":
        pending.status = st.DECLINED
        st.save_state(settings.state_path, state)
        client.send_chat_message(pending.chat_id, to_html(DECLINED_TEXT))
    else:
        st.save_state(settings.state_path, state)
        client.send_chat_message(pending.chat_id, to_html(UNCLEAR_TEXT))


def run_forever(settings: Settings, client: GraphClient, llm: LlmClient) -> None:
    """Pętla: do następnego terminu obsługuj odpowiedzi, w terminie wyślij nowe powiadomienia."""
    while True:
        target = next_run(
            datetime.now(_UTC),
            tz=settings.tz,
            weekday=settings.run_weekday,
            hour=settings.run_hour,
            minute=settings.run_minute,
        )
        logger.info("Następny przebieg powiadomień: %s", target.isoformat())
        while datetime.now(_UTC) < target:
            try:
                poll_replies(settings, client, llm)
            except Exception:
                # Błąd listenera nie może zabić pętli.
                logger.exception("Listener odpowiedzi zawiódł")
            remaining = (target - datetime.now(_UTC)).total_seconds()
            if remaining > 0:
                time.sleep(min(remaining, float(settings.poll_interval_s)))
        try:
            run_once(settings, client, now=datetime.now(_UTC))
        except Exception:
            # Błąd jednego przebiegu nie może zabić pętli.
            logger.exception("Przebieg powiadomień nie powiódł się")


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
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass

    settings = Settings.from_env()
    settings.validate()
    if settings.dry_run:
        logger.info("Tryb DRY-RUN — nic nie zostanie wysłane ani zapisane.")

    provider = build_token_provider(settings)
    llm: LlmClient = AnthropicLlm(settings.anthropic_api_key, model=settings.llm_model)
    with httpx.Client(timeout=30) as http:
        client = GraphClient(http, provider)
        if args.once:
            run_once(settings, client, now=datetime.now(_UTC))
        elif args.poll_once:
            poll_replies(settings, client, llm)
        else:
            run_forever(settings, client, llm)


if __name__ == "__main__":
    main()
