"""Przechwycenie „zapisz to" — notatka z WĄTKU po @wzmiance bota (ADR 0048, F2).

Lustro ``MeetingNoteRouter`` dla innego wyzwalacza: nie komenda ``/notatka`` z argami, lecz
@WZMIANKA bota niosąca dyrektywę ``zapisz to`` z kluczem projektu — po kresce
(``zapisz to | <projekt>``) albo wprost w zdaniu, gdy tekst wzmianki niesie klucz
z rejestru. Świadomie OSOBNY router (nie read-only
``CommandRouter`` — ADR 0017 zostaje read-only), budowany tylko przy włączonej bramce
``enable_thread_note_capture``, konsultowany przez respondera OBOK komend i routera spotkań.

Bezpieczeństwo (ADR 0009 §3 / 0048): ``project`` pochodzi z JAWNEGO argumentu wzmianki (źródło
ZAUFANE), NIGDY z treści wątku — wątek nie przekieruje notatki do cudzego projektu. Miejsce/data z
kontekstu (``source_timestamp`` = Graph timestamp wzmianki, DETERMINISTYCZNY — nie zegar obsługi,
inaczej retry złamałby idempotencję). Zapis idzie przez bramkowany, create-only
``ThreadNoteService`` (ADR 0006). Wyzwalacz wymaga @wzmianki bota (``mentions_bot``) — sama fraza
w treści nie uruchamia zapisu (bot i tak odpowiada na każdą wiadomość człowieka).

Autoryzacja (B2 / ADR 0042): reuse ``MeetingNoteAuthorizer`` — nadawca musi być rozpoznanym
członkiem pionu, rozstrzygane SYNCHRONICZNIE PRZED poborem wątku. Async (B3 / ADR 0043): jak
``/notatka`` — ACK teraz, łańcuch w tle, wynik do wątku; idempotencja (deterministyczny id) czyni
retry bezpiecznym.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import TYPE_CHECKING

from workmate.core.errors import NoteAuthorizationError, WorkMateError

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
    from workmate.core.application.thread_notes import ThreadNoteService
    from workmate.core.ports.repositories import ProjectsRepository

logger = logging.getLogger(__name__)

# Dyrektywa wyzwalacza (case-insensitive). Składnia: ``@WorkMate zapisz to | <projekt>`` albo
# forma naturalna (``zapisz to jako notatkę projektu <klucz>``) — patrz ``_parse_directive``.
_DIRECTIVE = "zapisz to"
# Token klucza projektu: rejestr używa kluczy typu ``workmate`` / ``scada-integration``.
_TOKEN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Ile kluczy wypisać w podpowiedzi, zanim urwiemy wielokropkiem (podpowiedź, nie katalog).
_USAGE_MAX_KEYS = 8


def _usage(klucze: Sequence[str]) -> str:
    """Podpowiedź składni z REALNYMI kluczami rejestru.

    Poprzednia wersja podawała jeden przykład na sztywno („np. scada-integration") i był to ten
    sam defekt co martwa obietnica ``/mnt/user/outputs``: klucza nie było ani w rejestrze, ani
    w notatkach, więc każdy, kto skopiował przykład, dostawał odmowę. Router ma rejestr pod ręką
    — ma z niego czytać, a nie powielać literał, który zgnije przy pierwszej zmianie rejestru.
    """
    if klucze:
        lista = ", ".join(klucze[:_USAGE_MAX_KEYS])
        if len(klucze) > _USAGE_MAX_KEYS:
            lista += ", …"
        projekt = f"Klucze z rejestru: {lista}."
    else:
        projekt = "Rejestr projektów jest pusty — poproś operatora o dodanie projektu."
    return (
        "Aby zapisać ten wątek jako notatkę, wzmiankuj mnie i podaj projekt:\n"
        "  @WorkMate zapisz to | <projekt>\n"
        "  albo wprost: @WorkMate zapisz to jako notatkę projektu <projekt>\n"
        f"  {projekt} O projekcie decydujesz Ty, nie treść wątku."
    )


_ACK = (
    "Przyjąłem — zapisuję ten wątek jako notatkę (streszczam ustalenia). "
    "Wynik odeślę w tym wątku za chwilę."
)
_ASYNC_INTERNAL_ERROR = (
    "Nie udało się zapisać notatki w tle (błąd wewnętrzny). Spróbuj ponownie — "
    "ponowienie jest bezpieczne (notatka nie zostanie zduplikowana)."
)
_BAD_TIMESTAMP = (
    "Nie udało się ustalić daty wątku (brak znacznika czasu wiadomości). Spróbuj ponownie."
)


@dataclass(frozen=True)
class ThreadNoteContext:
    """Kontekst wywołania „zapisz to" z respondera (odpowiednik ``CommandContext`` dla wątku).

    ``external_id`` (``team/channel/root``) — cel poboru wątku i odpowiedzi. ``source_message_id``
    — id wzmianki (klucz idempotencji, ADR 0048 §5). ``source_timestamp`` — Graph ``created``
    wzmianki (deterministyczna data notatki). ``sender_id`` — AAD id nadawcy (autoryzacja B2).
    ``mentions_bot`` — czy wiadomość @wzmiankuje bota (warunek wyzwalacza).
    """

    external_id: str
    source_message_id: str
    source_timestamp: str
    sender_id: str
    mentions_bot: bool


class ThreadNoteRouter:
    """Router „zapisz to": wątek (Graph) → streszczenie → zapis (idempotentny, gated).

    ``dispatch`` zwraca tekst odpowiedzi, gdy wiadomość jest wyzwalaczem „zapisz to" (wzmianka
    bota + dyrektywa), albo ``None`` (to zwykła wiadomość → responder obsłuży ją turą agenta).
    Błędy (nieznany projekt, brak treści, błąd Claude) degradują do CZYTELNEGO komunikatu.
    """

    def __init__(
        self,
        service: ThreadNoteService,
        *,
        projects: ProjectsRepository | None = None,
        authorizer: MeetingNoteAuthorizer | None = None,
        scheduler: Callable[[Callable[[], None]], None] | None = None,
        callback: Callable[[str, str], None] | None = None,
    ) -> None:
        self._service = service
        # Rejestr projektów — WYŁĄCZNIE do podpowiedzi i do rozpoznania klucza w formie
        # naturalnej. O tym, czy projekt istnieje, i tak rozstrzyga ``require_project``
        # w serwisie: ta ścieżka niczego nie autoryzuje. ``None`` (ścieżki operatorskie,
        # testy) → podpowiedź bez kluczy i wyłącznie składnia z kreską.
        self._projects = projects
        # Bramka członkostwa (B2 / ADR 0042); ``None`` → bez autoryzacji (operatorskie ścieżki).
        self._authorizer = authorizer
        # Async (B3 / ADR 0043): ``scheduler`` zleca thunk do tła, ``callback`` odsyła wynik do
        # wątku. Tryb async wymaga OBU — inaczej router liczy inline.
        self._scheduler = scheduler
        self._callback = callback

    def dispatch(self, text: str, ctx: ThreadNoteContext) -> str | None:
        # Wyzwalacz wymaga @wzmianki bota: bez niej to zwykła wiadomość (bot odpowie normalną turą).
        if not ctx.mentions_bot:
            return None
        klucze = self._klucze()
        project = _parse_directive(text, klucze)
        if project is None:
            return None  # wzmianka bez „zapisz to" → normalna tura agenta
        if not project:
            # „zapisz to" bez rozpoznanego projektu → podpowiedz składnię REALNYMI kluczami.
            return _usage(sorted(klucze))
        return self._handle(project, ctx)

    def _klucze(self) -> frozenset[str]:
        """Klucze rejestru; przy braku repozytorium albo błędzie odczytu — pusty zbiór.

        Rejestr jest tu wygodą, nie bramką, więc jego awaria ma degradować podpowiedź, a nie
        wywracać wyzwalacz: ze składnią z kreską „zapisz to" działa dalej.
        """
        if self._projects is None:
            return frozenset()
        try:
            return frozenset(p.key for p in self._projects.all())
        except Exception:
            logger.warning("Nie udało się odczytać rejestru projektów do podpowiedzi 'zapisz to'")
            return frozenset()

    def _handle(self, project: str, ctx: ThreadNoteContext) -> str:
        try:
            on = _iso_date(ctx.source_timestamp)
        except ValueError:
            return _BAD_TIMESTAMP
        # Autoryzacja PRZED poborem wątku (B2 / ADR 0042): nieznany nadawca → odmowa, zero pracy.
        if self._authorizer is not None:
            try:
                self._authorizer.authorize(ctx.sender_id, project=project)
            except NoteAuthorizationError as exc:
                return f"Brak uprawnień do zapisania notatki z wątku: {exc}"
        # Async (B3 / ADR 0043): ACK teraz, łańcuch w tle, wynik do wątku. Autoryzacja już przeszła.
        if self._scheduler is not None and self._callback is not None:
            eid, mid = ctx.external_id, ctx.source_message_id
            self._scheduler(lambda: self._run_and_post(eid, mid, project, on, eid))
            return _ACK
        return self._compose_reply(ctx.external_id, ctx.source_message_id, project, on)

    def _compose_reply(
        self, external_id: str, source_message_id: str, project: str, on: date
    ) -> str:
        """Złóż notatkę i ZWRÓĆ tekst wyniku (zapisana / już była / błąd). Bez I/O drzwi.

        Wspólny rdzeń dla trybu SYNC (zwrot wprost) i ASYNC (zwrot posyłany do wątku). Błędy
        oczekiwane degradują do czytelnego tekstu — wyzwalacz nie wywraca tury ani wątku.
        """
        try:
            outcome = self._service.note_from_thread(
                external_id, source_message_id, project=project, on=on
            )
        except WorkMateError as exc:
            # Nieznany projekt, kolizja/odrzucenie zapisu, błąd Claude API — czytelnie.
            return f"Nie udało się zapisać notatki z wątku: {exc}"
        except (ValueError, KeyError) as exc:
            # Błąd pobrania/parsowania wątku z Graph (zły external_id, brak wątku, 4xx).
            return f"Nie udało się pobrać treści wątku: {exc}"
        note = outcome.note
        if note is None:
            # Notatka tej wzmianki już była (idempotencja, ADR 0048) — pobór/Claude pominięte.
            return (
                "✓ Ten wątek był już zapisany wcześniej (idempotencja, ADR 0048).\n"
                f"  id: {outcome.note_id}"
            )
        meta = note.metadata
        return (
            "✓ Notatka z wątku zapisana ('zapisz to', ADR 0048).\n"
            f"  id:         {note.id}\n"
            f"  tytuł:      {meta.title}\n"
            f"  uczestnicy: {', '.join(meta.participants) or '—'}\n"
            f"  decyzje: {len(meta.decisions)} · action items: {len(meta.action_items)} · "
            f"pytania: {len(meta.open_questions)}"
        )

    def _run_and_post(
        self, external_id: str, source_message_id: str, project: str, on: date, target: str
    ) -> None:
        """Zadanie w tle (B3 / ADR 0043): policz wynik i ODEŚLIJ go do wątku ``target``.

        Nic nie połykamy po cichu: nieoczekiwany błąd łańcucha ląduje w logu i mimo to posyłamy
        userowi czytelny komunikat; błąd samej wysyłki też logujemy. ``self._callback`` jest
        niepuste (sprawdzone w ``_handle``).
        """
        assert self._callback is not None  # tryb async: callback zawsze wstrzyknięty
        try:
            text = self._compose_reply(external_id, source_message_id, project, on)
        except Exception:
            logger.exception("Błąd w tle przy zapisie notatki z wątku (wątek %r)", target)
            text = _ASYNC_INTERNAL_ERROR
        try:
            self._callback(target, text)
        except Exception:
            logger.exception("Nie udało się odesłać wyniku 'zapisz to' do wątku %r", target)


def _parse_directive(text: str, klucze: frozenset[str] = frozenset()) -> str | None:
    """Rozpoznaj dyrektywę „zapisz to"; zwróć projekt, ``""`` lub ``None``.

    ``None`` → brak dyrektywy (zwykła wiadomość). ``""`` → dyrektywa jest, ale projektu nie da
    się ustalić jednoznacznie (podpowiedz składnię). Inaczej → klucz projektu.

    Dwie drogi, ta sama gwarancja. Z kreską: bierzemy wszystko po ``|`` — bez zmian, bo tak
    brzmi ADR 0048 i tak stoi w dokumentacji. Bez kreski: szukamy w tekście wzmianki tokenu,
    który JEST kluczem rejestru; przy zerze albo dwóch trafieniach odmawiamy podpowiedzią,
    bo zgadywanie miejsca zapisu jest gorsze niż pytanie.

    Rozluźnienie NIE osłabia niczego, co chroni ADR 0009 §3. Gwarancją jest tam POCHODZENIE
    klucza — z tekstu wzmianki nadawcy, nigdy z treści wątku — a zdanie „zapisz to jako notatkę
    projektu workmate" też jest tekstem wzmianki. Sztywna kreska chroniła prostotę parsera,
    nie użytkownika: na demo 2026-08-21 człowiek napisał formę naturalną i dostał odmowę.
    """
    idx = text.lower().find(_DIRECTIVE)
    if idx == -1:
        return None
    rest = text[idx + len(_DIRECTIVE) :]
    if "|" in rest:
        return rest.split("|", 1)[1].strip()
    trafienia = {token for token in _TOKEN.findall(rest.lower()) if token in klucze}
    return trafienia.pop() if len(trafienia) == 1 else ""


def _iso_date(timestamp: str) -> date:
    """Data z Graph ``created`` (ISO-8601); pusty/zły → ``ValueError`` (deterministyczna data)."""
    if not timestamp:
        raise ValueError("pusty znacznik czasu wiadomości")
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date()
