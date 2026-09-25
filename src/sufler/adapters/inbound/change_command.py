"""Digest „co się zmieniło od <data>" — @wzmianka bota → przegląd zmian (ADR 0052, F5).

Lustro ``BriefRouter`` (F4) dla innego wyzwalacza: @WZMIANKA + dyrektywa ``co się zmieniło od
<data> [| pdf]``. READ-ONLY (fold zdarzeń), więc BEZ bramki zapisu, BEZ autoryzacji nadawcy i BEZ
async — jak one-pager. Router konsultowany przez respondera OBOK komend, routerów zapisu i briefu.

Bezpieczeństwo (ADR 0052): ``<data>`` to JAWNY argument wzmianki (źródło ZAUFANE) parsowany jako
ISO ``YYYY-MM-DD`` — nie interpretacja treści wątku. Wyzwalacz wymaga @wzmianki bota
(``mentions_bot``). Opcjonalny ``| pdf`` dostarcza ten sam digest PLIKIEM (reuse kanału file-reply,
ADR 0026); brak/awaria dostawy → degradacja do TEKSTU. Treść zdarzeń to DANE, nie polecenia.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from sufler.core.errors import NoteAuthorizationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sufler.core.application.change_digest import ChangeDigestService
    from sufler.core.application.note_read_authz import NoteReadAuthorizer

logger = logging.getLogger(__name__)

# Dyrektywa wyzwalacza (case-insensitive). Składnia: ``@Sufler co się zmieniło od <data> [|pdf]``.
_DIRECTIVE = "co się zmieniło od"
_PDF_FLAG = "pdf"
_USAGE = (
    "Aby dostać przegląd zmian, wzmiankuj mnie i podaj datę początkową (ISO):\n"
    "  @Sufler co się zmieniło od 2026-07-01\n"
    "  Dopisz `| pdf`, żeby dostać go plikiem. Datę podajesz Ty (format RRRR-MM-DD)."
)


@dataclass(frozen=True)
class ChangeDigestContext:
    """Kontekst wywołania digestu z respondera.

    ``external_id`` (``team/channel/root``) — cel ewentualnej dostawy PDF w wątku. ``mentions_bot``
    — czy wiadomość @wzmiankuje bota (warunek wyzwalacza). ``sender_id`` (AAD id nadawcy,
    addytywne) niesie TOŻSAMOŚĆ do bramki członkostwa (ADR 0062) — patrz ``ChangeDigestRouter``.
    """

    external_id: str
    mentions_bot: bool
    sender_id: str = ""


class ChangeDigestRouter:
    """Router „co się zmieniło od <data>": fold zdarzeń → przegląd (tekst albo PDF w wątku).

    ``dispatch`` zwraca tekst odpowiedzi dla wyzwalacza (wzmianka bota + dyrektywa) albo ``None``
    (zwykła wiadomość → tura agenta). Zła/brakująca data degraduje do podpowiedzi składni; błąd
    dostawy PDF degraduje do tekstu.
    """

    def __init__(
        self,
        service: ChangeDigestService,
        *,
        deliver_pdf: Callable[[str, str, str], None] | None = None,
        read_authorizer: NoteReadAuthorizer | None = None,
    ) -> None:
        self._service = service
        # Dostawa PDF (external_id, BAZA nazwy bez rozszerzenia, treść Markdown); ``None`` → tryb
        # PDF niedostępny (file-reply off), ``| pdf`` degraduje do tekstu.
        self._deliver_pdf = deliver_pdf
        # Bramka członkostwa ODCZYTU (ADR 0062); ``None`` → wyłączona. Digest składa się ze zdarzeń,
        # nie z notatek — a mimo to jest bramkowany, w odróżnieniu od ``GitHub(action='events')``.
        # Różnica jest w POZIOMIE: `GitHub(events)` to kursorowy odczyt mostu W TURZE, której
        # narzędzia bazy wiedzy i tak odmawiają nierozpoznanemu nadawcy; ta dyrektywa jest
        # ODPOWIEDZIĄ DRZWI składaną przed jakąkolwiek autoryzacją i streszcza aktywność
        # WSZYSTKICH projektów pionu. Decyzja właściciela, zapisana w ADR 0062 (amendment).
        self._read_authorizer = read_authorizer

    def dispatch(self, text: str, ctx: ChangeDigestContext) -> str | None:
        # Wyzwalacz wymaga @wzmianki bota: bez niej to zwykła wiadomość (bot odpowie normalną turą).
        if not ctx.mentions_bot:
            return None
        parsed = _parse_directive(text)
        if parsed is None:
            return None  # wzmianka bez „co się zmieniło od" → normalna tura agenta
        # Autoryzacja PO rozpoznaniu dyrektywy, PRZED foldem zdarzeń — jak w ``BriefRouter``.
        refusal = self._read_authz_refusal(ctx.sender_id)
        if refusal is not None:
            return refusal
        raw_date, want_pdf = parsed
        day = _parse_date(raw_date)
        if day is None:
            return _USAGE  # brak/zła data → podpowiedz składnię
        return self._handle(day, want_pdf, ctx)

    def _read_authz_refusal(self, sender_id: str) -> str | None:
        """Odmowa odczytu (ADR 0062) albo ``None`` (wolno / bramka wyłączona) — lustro briefu."""
        if self._read_authorizer is None:
            return None
        try:
            self._read_authorizer.authorize(sender_id)
        except NoteAuthorizationError as exc:
            return f"Brak uprawnień do odczytu bazy wiedzy: {exc}"
        return None

    def _handle(self, day: date, want_pdf: bool, ctx: ChangeDigestContext) -> str:
        digest = self._service.since(day)
        text = digest.to_text()
        if not want_pdf:
            return text
        return self._deliver_or_degrade(day, text, ctx)

    def _deliver_or_degrade(self, day: date, text: str, ctx: ChangeDigestContext) -> str:
        """Wyślij digest PDF w wątku; brak dostawy lub błąd → zwróć treść TEKSTEM.

        Nic nie połykamy po cichu: awarię dostawy logujemy i mimo to dostarczamy digest tekstem
        (ADR 0052: degradacja do odpowiedzi inline).
        """
        if self._deliver_pdf is None:
            return f"{text}\n\n_(PDF niedostępny — odpowiedź plikiem wyłączona; podaję treścią.)_"
        try:
            self._deliver_pdf(ctx.external_id, f"zmiany-od-{day.isoformat()}", text)
        except Exception:
            logger.exception("Nie udało się wysłać digestu PDF (od %s)", day.isoformat())
            return f"{text}\n\n_(Nie udało się wysłać PDF — podaję treścią.)_"
        return f"Wysłałem przegląd zmian od {day.isoformat()} jako PDF w tym wątku."


def _parse_directive(text: str) -> tuple[str, bool] | None:
    """Z dyrektywy „co się zmieniło od <data> [| pdf]" wyłuskaj ``(data, chce_pdf)`` albo ``None``.

    ``None`` → brak dyrektywy (zwykła wiadomość). Data to PIERWSZY token po frazie (argument
    nadawcy, nie treść wątku). ``| pdf`` po dacie zamawia dostawę plikiem; inny/brak flagi → tekst.
    """
    idx = text.lower().find(_DIRECTIVE)
    if idx == -1:
        return None
    rest = text[idx + len(_DIRECTIVE) :]
    head, sep, tail = rest.partition("|")
    want_pdf = sep == "|" and tail.strip().lower() == _PDF_FLAG
    tokens = head.split()
    return (tokens[0] if tokens else ""), want_pdf


def _parse_date(raw: str) -> date | None:
    """Sparsuj datę ISO ``YYYY-MM-DD``; pusta/zła → ``None`` (router podpowie składnię)."""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None
