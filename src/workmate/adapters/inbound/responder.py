"""Szew między DRZWIAMI (Teams, Telegram, …) a TREŚCIĄ odpowiedzi (Faza 2).

``Responder`` oddziela transport (konkretne drzwi) od tego, co bot odpowiada —
wspólny dla wszystkich drzwi wejściowych, dlatego żyje tu, w `adapters/inbound/`,
a nie w pakiecie pojedynczych drzwi. Dziś dostępny ``EchoResponder`` (spike:
potwierdzenie odbioru); ``RuntimeResponder`` opakowuje runtime agenta rdzenia, a
``SaveNoteResponder`` (stub) — przyszły zapis. Przełączenie to jedna linia w
entry-poincie drzwi (``EchoResponder()`` → ``RuntimeResponder(runtime)``);
handler i wiring drzwi bez zmian.

Moduł jest wolny od importów SDK, więc szew i jego testy działają bez extra
drzwi. Reguła ``core ↛ adapters`` stoi: rdzeń nie zaimportuje tego protokołu —
to drzwi opakują runtime rdzenia w ``Responder`` (strukturalnie, jak atrapy repo
w testach).
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from workmate.core.errors import WorkMateError
from workmate.core.ports.llm import (
    AssistantTurn,
    RawTurn,
    ToolOutput,
    ToolResults,
    UserText,
)

# ``stop_reason`` oznaczający uciętą odpowiedź (ADR 0011) — drzwi dokładają notkę.
_TRUNCATED_STOP = "max_tokens"

# Komendy jawnego startu nowego wątku (ADR 0012 — granica wątku NA ŻĄDANIE). Rozpoznawane
# po PIERWSZYM tokenie (z ukośnikiem), jednakowo na wszystkich drzwiach — logika żyje tu,
# w wspólnym szwie, a nie w kodzie pojedynczych drzwi.
_NEW_THREAD_COMMANDS = frozenset({"/nowa", "/nowy", "/new"})
_NEW_THREAD_ACK = "Zaczynam nową rozmowę. Poprzednia została zapisana w archiwum."
_NEW_THREAD_ALREADY_FRESH = "Jesteś już w nowej, pustej rozmowie — nie ma czego rozdzielać."

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.core.agent.runtime import AgentRuntime
    from workmate.core.application.conversations import ConversationService
    from workmate.core.application.services import NotesWriteService
    from workmate.core.domain.conversation import ConversationMessage
    from workmate.core.ports.llm import TranscriptEntry

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Bieżąca chwila jako NAIVE UTC — spójna z timestampami bazy rozmów.

    Magazyn zapisuje ``updated_at`` przez ``CURRENT_TIMESTAMP`` (UTC, bez strefy),
    a serwis liczy bezczynność jako ``now - updated_at`` (ADR 0012). ``now`` musi
    więc być w tej samej postaci (naive UTC), inaczej odejmowanie aware−naive rzuca
    ``TypeError``. Zegar jest w adapterze — rdzeń nie woła zegara.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class InboundMessage:
    """Wiadomość z drzwi, znormalizowana do postaci niezależnej od SDK.

    ``text`` wystarcza echu; ``sender``/``conversation_id`` niosą atrybucję, której
    przyszłe ``save_note`` użyje bez zmiany sygnatury szwu (pola addytywne).
    """

    text: str
    sender: str = ""
    conversation_id: str = ""


class Responder(Protocol):
    """Kontrakt szwu: z wiadomości produkuje tekst odpowiedzi."""

    async def respond(self, message: InboundMessage) -> str: ...


class EchoResponder:
    """Spike: potwierdza odbiór, nie dotykając rdzenia WorkMate."""

    async def respond(self, message: InboundMessage) -> str:
        return f"Odebrałem notatkę: {message.text}"


class RuntimeResponder:
    """Szew M1→drzwi (ADR 0008): odpowiedź składa runtime agenta rdzenia.

    Wpięcie to jedna linia w entry-poincie drzwi (``EchoResponder()`` →
    ``RuntimeResponder(runtime)``); handler i wiring bez zmian. ``AgentRuntime.run``
    jest synchroniczny (woła Claude API), więc uruchamiamy go w wątku puli, żeby nie
    blokować pętli zdarzeń drzwi async. Katalog runtime'u dla mniej zaufanych drzwi
    (Teams, Telegram) budujemy BEZ ``write_service`` (ADR 0006) — agent czyta, ale
    nie zapisuje.
    """

    def __init__(self, runtime: AgentRuntime) -> None:
        self._runtime = runtime

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._runtime.run, message.text)


class SaveNoteResponder:
    """STUB (ADR 0008): responder zapisujący wiadomość jako notatkę przez ``save_note``.

    Świadomie NIEWPIĘTY: drzwi asynchroniczne (Teams, Telegram) są mniej zaufane
    (ADR 0006), więc bezpośredni zapis z nich wymaga osobnej decyzji (bramka zapisu
    per drzwi + parsowanie wiadomości w ``NoteMetadata``). Zostawiony jako punkt
    szwu — realizacja to kolejny krok M3/M4, nie spike.
    """

    def __init__(self, write_service: NotesWriteService) -> None:
        self._write_service = write_service

    async def respond(self, message: InboundMessage) -> str:
        raise NotImplementedError(
            "SaveNoteResponder to stub — zapis z drzwi wymaga decyzji bramkowania (ADR 0006/0008)."
        )


class ConversationalResponder:
    """Szew: runtime agenta z PAMIĘCIĄ rozmowy (wątkowość + limit kontekstu, ADR 0010).

    Utrzymuje historię per (kanał, rozmowa) w ``ConversationService``; przy limicie
    kontekstu automatycznie startuje nową rozmowę (rollover), a runtime dostaje
    historię BIEŻĄCEJ rozmowy jako kontekst. ``channel`` rozróżnia drzwi (``telegram``/
    ``teams``) w bazie rozmów. Wywołania synchroniczne (magazyn + runtime) idą w wątku
    puli, żeby nie blokować pętli async drzwi.
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        conversations: ConversationService,
        *,
        channel: str,
        clock: Callable[[], datetime] = _utcnow,
        show_thinking: bool = False,
    ) -> None:
        self._runtime = runtime
        self._conversations = conversations
        self._channel = channel
        # Źródło „teraz" dla kryterium bezczynności (ADR 0012); wstrzykiwalne, by testy
        # mogły symulować upływ czasu bez realnego zegara. Domyślnie naive UTC.
        self._clock = clock
        # Czy dołączać podsumowanie rozumowania modelu do odpowiedzi. TYLKO drzwi zaufane
        # (CLI) — domyślnie False, żeby async drzwi (Telegram/Teams) nie wysyłały rozumowania
        # użytkownikom (treść wewnętrzna, nie część odpowiedzi).
        self._show_thinking = show_thinking
        # Serializuje SZYBKIE operacje na magazynie (wybór wątku, utrwalenie tury),
        # bo drzwi async wołają respond() z puli wątków (run_in_executor) i dwie tury
        # naraz mogłyby podwójnie otworzyć/osierocić rozmowę. Wolne wywołanie LLM
        # zostaje POZA zamkiem — równoległość między rozmowami zachowana.
        self._store_lock = threading.Lock()

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._respond_sync, message)

    def _respond_sync(self, message: InboundMessage) -> str:
        # Klucz wątku: rozmowa z kanału (czat/wątek), a gdy jej brak — nadawca.
        external_id = message.conversation_id or message.sender or "default"
        # Komenda jawnego startu wątku: domknij bieżący wątek i potwierdź — bez wołania
        # LLM i bez zapisu tury (sama komenda nie jest treścią rozmowy).
        if _is_new_thread_command(message.text):
            with self._store_lock:
                started = self._conversations.start_new_thread(self._channel, external_id)
            return _NEW_THREAD_ACK if started else _NEW_THREAD_ALREADY_FRESH
        now = self._clock()  # dla kryterium bezczynności (ADR 0012)
        with self._store_lock:
            conversation_id, history, rolled_over = self._conversations.prepare_turn(
                self._channel, external_id, message.text, now=now
            )
        # Błąd runtime propaguje się TU — nic nie utrwalono, brak osieroconej tury.
        result = self._runtime.run_turn(message.text, history=_to_transcript(history))
        # Bezstratny zapis PEŁNEGO transkryptu tury (ADR 0011): wiadomość + tury
        # assistant/tool z blokami VERBATIM. Tura ucięta jest już wykluczona z ``entries``.
        with self._store_lock:
            self._conversations.record_run(
                conversation_id, result.entries, stop_reason=result.stop_reason
            )
        reply = _with_notices(
            result.reply, rolled_over=rolled_over, stop_reason=result.stop_reason
        )
        if self._show_thinking:
            reply = _with_thinking(reply, result.thinking)
        return reply


class SafeResponder:
    """Dekorator responder'a: łagodna degradacja przy błędach (odporność drzwi async).

    Owija dowolny ``Responder`` i łapie błędy, żeby wdrożony bot nie odpowiadał ciszą
    ani tracebackiem, gdy runtime/narzędzie/infrastruktura zawiedzie:

    - ``WorkMateError`` (LLM, repozytorium, zapis) — błąd oczekiwany (np. przejściowy
      błąd Claude API): log WARNING + przyjazny komunikat.
    - dowolny inny wyjątek — defekt kodu: log z pełnym tracebackiem (``exception``),
      ale i tak zwracamy komunikat zamiast wywracać proces bota (jedna zła tura nie
      kładzie usługi). Błąd NIE jest połykany po cichu — ląduje w logu ze szczegółami.

    Analogicznie do granicy MCP (która zamienia błąd na ``{"error": ...}``) — ten szew
    daje tę granicę drzwiom async (Teams, Telegram). Kontekst (nadawca, rozmowa) w logu.
    """

    _FALLBACK = (
        "Przepraszam, wystąpił chwilowy błąd po mojej stronie. "
        "Spróbuj ponownie za chwilę."
    )

    def __init__(self, inner: Responder, *, fallback: str = _FALLBACK) -> None:
        self._inner = inner
        self._fallback = fallback

    async def respond(self, message: InboundMessage) -> str:
        try:
            return await self._inner.respond(message)
        except WorkMateError as exc:
            logger.warning(
                "Błąd obsługi wiadomości (nadawca=%r, rozmowa=%r): %s",
                message.sender,
                message.conversation_id,
                exc,
            )
            return self._fallback
        except Exception:
            logger.exception(
                "Nieoczekiwany błąd obsługi wiadomości (nadawca=%r, rozmowa=%r)",
                message.sender,
                message.conversation_id,
            )
            return self._fallback


def _is_new_thread_command(text: str) -> bool:
    """Czy wiadomość to komenda jawnego startu wątku (pierwszy token, np. ``/nowa``).

    Wymaga ukośnika (komendy w ``_NEW_THREAD_COMMANDS``), więc zwykłe zdanie zaczynające
    się od słowa „nowa" nie zostanie pomylone z komendą. Ewentualne argumenty po komendzie
    są ignorowane (liczy się pierwszy token). W czacie GRUPOWYM Telegram dokleja do komendy
    sufiks ``@nazwa_bota`` (np. ``/nowa@WorkMateBot``) — obcinamy go przed dopasowaniem,
    żeby rozpoznanie zostało w jednym miejscu (szew), bez wiedzy o SDK drzwi.
    """
    stripped = text.strip()
    if not stripped:
        return False
    token = stripped.split()[0].split("@", 1)[0].lower()
    return token in _NEW_THREAD_COMMANDS


def _with_thinking(reply: str, thinking: str) -> str:
    """Poprzedź odpowiedź podsumowaniem rozumowania modelu (tylko drzwi zaufane — CLI).

    ``thinking`` (gdy ``display=summarized``) to czytelne streszczenie toku myślenia.
    Pokazujemy je nad odpowiedzią, wyraźnie oznaczone; puste — nic nie dodajemy.
    """
    if not thinking.strip():
        return reply
    return f"[rozumowanie modelu]\n{thinking.strip()}\n\n{reply}"


def _with_notices(reply: str, *, rolled_over: bool, stop_reason: str) -> str:
    """Dołóż notki systemowe przed odpowiedź (rollover rozmowy, ucięcie na limicie).

    Notka rolloveru jest NEUTRALNA co do powodu: nowy wątek startuje albo po limicie
    kontekstu, albo po dłuższej przerwie (bezczynność, ADR 0012) — ``prepare_turn`` nie
    rozróżnia tych przyczyn, a użytkownikowi wystarczy wiedza, że zaczęła się nowa rozmowa.
    """
    notices: list[str] = []
    if rolled_over:
        notices.append(
            "(Zaczynam nową rozmowę — poprzednia dobiegła limitu kontekstu "
            "albo minęła dłuższa przerwa.)"
        )
    if stop_reason == _TRUNCATED_STOP:
        notices.append("(Odpowiedź została ucięta — przekroczyła limit długości.)")
    if not notices:
        return reply
    return "\n".join(notices) + "\n\n" + reply


def _to_transcript(messages: list[ConversationMessage]) -> list[TranscriptEntry]:
    """Zmapuj tury rozmowy na wpisy transkryptu LLM — bezstratnie (ADR 0011).

    Tury asystenta z zapisanymi blokami odtwarzamy jako ``RawTurn`` (bloki dostawcy
    VERBATIM — thinking z ``signature`` wraca 1:1); tury ``tool`` jako ``ToolResults``
    z formy domenowej. Wiersze sprzed 0011 (bez bloków) degradują do text-only
    ``AssistantTurn`` / ``UserText`` — zawsze poprawne do odesłania do API.
    """
    entries: list[TranscriptEntry] = []
    for msg in messages:
        if msg.role == "assistant":
            if msg.blocks:
                entries.append(RawTurn("assistant", tuple(msg.blocks)))
            elif msg.text:
                entries.append(AssistantTurn(msg.text, ()))
        elif msg.role == "tool":
            if msg.blocks:
                entries.append(
                    ToolResults(
                        tuple(
                            ToolOutput(
                                b["call_id"], b["content"], b.get("is_error", False)
                            )
                            for b in msg.blocks
                        )
                    )
                )
        elif msg.text:
            entries.append(UserText(msg.text))
    return entries
