"""Komenda ZAPISU ``/notatka`` — produkcyjne M3 z drzwi Teams (ADR 0009 §4 / 0041, B1).

Realizuje odłożoną decyzję Gate-2: notatka ze spotkania składana WPROST z drzwi Teams. Świadomie
NIE jest częścią read-only ``CommandRouter`` (ADR 0017 zostaje read-only) — to OSOBNY router,
budowany tylko przy włączonej bramce zapisu, konsultowany przez respondera obok (nie zamiast)
komend odczytu.

Bezpieczeństwo (ADR 0009 §3): ``meeting_ref``, ``project`` i ``date`` pochodzą z ARGUMENTÓW KOMENDY
wpisanych przez człowieka w kanale (źródło ZAUFANE), NIGDY z treści transkryptu — transkrypt nie
może przekierować notatki do cudzego projektu. Zapis idzie przez bramkowany, DOPISUJĄCY
``NotesWriteService`` (ADR 0006): create-only, nigdy nie nadpisuje. Sam pobór transkryptu i
streszczenie żyją za portami (Graph + Claude), więc router jest testowalny na atrapach.

Autoryzacja (B2 / ADR 0042): gdy wstrzyknięto ``authorizer``, router SYNCHRONICZNIE rozstrzyga, czy
nadawca (``ctx.sender_id`` = AAD id) jest rozpoznanym członkiem pionu — ZANIM ruszy wolny łańcuch.
Nieznany nadawca → czytelna odmowa, żadnego pobrania transkryptu. ``authorizer=None`` (np.
operatorskie CLI, jeden zaufany user) pomija bramkę; drzwi Teams budują router z autoryzacją.

Async (B3 / ADR 0043): gdy wstrzyknięto ``scheduler`` i ``callback``, po autoryzacji router NIE
liczy notatki inline (co blokowałoby poller na czas transkrypt+Claude), lecz zwraca ACK i zleca
łańcuch do tła; wynik trafia do wątku (``callback(external_id, tekst)``). Idempotencja
(deterministyczny id, cz.1) sprawia, że ponowienie po zgubionym zadaniu jest bezpieczne (kolizja).
Bez tych zależności router działa SYNCHRONICZNIE (zachowanie ADR 0041).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import TYPE_CHECKING

from workmate.core.errors import NoteAuthorizationError, WorkMateError

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.adapters.inbound.commands import CommandContext
    from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
    from workmate.core.application.meeting_notes import MeetingNoteService

logger = logging.getLogger(__name__)

_TOKEN = "/notatka"
_USAGE = (
    "Użycie: /notatka <ref-spotkania> | <projekt> | <RRRR-MM-DD>\n"
    "  <ref-spotkania> — link „Dołącz do spotkania” (joinWebUrl) albo id spotkania,\n"
    "  <projekt>       — klucz projektu z rejestru (np. scada-integration),\n"
    "  <RRRR-MM-DD>    — data spotkania (o dacie/projekcie decyduje wywołujący, nie transkrypt)."
)
_ACK = (
    "Przyjąłem — składam notatkę ze spotkania (pobieram transkrypt i streszczam). "
    "Wynik odeślę w tym wątku za chwilę."
)
_ASYNC_INTERNAL_ERROR = (
    "Nie udało się złożyć notatki w tle (błąd wewnętrzny). Spróbuj ponownie — "
    "ponowienie jest bezpieczne (notatka nie zostanie zduplikowana)."
)


class MeetingNoteRouter:
    """Router komendy ZAPISU ``/notatka``: transkrypt (Graph) → streszczenie → zapis (idempotent).

    ``dispatch`` zwraca tekst odpowiedzi, gdy wiadomość jest komendą ``/notatka``, albo ``None``
    (to nie ta komenda → responder obsłuży ją normalną turą agenta). Błędy (nieznany projekt,
    brak transkryptu, błąd Claude) degradują do CZYTELNEGO komunikatu — komenda nie wywraca tury.
    """

    def __init__(
        self,
        service: MeetingNoteService,
        *,
        authorizer: MeetingNoteAuthorizer | None = None,
        scheduler: Callable[[Callable[[], None]], None] | None = None,
        callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._service = service
        # Bramka członkostwa (B2 / ADR 0042); ``None`` → bez autoryzacji (operatorskie CLI).
        self._authorizer = authorizer
        # Async (B3 / ADR 0043): ``scheduler`` zleca thunk do tła, ``callback`` odsyła wynik do
        # wątku (external_id, tekst). Tryb async wymaga OBU — inaczej router liczy inline (0041).
        self._scheduler = scheduler
        self._callback = callback

    def dispatch(self, text: str, ctx: CommandContext) -> str | None:
        stripped = text.strip()
        if not stripped:
            return None
        parts = stripped.split(None, 1)
        token = parts[0].split("@", 1)[0].lower()  # obetnij sufiks @bot (grupy)
        if token != _TOKEN:
            return None  # nie ta komenda → normalna tura
        args = parts[1].strip() if len(parts) > 1 else ""
        return self._handle(args, ctx)

    def _handle(self, args: str, ctx: CommandContext) -> str:
        fields = [p.strip() for p in args.split("|")]
        if len(fields) != 3 or not all(fields):
            return _USAGE
        meeting_ref, project, date_text = fields
        try:
            meeting_date = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError:
            return f"Zła data {date_text!r} — wymagany format RRRR-MM-DD.\n{_USAGE}"
        # Autoryzacja PRZED pobraniem transkryptu (B2 / ADR 0042): nieznany nadawca → odmowa,
        # zero pracy w tle. Format daty sprawdzamy wyżej (tania odmowa nie potrzebuje tożsamości).
        if self._authorizer is not None:
            try:
                self._authorizer.authorize(ctx.sender_id, project=project)
            except NoteAuthorizationError as exc:
                return f"Brak uprawnień do złożenia notatki: {exc}"
        # Async (B3 / ADR 0043): ACK teraz, łańcuch w tle, wynik do wątku. Autoryzacja już przeszła
        # SYNCHRONICZNIE — do tła idzie tylko wolna część (transkrypt+Claude+zapis).
        if self._scheduler is not None and self._callback is not None:
            target = ctx.external_id
            self._scheduler(lambda: self._run_and_post(meeting_ref, project, meeting_date, target))
            return _ACK
        return self._compose_reply(meeting_ref, project, meeting_date)

    def _compose_reply(self, meeting_ref: str, project: str, meeting_date: date) -> str:
        """Złóż notatkę i ZWRÓĆ tekst wyniku (utworzona / już była / błąd). Bez I/O drzwi.

        Wspólny rdzeń dla trybu SYNC (zwrot wprost) i ASYNC (zwrot posyłany do wątku). Błędy
        oczekiwane degradują do czytelnego tekstu — komenda/zadanie nie wywraca tury ani wątku.
        """
        try:
            outcome = self._service.note_from_meeting(
                meeting_ref, project=project, meeting_date=meeting_date
            )
        except WorkMateError as exc:
            # Nieznany projekt w rejestrze, kolizja/odrzucenie zapisu, błąd Claude API — czytelnie.
            return f"Nie udało się złożyć notatki: {exc}"
        except (ValueError, KeyError) as exc:
            # Błąd pobrania/parsowania transkryptu z Graph (brak transkryptu, złe id/URL, 4xx).
            return f"Nie udało się pobrać transkryptu: {exc}"
        note = outcome.note
        if note is None:
            # Notatka tego spotkania już była (idempotencja, ADR 0043) — pobór/Claude pominięte.
            return (
                "✓ Notatka tego spotkania była już złożona wcześniej (idempotencja, ADR 0043).\n"
                f"  id: {outcome.note_id}"
            )
        meta = note.metadata
        return (
            "✓ Notatka ze spotkania zapisana (M3, ADR 0009).\n"
            f"  id:         {note.id}\n"
            f"  tytuł:      {meta.title}\n"
            f"  uczestnicy: {', '.join(meta.participants) or '—'}\n"
            f"  decyzje: {len(meta.decisions)} · action items: {len(meta.action_items)} · "
            f"pytania: {len(meta.open_questions)}"
        )

    def _run_and_post(
        self, meeting_ref: str, project: str, meeting_date: date, target: str
    ) -> None:
        """Zadanie w tle (B3 / ADR 0043): policz wynik i ODEŚLIJ go do wątku ``target``.

        Nic nie połykamy po cichu: nieoczekiwany błąd łańcucha ląduje w logu i mimo to posyłamy
        userowi czytelny komunikat; błąd samej wysyłki też logujemy (wtedy user go nie dostanie,
        ale ślad zostaje). ``self._callback`` jest niepuste (sprawdzone w ``_handle``).
        """
        assert self._callback is not None  # tryb async: callback zawsze wstrzyknięty
        try:
            text = self._compose_reply(meeting_ref, project, meeting_date)
        except Exception:
            logger.exception("Błąd w tle przy składaniu notatki /notatka (wątek %r)", target)
            text = _ASYNC_INTERNAL_ERROR
        try:
            self._callback(target, text)
        except Exception:
            logger.exception("Nie udało się odesłać wyniku /notatka do wątku %r", target)
