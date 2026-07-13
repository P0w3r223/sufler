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

from workmate.adapters.inbound.commands import CommandContext
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.errors import WorkMateError
from workmate.core.ports.llm import (
    AssistantTurn,
    Attachment,
    RawTurn,
    ToolOutput,
    ToolResults,
    UserText,
    attachment_from_row,
)

# ``stop_reason`` oznaczający uciętą odpowiedź (ADR 0011) — drzwi dokładają notkę.
_TRUNCATED_STOP = "max_tokens"

if TYPE_CHECKING:
    from collections.abc import Callable

    from workmate.adapters.inbound.commands import CommandRouter
    from workmate.core.agent.runtime import AgentRuntime
    from workmate.core.application.compaction import CompactionService
    from workmate.core.application.conversations import ConversationService
    from workmate.core.application.services import NotesWriteService
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.conversation import ConversationMessage, ConversationSummary
    from workmate.core.ports.llm import TranscriptEntry

# Prefiks wiadomości z podsumowaniem kompaktowania (ADR 0014). Sonnet 5 nie ma systemowych
# wiadomości w środku rozmowy, więc podsumowanie idzie jako treść użytkownika z tym nagłówkiem.
_SUMMARY_PREFIX = "[Podsumowanie wcześniejszej rozmowy]"

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
    przyszłe ``save_note`` użyje bez zmiany sygnatury szwu (pola addytywne). ``attachments``
    (addytywne, domyślnie puste) niosą treść multimodalną z drzwi, które ją materializują.
    """

    text: str
    sender: str = ""
    conversation_id: str = ""
    attachments: tuple[Attachment, ...] = ()


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
        return await loop.run_in_executor(
            None,
            lambda: self._runtime.run(message.text, attachments=message.attachments),
        )


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
        compaction: CompactionService | None = None,
        commands: CommandRouter | None = None,
        workspace_catalog_factory: Callable[[WorkspaceScope], list[ToolSpec]] | None = None,
    ) -> None:
        self._runtime = runtime
        self._conversations = conversations
        self._channel = channel
        # Fabryka narzędzi KATALOGU ROBOCZEGO per rozmowa (ADR 0018); ``None`` → brak zapisu plików.
        # Scope budujemy z ZAUFANEGO (kanał, external_id), nie od modelu — rozmowy są izolowane.
        self._workspace_catalog_factory = workspace_catalog_factory
        # Router komend read-only (``/pomoc``, ``/szukaj``, …); ``None`` → brak komend (dawne
        # zachowanie). Wpinany w ``build_conversational_responder``; obejmuje wszystkie drzwi.
        self._commands = commands
        # Kompaktowanie historii (ADR 0014); ``None`` → wyłączone (replay = pełna historia,
        # rollover na limicie działa jak wcześniej). Gdy wpięte, drzwi streszczają starą
        # część rozmowy po przekroczeniu progu i doklejają podsumowanie do kontekstu.
        self._compaction = compaction
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
        # Komenda read-only (``/pomoc``, ``/nowa``, ``/szukaj``, …): wykonaj i zwróć odpowiedź
        # PRZED pętlą agenta — bez wołania LLM i bez ``record_run`` (komenda ≠ tura rozmowy,
        # nie liczy się do limitu kontekstu ani FTS). ``dispatch`` = ``None`` → to zwykła wiadomość.
        if self._commands is not None:
            with self._store_lock:
                reply = self._commands.dispatch(
                    message.text, CommandContext(self._channel, external_id)
                )
            if reply is not None:
                return reply
        now = self._clock()  # dla kryterium bezczynności (ADR 0012)
        with self._store_lock:
            conversation_id, history, rolled_over = self._conversations.prepare_turn(
                self._channel, external_id, message.text, now=now
            )
        transcript = self._build_transcript(conversation_id, history, rolled_over)
        # Narzędzia katalogu roboczego (ADR 0018) dokładane per turę, ze scope z ZAUFANEGO
        # (kanał, external_id) — model nie widzi scope w schemacie, więc nie sięgnie cudzej rozmowy.
        extra_tools = (
            self._workspace_catalog_factory(WorkspaceScope(self._channel, external_id))
            if self._workspace_catalog_factory is not None
            else ()
        )
        # Błąd runtime propaguje się TU — nic nie utrwalono, brak osieroconej tury.
        result = self._runtime.run_turn(
            message.text,
            attachments=message.attachments,
            history=transcript,
            extra_tools=extra_tools,
        )
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

    def _build_transcript(
        self,
        conversation_id: str,
        history: list[ConversationMessage],
        rolled_over: bool,
    ) -> list[TranscriptEntry]:
        """Złóż kontekst dla runtime'u; z kompaktowaniem (ADR 0014) — streść i doklej skrót.

        Bez kompaktowania: transkrypt = pełna historia. Z kompaktowaniem: gdy wejście
        ostatniej tury przekroczyło próg, ``maybe_compact`` streszcza starą część (woła LLM,
        więc POZA ``_store_lock``); potem pobieramy skrócony replay i aktywne podsumowanie i
        doklejamy je na początek kontekstu. Po rolloverze wątek jest świeży — nie kompaktujemy
        (podsumowania i tak nie ma).
        """
        if self._compaction is None:
            return _to_transcript(history)
        if not rolled_over:
            self._compaction.maybe_compact(conversation_id)
        with self._store_lock:
            replay = self._conversations.replay_messages(conversation_id)
            summary = self._conversations.active_summary(conversation_id)
        return _to_transcript_with_summary(summary, replay)


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
        elif msg.text or msg.blocks:
            # Wiersz użytkownika: ``blocks`` (gdy są) to NEUTRALNA forma załączników —
            # odtwarzamy je, by replay był bezstratny. Warunek ``or msg.blocks`` pilnuje,
            # by wiadomość z SAMYM plikiem (pusty caption) nie wypadła z transkryptu.
            attachments = tuple(attachment_from_row(b) for b in (msg.blocks or []))
            entries.append(UserText(msg.text, attachments))
    return entries


def _to_transcript_with_summary(
    summary: ConversationSummary | None, messages: list[ConversationMessage]
) -> list[TranscriptEntry]:
    """Jak ``_to_transcript``, ale z doklejonym aktywnym podsumowaniem (ADR 0014).

    Podsumowanie idzie jako treść UŻYTKOWNIKA z prefiksem (Sonnet 5 nie ma systemowych
    wiadomości w środku rozmowy). Doklejamy je do PIERWSZEJ tury użytkownika w replayu —
    po kompaktowaniu replay zaczyna się właśnie turą użytkownika — zamiast wstawiać osobną
    wiadomość, żeby nie powstały dwie tury ``user`` z rzędu. Gdy replay nie zaczyna się od
    użytkownika (sytuacja defensywna), podsumowanie idzie jako osobna wiadomość na początku.
    """
    entries = _to_transcript(messages)
    if summary is None:
        return entries
    header = f"{_SUMMARY_PREFIX}\n{summary.summary}"
    if entries and isinstance(entries[0], UserText):
        first = entries[0]
        # Doklejamy nagłówek do tekstu, ale ZACHOWUJEMY załączniki pierwszej tury.
        return [UserText(f"{header}\n\n{first.text}", first.attachments), *entries[1:]]
    return [UserText(header), *entries]
