"""Cotygodniowy przebieg kart czasu (ADR 0035) — od godzin ze źródła do wiadomości na Teams.

Jedna pętla, jedna osoba na obrót: rozwiąż tożsamość → zbuduj zestawienie → strażnik → zapisz
arkusz → wyślij wiadomość. Wszystkie zależności są portami, a zegar jest wstrzykiwany, więc cały
przebieg testujemy w pamięci — bez Graph, bez openpyxl, bez sieci.

Trzy decyzje, które trzymają ten moduł w ryzach:

**Arkusz PRZED wiadomością.** Awaria między jednym a drugim zostawi plik bez powiadomienia
(nikt nie ucierpi, następny przebieg dośle) zamiast wiadomości wskazującej nieistniejący plik
(człowiek szuka czegoś, czego nie ma).

**Izolacja per osoba.** Wyjątek jednej osoby nie może zatrzymać pozostałych — raport jest
strukturalny, nie „poleciało albo nie". Wyjątek: brak dostępu do katalogu wyjściowego jest
awarią CAŁEGO przebiegu, więc padamy raz, zamiast dwadzieścia razy pod rząd na to samo.

**Stan zapisywany po KAŻDEJ osobie.** Awaria w połowie nie może skasować pamięci o tym, że
piętnaście osób już dostało wiadomość — wysłanie jej drugi raz jest gorsze niż niewysłanie.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from workmate.core.domain.timesheet import (
    PersonTimesheet,
    build_timesheet,
    group_by_source_id,
)
from workmate.core.domain.timesheet_message import render_timesheet_message
from workmate.core.domain.timesheet_sheet import project_sheet, sheet_filename
from workmate.core.domain.week import reported_week, week_label

if TYPE_CHECKING:
    from workmate.core.ports.timesheets import HoursSource, IdentityDirectory, SheetWriter

logger = logging.getLogger(__name__)

# Powody pominięcia/niepowodzenia — stałe, nie wolny tekst: raport jest odczytywany przez
# człowieka i przez logi, a stabilne kody da się zliczać między przebiegami.
SKIP_NO_WORK = "no_work"
FAIL_UNKNOWN_PERSON = "unknown_person"
FAIL_BAD_DATA = "bad_data"
FAIL_SHEET = "sheet_write_failed"
FAIL_SEND = "send_failed"
# Wiadomość WYSZŁA, ale nie udało się tego zapamiętać — przy ponowieniu osoba dostanie ją
# drugi raz. Osobny powód, bo wymaga innej reakcji człowieka niż nieudana wysyłka.
FAIL_STATE_WRITE = "state_write_failed"


@dataclass(frozen=True)
class PersonOutcome:
    """Wynik jednej osoby — zawsze jeden z trzech: wysłane, pominięte, nieudane."""

    source_id: str
    reason: str = ""
    detail: str = ""
    file_path: str = ""
    minutes: int = 0


@dataclass
class RunReport:
    """Strukturalny wynik całego przebiegu — nigdy gołe „udało się"."""

    week_label: str = ""
    sent: list[PersonOutcome] = field(default_factory=list)
    skipped: list[PersonOutcome] = field(default_factory=list)
    failed: list[PersonOutcome] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        """Jedna linia do logu operatora — to ona trafia do dziennika systemd."""
        tryb = " (PRÓBNY)" if self.dry_run else ""
        return (
            f"Karty czasu {self.week_label}{tryb}: wysłano {len(self.sent)}, "
            f"pominięto {len(self.skipped)}, błędy {len(self.failed)}"
        )


class WeeklyTimesheetService:
    """Przebieg tygodniowy: godziny → arkusze per osoba → prywatne wiadomości na Teams."""

    def __init__(
        self,
        hours: HoursSource,
        identities: IdentityDirectory,
        sheets: SheetWriter,
        send_html: Callable[[str, str], None],
        *,
        output_dir: str,
        tz: ZoneInfo,
        start_hour: int = 8,
        max_minutes_per_day: int = 0,
        dry_run: bool = True,
        only_source_ids: tuple[str, ...] = (),
        already_done: Callable[[str, str], bool] | None = None,
        mark_done: Callable[[str, str, PersonOutcome], None] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(tz=ZoneInfo("UTC")),
    ) -> None:
        self._hours = hours
        self._identities = identities
        self._sheets = sheets
        # Wysyłka jest wstrzykiwana jako WYWOŁANIE, nie port: adapter Teams jest asynchroniczny,
        # a ten przebieg jest wsadowy i synchroniczny. Mostkowanie tych światów należy do drzwi,
        # nie do rdzenia — dzięki temu testujemy pętlę zwykłą funkcją zapisującą wywołania.
        self._send_html = send_html
        self._output_dir = output_dir.rstrip("/\\")
        self._tz = tz
        self._start_hour = start_hour
        self._max_minutes_per_day = max_minutes_per_day
        self._dry_run = dry_run
        self._only = only_source_ids
        self._already_done = already_done or (lambda week, source_id: False)
        self._mark_done = mark_done or (lambda week, source_id, outcome: None)
        self._now = now

    def run(self) -> RunReport:
        """Wykonaj przebieg za tydzień ZAMKNIĘTY (poprzedni pon.–ndz.); zwróć raport.

        Okno bierze ``reported_week`` i jest to tydzień POPRZEDNI, nie bieżący: okno bieżące
        gubiło godziny z weekendu i z piątkowego popołudnia BEZPOWROTNIE, bo kolejny przebieg
        raportował już swój własny tydzień (ADR 0035 § Consequences).
        """
        week_start, week_end = reported_week(self._now(), self._tz)
        label = week_label(week_start)
        report = RunReport(week_label=label, dry_run=self._dry_run)
        logger.info(
            "Karty czasu %s: okno %s..%s, tryb %s.",
            label,
            week_start.date(),
            week_end.date(),
            "PRÓBNY (nic nie wychodzi)" if self._dry_run else "BOJOWY",
        )
        entries = self._hours.read(week_start.date(), week_end.date())
        for source_id, own in sorted(group_by_source_id(entries).items()):
            if self._only and source_id not in self._only:
                continue
            if self._already_done(label, source_id):
                # Już obsłużona w tym tygodniu — cisza. Powtórna wiadomość jest gorsza niż brak.
                continue
            self._process_one(source_id, own, week_start.date(), week_end.date(), label, report)
        logger.info(report.summary())
        return report

    def _process_one(
        self,
        source_id: str,
        own: list[Any],
        week_start: date,
        week_end: date,
        label: str,
        report: RunReport,
    ) -> None:
        """Obsłuż jedną osobę, izolując jej błędy od reszty przebiegu."""
        person = self._identities.resolve(source_id)
        if person is None:
            # Fail-closed: bez PEWNEJ tożsamości nie powstaje ani plik, ani wiadomość. Zgadywanie
            # (np. po nazwisku) mogłoby wpisać czyjeś godziny na cudze konto Jiry przy imporcie.
            report.failed.append(
                PersonOutcome(
                    source_id=source_id,
                    reason=FAIL_UNKNOWN_PERSON,
                    detail="brak wpisu w mapie tożsamości — uzupełnij konfigurację",
                )
            )
            logger.warning("Nieznana osoba %s — pomijam (fail-closed).", source_id)
            return
        try:
            timesheet = build_timesheet(
                person,
                own,
                week_start=week_start,
                week_end=week_end,
                week_label=label,
                max_minutes_per_day=self._max_minutes_per_day,
            )
        except Exception as exc:
            report.failed.append(
                PersonOutcome(source_id=source_id, reason=FAIL_BAD_DATA, detail=str(exc))
            )
            logger.warning("Dane %s odrzucone: %s", source_id, exc)
            return

        if not timesheet.worked():
            report.skipped.append(PersonOutcome(source_id=source_id, reason=SKIP_NO_WORK))
            return

        outcome = self._deliver(timesheet, label, report)
        if outcome is None or self._dry_run:
            return
        try:
            # Stan po KAŻDEJ osobie: awaria za chwilę nie może cofnąć tego, co już wyszło.
            self._mark_done(label, timesheet.person.source_id, outcome)
        except Exception as exc:
            # Zapis stanu też jest I/O i też potrafi paść (pełny dysk, prawa do pliku). Poza
            # ``try`` wywracał CAŁY przebieg: reszta kolejki nie dostawała nic, a TA osoba
            # miała już wysłaną wiadomość i nie była zapisana jako obsłużona — przy ponowieniu
            # dostałaby ją drugi raz i zaimportowała tydzień dwa razy. Utrata pamięci o jednej
            # osobie jest zła, ale wywrócenie pętli jest gorsze.
            report.failed.append(
                PersonOutcome(source_id=source_id, reason=FAIL_STATE_WRITE, detail=str(exc))
            )
            logger.warning("Nie zapisano stanu dla %s: %s", source_id, exc)

    def _deliver(
        self, timesheet: PersonTimesheet, label: str, report: RunReport
    ) -> PersonOutcome | None:
        """Zapisz arkusz, potem wyślij wiadomość. Kolejność jest częścią kontraktu."""
        person = timesheet.person
        path = f"{self._output_dir}/{sheet_filename(timesheet)}"
        try:
            sheet = project_sheet(timesheet, start_hour=self._start_hour, tz=self._tz)
            # Arkusz powstaje TAKŻE w trybie próbnym — to on jest artefaktem do przejrzenia
            # przed uruchomieniem bojowym. Próbny tryb wstrzymuje wiadomość, nie plik.
            self._sheets.write(path, sheet.headers, sheet.rows)
        except Exception as exc:
            report.failed.append(
                PersonOutcome(source_id=person.source_id, reason=FAIL_SHEET, detail=str(exc))
            )
            logger.warning("Arkusz dla %s nieudany: %s", person.source_id, exc)
            return None

        outcome = PersonOutcome(
            source_id=person.source_id, file_path=path, minutes=timesheet.total_minutes
        )
        if self._dry_run:
            report.sent.append(outcome)
            logger.info(
                "PRÓBNY: %s dostałby %s h, arkusz %s.",
                person.source_id,
                timesheet.total_hours,
                path,
            )
            return outcome
        try:
            self._send_html(person.aad_user_id, render_timesheet_message(timesheet, file_path=path))
        except Exception as exc:
            # Plik został — następny przebieg go nadpisze i ponowi wysyłkę (osoba nie jest
            # oznaczona jako obsłużona, bo ``_process_one`` woła ``mark_done`` tylko po sukcesie).
            report.failed.append(
                PersonOutcome(
                    source_id=person.source_id, reason=FAIL_SEND, detail=str(exc), file_path=path
                )
            )
            logger.warning("Wysyłka do %s nieudana: %s", person.source_id, exc)
            return None
        report.sent.append(outcome)
        return outcome
