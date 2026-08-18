"""One-pager „ogarnij mnie na <projekt>" — @wzmianka bota → brief projektu (ADR 0051, F4).

Lustro ``ThreadNoteRouter`` dla wyzwalacza @WZMIANKA + dyrektywa ``ogarnij mnie na <projekt>[ |
pdf]``, ale READ-ONLY: brief tylko czyta (status + notatki), więc BEZ bramki zapisu, BEZ autoryzacji
nadawcy (nie ma czego autoryzować poza tym, co pion i tak współdzieli) i BEZ idempotencji/async
(odczyt jest szybki). Router konsultowany przez respondera OBOK komend i routerów zapisu.

Bezpieczeństwo (ADR 0009 §3 / 0051): ``project`` pochodzi z JAWNEGO argumentu wzmianki (źródło
ZAUFANE), NIGDY z treści wątku — luźne dopasowanie dyrektywy nie przekieruje odczytu na cudzy
projekt. Wyzwalacz wymaga @wzmianki bota (``mentions_bot``) — sama fraza nie uruchamia briefu.

Opcjonalny ``| pdf`` dostarcza ten sam one-pager PLIKIEM w wątku (reuse kanału file-reply, ADR
0026); gdy dostawa PDF jest niedostępna lub zawiedzie, degradujemy do odpowiedzi TEKSTEM (brief
zawsze dociera). Treść statusu/notatek to DANE, nie polecenia (zasada przekrojowa).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.errors import NoteAuthorizationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.core.application.note_read_authz import NoteReadAuthorizer
    from workmate.core.application.project_brief import ProjectBriefService

logger = logging.getLogger(__name__)

# Dyrektywa wyzwalacza (case-insensitive). Składnia: ``@WorkMate ogarnij mnie na <projekt> [|pdf]``.
_DIRECTIVE = "ogarnij mnie na"
_PDF_FLAG = "pdf"
_USAGE = (
    "Aby dostać one-pager projektu, wzmiankuj mnie i podaj klucz projektu:\n"
    "  @WorkMate ogarnij mnie na <projekt>\n"
    "  Dopisz `| pdf`, żeby dostać go plikiem. O projekcie decydujesz Ty (klucz z rejestru), "
    "nie treść wątku."
)


@dataclass(frozen=True)
class BriefContext:
    """Kontekst wywołania one-pagera z respondera.

    ``external_id`` (``team/channel/root``) — cel ewentualnej dostawy PDF w wątku. ``mentions_bot``
    — czy wiadomość @wzmiankuje bota (warunek wyzwalacza). ``sender_id`` (AAD id nadawcy,
    addytywne) niesie TOŻSAMOŚĆ do bramki odczytu bazy wiedzy (ADR 0062): brief serwuje treść
    notatek, więc musi ją mieć, choć sam niczego nie zapisuje.
    """

    external_id: str
    mentions_bot: bool
    sender_id: str = ""


class BriefRouter:
    """Router „ogarnij mnie na": status + notatki → one-pager (tekst albo PDF w wątku).

    ``dispatch`` zwraca tekst odpowiedzi, gdy wiadomość jest wyzwalaczem (wzmianka bota +
    dyrektywa), albo ``None`` (zwykła wiadomość → responder obsłuży ją turą agenta). Nieznany
    projekt degraduje do CZYTELNEGO komunikatu; błąd dostawy PDF degraduje do tekstu.
    """

    def __init__(
        self,
        service: ProjectBriefService,
        *,
        deliver_pdf: Callable[[str, str, str], None] | None = None,
        read_authorizer: NoteReadAuthorizer | None = None,
    ) -> None:
        self._service = service
        # Dostawa PDF (external_id, BAZA nazwy bez rozszerzenia, treść Markdown) → wysyłka plikiem
        # w wątku; ``None`` → tryb PDF niedostępny (file-reply off), ``| pdf`` degraduje do tekstu.
        self._deliver_pdf = deliver_pdf
        # Bramka członkostwa ODCZYTU (ADR 0062); ``None`` → wyłączona (zachowanie sprzed bramki).
        # Brief nie jest „read-only, więc bez autoryzacji": ``ProjectBrief.to_text`` zwraca pięć
        # ostatnich notatek projektu z datami, tytułami i uczestnikami — czyli dokładnie tę treść,
        # której bramka broni w ``search_notes``. Router odpalał się PRZED jakąkolwiek autoryzacją,
        # więc jedna @wzmianka obchodziła całą bramkę.
        self._read_authorizer = read_authorizer

    def dispatch(self, text: str, ctx: BriefContext) -> str | None:
        # Wyzwalacz wymaga @wzmianki bota: bez niej to zwykła wiadomość (bot odpowie normalną turą).
        if not ctx.mentions_bot:
            return None
        parsed = _parse_directive(text)
        if parsed is None:
            return None  # wzmianka bez „ogarnij mnie na" → normalna tura agenta
        # Autoryzacja PO rozpoznaniu dyrektywy, PRZED dotknięciem notatek: zwykła wiadomość ma
        # dalej iść turą agenta (tam bramkę egzekwują narzędzia), a nie dostawać odmowy.
        refusal = self._read_authz_refusal(ctx.sender_id)
        if refusal is not None:
            return refusal
        project, want_pdf = parsed
        if not project:
            return _USAGE  # dyrektywa bez projektu → podpowiedz składnię
        return self._handle(project, want_pdf, ctx)

    def _read_authz_refusal(self, sender_id: str) -> str | None:
        """Odmowa odczytu bazy wiedzy (ADR 0062) albo ``None`` (wolno / bramka wyłączona)."""
        if self._read_authorizer is None:
            return None
        try:
            self._read_authorizer.authorize(sender_id)
        except NoteAuthorizationError as exc:
            return f"Brak uprawnień do odczytu bazy wiedzy: {exc}"
        return None

    def _handle(self, project: str, want_pdf: bool, ctx: BriefContext) -> str:
        brief = self._service.brief(project)
        if brief is None:
            return (
                f"Nie znam projektu '{project}'. Sprawdź klucz w rejestrze "
                "(np. przez /szukaj albo listę projektów)."
            )
        text = brief.to_text()
        if not want_pdf:
            return text
        return self._deliver_or_degrade(project, text, ctx)

    def _deliver_or_degrade(self, project: str, text: str, ctx: BriefContext) -> str:
        """Wyślij one-pager PDF w wątku; brak dostawy lub błąd → zwróć treść TEKSTEM.

        Nic nie połykamy po cichu: awarię dostawy logujemy i mimo to dostarczamy brief tekstem,
        żeby użytkownik nie został z niczym (ADR 0051: „degrades to the inline text answer").
        """
        if self._deliver_pdf is None:
            return f"{text}\n\n_(PDF niedostępny — odpowiedź plikiem wyłączona; podaję treścią.)_"
        try:
            self._deliver_pdf(ctx.external_id, f"brief-{project}", text)
        except Exception:
            logger.exception("Nie udało się wysłać one-pagera PDF (projekt %r)", project)
            return f"{text}\n\n_(Nie udało się wysłać PDF — podaję treścią.)_"
        return f"Wysłałem one-pager projektu '{project}' jako PDF w tym wątku."


def _parse_directive(text: str) -> tuple[str, bool] | None:
    """Rozpoznaj ``ogarnij mnie na <projekt> [| pdf]``; zwróć ``(projekt, chce_pdf)`` albo ``None``.

    ``None`` → brak dyrektywy (zwykła wiadomość). Projekt to PIERWSZY token po frazie (klucze
    rejestru są jednosłowne), przycięty — argument nadawcy, nie interpretacja treści wątku
    (ADR 0009 §3). ``| pdf`` po projekcie zamawia dostawę plikiem; inny/brak flagi → tekst.
    """
    idx = text.lower().find(_DIRECTIVE)
    if idx == -1:
        return None
    rest = text[idx + len(_DIRECTIVE) :]
    head, sep, tail = rest.partition("|")
    want_pdf = sep == "|" and tail.strip().lower() == _PDF_FLAG
    tokens = head.split()
    project = tokens[0] if tokens else ""
    return project, want_pdf
