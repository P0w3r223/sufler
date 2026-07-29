"""Orkiestracja jednego przebiegu digestu tygodniowego (ADR 0053, F6) — CZYSTA, testowalna.

Wydzielona z pętli/wysyłki, by dało się ją przetestować bez sieci, zegara i asyncio: składa digest
(reuse ``ChangeDigestService``, F5), a wysyłkę i utrwalanie stanu dostaje jako WSTRZYKNIĘTE
callbacki ``send``/``mark``. Idempotencja per (tydzień, odbiorca): odbiorcę z ``already`` pomijamy;
udaną wysyłkę oznaczamy natychmiast (``mark``), więc restart/nadrabianie nie wyśle drugi raz.
Nieudana wysyłka NIE jest oznaczana → kolejny przebieg ponowi. Pusty tydzień nie idzie DM-em (bez
spamu „0 zmian"), ale oznaczamy go jako obsłużony, by detekcja nadrabiania była spójna.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from datetime import date

    from workmate.core.application.change_digest import ChangeDigestService

logger = logging.getLogger(__name__)

# Nagłówek DM-a — treść digestu (z niezaufanych zdarzeń) idzie niżej, już zsanityzowana na wejściu
# i escapowana przez ``send_chat``. Nagłówek jest nasz, stały.
_HEADER = "Cześć! Oto co zmieniło się w pionie przez ostatni tydzień:"


@dataclass(frozen=True)
class DigestRunReport:
    """Wynik przebiegu: co poszło, do kogo, co się nie udało (do logu i testów)."""

    week_label: str
    total_events: int
    sent: tuple[str, ...]
    already: tuple[str, ...]
    failed: tuple[str, ...]
    skipped_empty: bool


def deliver_weekly_digest(
    change_service: ChangeDigestService,
    *,
    since: date,
    week_label: str,
    recipients: Iterable[str],
    already: set[str],
    send: Callable[[str, str], None],
    mark: Callable[[str], None],
) -> DigestRunReport:
    """Złóż digest od ``since`` i wyślij go odbiorcom jeszcze nieobsłużonym w tym tygodniu.

    ``already`` = odbiorcy już obsłużeni (z trwałego stanu). ``send(odbiorca, tekst)`` wysyła DM,
    ``mark(odbiorca)`` utrwala „obsłużony". Awaria pojedynczej wysyłki NIE kładzie przebiegu —
    logujemy i pomijamy oznaczenie (ponowienie w kolejnym przebiegu). Zwraca raport.
    """
    targets = tuple(recipients)
    digest = change_service.since(since)
    if digest.total == 0:
        # Pusty tydzień: bez wysyłki, ale ZAZNACZAMY jako obsłużony (spójna detekcja nadrabiania —
        # tydzień w stanie = nie pominięty). W trybie próbnym ``mark`` i tak nic nie utrwala.
        for recipient in targets:
            if recipient not in already:
                mark(recipient)
        logger.info("Digest %s: brak zmian w oknie — nie wysyłam (bez spamu).", week_label)
        return DigestRunReport(week_label, 0, (), tuple(sorted(already)), (), True)

    text = f"{_HEADER}\n\n{digest.to_text()}"
    sent: list[str] = []
    failed: list[str] = []
    for recipient in targets:
        if recipient in already:
            continue
        try:
            send(recipient, text)
        except Exception:
            # Nie połykamy po cichu: log + brak oznaczenia → kolejny przebieg ponowi tę osobę.
            logger.exception(
                "Digest %s do %s nie wyszedł — ponowię w następnym przebiegu.",
                week_label,
                _redact(recipient),
            )
            failed.append(recipient)
            continue
        mark(recipient)
        sent.append(recipient)
    return DigestRunReport(
        week_label, digest.total, tuple(sent), tuple(sorted(already)), tuple(failed), False
    )


def _redact(user_id: str) -> str:
    """Skróć AAD user id do logu — nie logujemy pełnego identyfikatora osoby."""
    return f"{user_id[:8]}…" if len(user_id) > 8 else user_id
