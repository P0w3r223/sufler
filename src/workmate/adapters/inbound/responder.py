"""Szew między DRZWIAMI (Teams, CLI, …) a TREŚCIĄ odpowiedzi (Faza 2).

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
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol

from workmate.adapters.inbound.brief_command import BriefContext
from workmate.adapters.inbound.change_command import ChangeDigestContext
from workmate.adapters.inbound.commands import CommandContext
from workmate.adapters.inbound.thread_note_command import ThreadNoteContext
from workmate.core.agent.prompt import build_session_header
from workmate.core.domain.trust import TrustClass
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.errors import WorkMateError
from workmate.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    Attachment,
    AttachmentQueue,
    RawTurn,
    ToolOutput,
    ToolResults,
    UserText,
    attachment_from_row,
)

# ``stop_reason`` oznaczający uciętą odpowiedź (ADR 0011) — drzwi dokładają notkę.
_TRUNCATED_STOP = "max_tokens"

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from workmate.adapters.inbound.brief_command import BriefRouter
    from workmate.adapters.inbound.change_command import ChangeDigestRouter
    from workmate.adapters.inbound.commands import CommandRouter
    from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
    from workmate.adapters.inbound.thread_note_command import ThreadNoteRouter
    from workmate.core.agent.runtime import AgentRuntime
    from workmate.core.application.audit import AuditService
    from workmate.core.application.compaction import CompactionService
    from workmate.core.application.conversations import ConversationService
    from workmate.core.application.metrics import MetricsService
    from workmate.core.application.services import NotesWriteService
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.conversation import ConversationMessage, ConversationSummary
    from workmate.core.ports.llm import TranscriptEntry

# Prefiks wiadomości z podsumowaniem kompaktowania (ADR 0014). Sonnet 5 nie ma systemowych
# wiadomości w środku rozmowy, więc podsumowanie idzie jako treść użytkownika z tym nagłówkiem.
_SUMMARY_PREFIX = "[Podsumowanie wcześniejszej rozmowy]"

# Narzędzia, których WYNIK niesie treść pisaną przez osoby spoza pionu (ADR 0066): komentarze,
# opisy issue/PR i wyjście powłoki. `Notes`/`Jira`/`Schedule` tu NIE są — czytają treść zza
# bramek zdolności, więc skaziłyby każdą rozmowę i zamieniły sygnał w szum.
_TAINTING_TOOLS = frozenset({"GitHub", "Bash", "File"})

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
    ``sender_id`` (AAD id nadawcy, addytywne) niesie CEL wyjściowej dostawy 1:1 (ADR 0027) —
    drzwi bez tego pojęcia zostawiają je puste, a narzędzie push-u się nie zbuduje.
    """

    text: str
    sender: str = ""
    conversation_id: str = ""
    attachments: tuple[Attachment, ...] = ()
    sender_id: str = ""
    # Szew „zapisz to" (ADR 0048), addytywne: ``mentions_bot`` = wiadomość @wzmiankuje bota
    # (warunek wyzwalacza); ``source_message_id`` = id wzmianki (klucz idempotencji, §5);
    # ``source_timestamp`` = Graph ``created`` wzmianki (deterministyczna data notatki). Drzwi bez
    # tego pojęcia zostawiają je puste/False, a router „zapisz to" nie zbuduje się (bramka OFF).
    mentions_bot: bool = False
    source_message_id: str = ""
    source_timestamp: str = ""


class Responder(Protocol):
    """Kontrakt szwu: z wiadomości produkuje tekst odpowiedzi."""

    async def respond(self, message: InboundMessage) -> str: ...


class OutboxDeliverer(Protocol):
    """Dwufazowa dostawa ze skrzynki nadawczej rozmowy (ADR 0009 paczki wdrożeniowej).

    ``snapshot`` musi paść PRZED turą, ``deliver`` po niej. Migawka jest granicą pochodzenia
    plików: rozmowy dzielą jeden wolumen brudnopisu (ADR 0010 paczki), więc bez niej nie da się
    odróżnić wyniku tej tury od pliku podłożonego wcześniej przez inną rozmowę.
    """

    def snapshot(self, scope: WorkspaceScope) -> None: ...

    def deliver(self, scope: WorkspaceScope) -> str: ...


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
    (Teams) budujemy BEZ ``write_service`` (ADR 0006) — agent czyta, ale
    nie zapisuje.
    """

    def __init__(self, runtime: AgentRuntime, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._runtime = runtime
        # Zegar wstrzykiwany jak w ``ConversationalResponder`` — nagłówek sesji (ADR 0056)
        # niesie datę, a rdzeń zegara nie woła.
        self._clock = clock

    async def respond(self, message: InboundMessage) -> str:
        loop = asyncio.get_running_loop()
        header = build_session_header(self._clock(), thread=message.conversation_id)
        return await loop.run_in_executor(
            None,
            lambda: self._runtime.run(
                message.text, attachments=message.attachments, session_header=header
            ),
        )


class SaveNoteResponder:
    """STUB (ADR 0008): responder zapisujący wiadomość jako notatkę przez ``save_note``.

    Świadomie NIEWPIĘTY: drzwi asynchroniczne (Teams) są mniej zaufane
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
    historię BIEŻĄCEJ rozmowy jako kontekst. ``channel`` rozróżnia drzwi (``teams``/
    ``teams_graph``) w bazie rozmów. Wywołania synchroniczne (magazyn + runtime) idą w wątku
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
        shell_catalog_factory: Callable[[WorkspaceScope, str], list[ToolSpec]] | None = None,
        file_catalog_factory: (
            Callable[[WorkspaceScope, AttachmentQueue], Sequence[ToolSpec]] | None
        ) = None,
        attachment_stager: (
            Callable[[WorkspaceScope, Sequence[Attachment]], Sequence[str]] | None
        ) = None,
        attachment_budget_bytes: int = 0,
        sender_trust: Callable[[str], TrustClass] | None = None,
        trust_labels: bool = False,
        thread_tool_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
        user_push_tool_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
        my_jira_tasks_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
        notes_read_factory: Callable[[str], Sequence[ToolSpec]] | None = None,
        github_thread_link: Callable[[str], tuple[str, int] | None] | None = None,
        meeting_notes: MeetingNoteRouter | None = None,
        thread_note: ThreadNoteRouter | None = None,
        project_brief: BriefRouter | None = None,
        change_digest: ChangeDigestRouter | None = None,
        metrics: MetricsService | None = None,
        audit: AuditService | None = None,
        outbox_delivery: OutboxDeliverer | None = None,
        skills: Sequence[tuple[str, str]] = (),
    ) -> None:
        self._runtime = runtime
        self._conversations = conversations
        self._channel = channel
        # Fabryka narzędzi KATALOGU ROBOCZEGO per rozmowa (ADR 0018); ``None`` → brak zapisu plików.
        # Scope budujemy z ZAUFANEGO (kanał, external_id), nie od modelu — rozmowy są izolowane.
        self._workspace_catalog_factory = workspace_catalog_factory
        # Fabryka narzędzia POWŁOKI per rozmowa (ADR 0057); ``None`` → brak (bramka wyłączona
        # albo platforma bez gniazd unix). Bierze scope (``cwd`` rozmowy) ORAZ ``sender_id``: na
        # drzwiach wieloużytkownikowych (Teams) bramkuje powłokę członkostwem nadawcy (ADR 0063),
        # bo powłoka sięga ścieżką bezwzględną poza scope — jej granica zaufania musi zrównać się
        # z ``identities.yaml``, jak każda ścieżka danych. Drzwi zaufane (CLI) podają fabrykę bez
        # autoryzatora → powłoka nie bramkowana (jeden operator, brak ``sender_id``).
        self._shell_catalog_factory = shell_catalog_factory
        # Narzędzie ``File`` (ADR 0064) i odkładanie załączników na dysk rozmowy. ``None`` →
        # dawne zachowanie: załącznik żyje wyłącznie w blokach rozmowy, a model nie ma jak po
        # niego wrócić. Budżet materiałów tury jest WSPÓLNY z drzwiami — pobrania modelu i
        # załączniki użytkownika jadą w tym samym żądaniu API, więc dzielą jeden sufit.
        self._file_catalog_factory = file_catalog_factory
        self._attachment_stager = attachment_stager
        self._attachment_budget_bytes = attachment_budget_bytes
        # Rozszczepienie nadawcy na T1/T2 (ADR 0066) — OPT-IN, jedzie za tą samą bramką co
        # autoryzacja odczytu notatek (0062), bo obie zależą od tego samego faktu: czy mapa
        # tożsamości jest kompletna. Przy niekompletnej mapie włączenie zdegradowałoby realnych
        # członków pionu do danych. ``None`` → każda tura jest T1, jak dotąd.
        self._sender_trust = sender_trust
        # Strukturalne koperty T3 na treści obcej — niezależne od powyższego, bo NIE zależą od
        # mapy tożsamości i nikogo nie degradują: plik jest plikiem niezależnie od tego, kto go
        # przysłał. Stąd osobna bramka, a nie jedna wspólna.
        self._trust_labels = trust_labels
        # Fabryka narzędzia ODPOWIEDZI W WĄTKU (ADR 0024, Faza 3b); ``None`` → brak (inne drzwi).
        # Z ``external_id`` (``team/channel/root``) odczytuje cel wątku i wstrzykuje scoped
        # ``reply_on_thread`` z PRE-ZWIĄZANYM numerem — model nie przekieruje na inne issue.
        self._thread_tool_factory = thread_tool_factory
        # Fabryka narzędzia PUSH-U OBRAZU do rozmówcy 1:1 (ADR 0027, A′3); ``None`` → brak (inne
        # drzwi lub bramka off). Klucz to ``sender_id`` (AAD id nadawcy), NIE external_id wątku:
        # cel dostawy jest PRE-ZWIĄZANY z nadawcy, model nie podaje odbiorcy (anty-eksfiltracja).
        self._user_push_tool_factory = user_push_tool_factory
        # Fabryka narzędzia "moje zadania" Jira (ADR 0054), PER NADAWCA (jak push-u obrazu);
        # ``None`` → brak (Jira/tożsamość nie skonfigurowane albo inne drzwi). Ta sama fabryka
        # zasila komendę ``/moje-zadania`` w ``CommandRouter`` — jedno miejsce rozwiązywania
        # tożsamości.
        self._my_jira_tasks_factory = my_jira_tasks_factory
        # Fabryka narzędzi ODCZYTU bazy wiedzy (ADR 0062), PER NADAWCA (jak push-u/„moje zadania")
        # — ``None`` gdy bramka odczytu wyłączona / powłoka obecna / inne drzwi. Rozpoznany członek
        # dostaje realne search_notes/get_note/list_projects; nierozpoznany — te same nazwy jako
        # odmowa. Gdy wpięta, narzędzia odczytu są STŁUMIONE w katalogu bazowym runtime'u (żeby
        # nie było drogi obejścia bramki); tu wracają, domknięte tożsamością TEGO nadawcy.
        self._notes_read_factory = notes_read_factory
        # Powiązanie wątku Teams z issue/PR (ADR 0024) — do NAGŁÓWKA SESJI, nie do katalogu.
        # Do kroku 5.5 (ADR 0009 paczki) jechało jako narzędzie `reply_on_thread` z numerem
        # domkniętym w closurze; wołało tę samą metodę serwisu co `GitHub(action='comment')`,
        # za tą samą bramką i obok niej, więc niczego nie zawężało — wypełniało argument.
        self._github_thread_link = github_thread_link
        # Router komend read-only (``/pomoc``, ``/szukaj``, …); ``None`` → brak komend (dawne
        # zachowanie). Wpinany w ``build_conversational_responder``; obejmuje wszystkie drzwi.
        self._commands = commands
        # OSOBNY router komendy ZAPISU ``/notatka`` (produkcyjne M3, ADR 0009/0041); ``None`` →
        # brak (bramka off / inne drzwi). Read-only ``CommandRouter`` zostaje read-only (ADR 0017);
        # ta komenda pisze notatkę i biegnie POZA ``_store_lock`` (pobór Graph + Claude są wolne).
        self._meeting_notes = meeting_notes
        # OSOBNY router przechwycenia „zapisz to" (ADR 0048, F2); ``None`` → brak (bramka off / inne
        # drzwi). Wyzwalany @wzmianką bota + dyrektywą; pisze notatkę z WĄTKU (nie ze spotkania) i
        # biegnie POZA ``_store_lock`` (pobór wątku + Claude są wolne), jak router spotkań.
        self._thread_note = thread_note
        # OSOBNY router one-pagera „ogarnij mnie na <projekt>" (ADR 0051, F4); ``None`` → brak
        # (bramka off / inne drzwi). Wyzwalany @wzmianką bota + dyrektywą; READ-ONLY (status +
        # notatki), więc bez bramki zapisu/autoryzacji; biegnie POZA ``_store_lock`` (odczyt
        # notatek/statusu bywa wolny), jak pozostałe routery dyrektyw.
        self._project_brief = project_brief
        # OSOBNY router digestu „co się zmieniło od <data>" (ADR 0052, F5); ``None`` → brak
        # (bramka off / inne drzwi). Wyzwalany @wzmianką bota + dyrektywą; READ-ONLY (fold
        # zdarzeń), poza ``_store_lock``, jak brief.
        self._change_digest = change_digest
        # Licznik wywołań (Tor A, metryki); ``None`` → wyłączony (brak WORKMATE_METRICS_DB). Zapis
        # jest best-effort na WSZYSTKICH turach (także komendach) — liczymy „wywołania per drzwi".
        self._metrics = metrics
        # Dziennik audytu wywołań narzędzi (Faza 0, ADR 0067); ``None`` → wyłączony (brak
        # WORKMATE_AUDIT_DB). Gdy wpięty, budujemy rejestrator PER TURĘ (pseudonim nadawcy/rozmowy
        # domknięty raz) i podajemy go do ``run_turn`` — runtime woła go dla każdego tool-calla.
        self._audit = audit
        # Dostawa plików ze skrzynki nadawczej rozmowy PO turze (ADR 0009 paczki); ``None`` → brak
        # (bramka off / inne drzwi). Zwraca zdanie do doklejenia do odpowiedzi albo pusty napis.
        # Ten sam ``scope`` co narzędzia katalogu roboczego — skrzynka leży w katalogu TEJ rozmowy,
        # więc model nie ma jak nadać pliku „z cudzej".
        self._outbox_delivery = outbox_delivery
        # Lista procedur z `/mnt/skills` (ADR 0005) — czytana RAZ przy składaniu drzwi, bo jest
        # stała w obrębie procesu. Idzie do nagłówka sesji, nie do korpusu: korpus niesie
        # breakpoint cache'u, a lista bywa zmieniana między wydaniami obrazu.
        self._skills = tuple(skills)
        # Kompaktowanie historii (ADR 0014); ``None`` → wyłączone (replay = pełna historia,
        # rollover na limicie działa jak wcześniej). Gdy wpięte, drzwi streszczają starą
        # część rozmowy po przekroczeniu progu i doklejają podsumowanie do kontekstu.
        self._compaction = compaction
        # Źródło „teraz" dla kryterium bezczynności (ADR 0012); wstrzykiwalne, by testy
        # mogły symulować upływ czasu bez realnego zegara. Domyślnie naive UTC.
        self._clock = clock
        # Czy dołączać podsumowanie rozumowania modelu do odpowiedzi. TYLKO drzwi zaufane
        # (CLI) — domyślnie False, żeby async drzwi (Teams) nie wysyłały rozumowania
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
        # Metryka wywołania (Tor A): best-effort, PRZED dispatchem, więc liczy też komendy. Nadawca
        # jest pseudonimizowany w serwisie; błąd licznika (np. blokada SQLite) NIE może zabić tury.
        if self._metrics is not None:
            try:
                self._metrics.record(
                    self._channel, message.sender_id or message.sender, self._clock()
                )
            except Exception:
                logger.warning(
                    "Nie udało się zapisać metryki wywołania (kanał %r) — pomijam", self._channel
                )
        # Komenda read-only (``/pomoc``, ``/nowa``, ``/szukaj``, …): wykonaj i zwróć odpowiedź
        # PRZED pętlą agenta — bez wołania LLM i bez ``record_run`` (komenda ≠ tura rozmowy,
        # nie liczy się do limitu kontekstu ani FTS). ``dispatch`` = ``None`` → to zwykła wiadomość.
        if self._commands is not None:
            with self._store_lock:
                reply = self._commands.dispatch(
                    message.text,
                    CommandContext(self._channel, external_id, message.sender_id),
                )
            if reply is not None:
                return reply
        # Komenda ZAPISU ``/notatka`` (produkcyjne M3, ADR 0009/0041) — POZA ``_store_lock`` (pobór
        # transkryptu z Graph + streszczenie Claude są wolne, a save_note ma własną create-only
        # bezpieczną współbieżność). ``None`` = to nie ta komenda → normalna tura agenta niżej.
        if self._meeting_notes is not None:
            # ``sender_id`` (AAD id nadawcy) NIESIE tożsamość do autoryzacji zapisu (B2 / ADR 0042):
            # router rozstrzyga członkostwo, zanim ruszy transkrypt. Read-only dispatch wyżej go
            # nie potrzebuje (komendy odczytu nie zależą od nadawcy).
            reply = self._meeting_notes.dispatch(
                message.text, CommandContext(self._channel, external_id, message.sender_id)
            )
            if reply is not None:
                return reply
        # Wyzwalacz „zapisz to" (ADR 0048, F2) — POZA ``_store_lock`` (pobór wątku + Claude wolne).
        # Rusza TYLKO przy @wzmiance bota; ``None`` = zwykła wiadomość → tura agenta niżej.
        # ``external_id`` (team/channel/root) = cel poboru/odpowiedzi; ``source_*`` niosą klucz
        # idempotencji (id wzmianki) i deterministyczną datę (Graph timestamp), nie zegar obsługi.
        if self._thread_note is not None:
            reply = self._thread_note.dispatch(
                message.text,
                ThreadNoteContext(
                    external_id=external_id,
                    source_message_id=message.source_message_id,
                    source_timestamp=message.source_timestamp,
                    sender_id=message.sender_id,
                    mentions_bot=message.mentions_bot,
                ),
            )
            if reply is not None:
                return reply
        # One-pager „ogarnij mnie na <projekt>" (ADR 0051, F4) — POZA ``_store_lock`` (odczyt
        # notatek/statusu). Rusza TYLKO przy @wzmiance bota; ``None`` = zwykła wiadomość → tura
        # agenta niżej. ``external_id`` (team/channel/root) = cel ewentualnej dostawy PDF w wątku.
        if self._project_brief is not None:
            reply = self._project_brief.dispatch(
                message.text,
                BriefContext(external_id=external_id, mentions_bot=message.mentions_bot),
            )
            if reply is not None:
                return reply
        # Digest „co się zmieniło od <data>" (ADR 0052, F5) — POZA ``_store_lock`` (fold zdarzeń).
        # Rusza TYLKO przy @wzmiance bota; ``None`` = zwykła wiadomość → tura agenta niżej.
        if self._change_digest is not None:
            reply = self._change_digest.dispatch(
                message.text,
                ChangeDigestContext(external_id=external_id, mentions_bot=message.mentions_bot),
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
        scope = WorkspaceScope(self._channel, external_id)
        extra_tools: list[ToolSpec] = list(
            self._workspace_catalog_factory(scope)
            if self._workspace_catalog_factory is not None
            else ()
        )
        # Powłoka (ADR 0057) dokładana tym samym scope: polecenia startują w katalogu roboczym
        # tej rozmowy, więc pliki tworzone narzędziem i widziane powłoką to te same pliki. Fabryka
        # dostaje też ``sender_id``: na drzwiach wieloużytkownikowych bramkuje powłokę członkostwem
        # (ADR 0063) — nierozpoznany nadawca → pusta lista. Jak przy narzędziach odczytu niżej: błąd
        # budowy (np. rozwiązywanie tożsamości) degraduje do „brak powłoki" (fail-closed) i loguje,
        # nie zabija tury.
        if self._shell_catalog_factory is not None:
            try:
                extra_tools.extend(self._shell_catalog_factory(scope, message.sender_id))
            except Exception:
                logger.warning(
                    "Nie udało się zbudować narzędzia powłoki dla nadawcy %r — pomijam",
                    message.sender_id,
                )
        # Załączniki tej tury odkładamy na dysk katalogu rozmowy (ADR 0064), zanim model
        # cokolwiek zobaczy: dopiero plik na dysku widzi ZARAZEM powłoka i ``File(read)``, i tylko
        # on przeżywa kompaktowanie kontekstu, po którym z załącznika zostaje sam opis. Odkładanie
        # jest OPCJONALNYM wzbogaceniem — pełny dysk czy zła nazwa nie mogą zabić tury, w której
        # model i tak dostaje załącznik w kontekście. Nazwy trafiają do nagłówka sesji, bo na dysku
        # są slugiem oryginalnej nazwy i model inaczej zgadywałby, jak wołać ``File``.
        staged_files: list[str] = []
        if self._attachment_stager is not None and message.attachments:
            try:
                staged_files = list(self._attachment_stager(scope, message.attachments))
            except Exception:
                logger.warning(
                    "Nie udało się odłożyć załączników rozmowy %r — pomijam", external_id
                )
        # Kolejka materiałów tej tury: sufit pomniejszony o to, co drzwi już wstawiły do tury
        # użytkownika (dzielą jedno żądanie API). Ujemny wynik podcinamy do zera — model dostanie
        # rzeczową odmowę „budżet wyczerpany", zamiast pobrania, które wywróci żądanie.
        attachment_queue = AttachmentQueue(
            budget_bytes=max(0, self._attachment_budget_bytes - _attachment_bytes(message))
        )
        if self._file_catalog_factory is not None:
            try:
                extra_tools.extend(self._file_catalog_factory(scope, attachment_queue))
            except Exception:
                logger.warning(
                    "Nie udało się zbudować narzędzia File dla %r — pomijam", external_id
                )
        # Narzędzie odpowiedzi w wątku (ADR 0024, Faza 3b): dokładane, gdy wątek kanału jest
        # powiązany z issue/PR (fabryka odczytuje cel z external_id) — inaczej pusta lista.
        # To OPCJONALNE wzbogacenie: błąd odczytu mapowania (np. blokada SQLite) NIE może zabić
        # tury odczytowej — degradujemy do „brak narzędzia wątku" i logujemy.
        if self._thread_tool_factory is not None:
            try:
                extra_tools.extend(self._thread_tool_factory(external_id))
            except Exception:
                logger.warning(
                    "Nie udało się zbudować narzędzia wątku dla %r — pomijam", external_id
                )
        # Narzędzie push-u obrazu 1:1 (ADR 0027, A′3): dokładane, gdy wiadomość niesie ``sender_id``
        # (drzwi Teams) i bramka włączona. Cel wiąże się z NADAWCY (nie od modelu). Jak wyżej —
        # opcjonalne wzbogacenie: błąd budowy nie może zabić tury, degradujemy i logujemy.
        if self._user_push_tool_factory is not None and message.sender_id:
            try:
                extra_tools.extend(self._user_push_tool_factory(message.sender_id))
            except Exception:
                logger.warning(
                    "Nie udało się zbudować narzędzia push-u obrazu dla nadawcy %r — pomijam",
                    message.sender_id,
                )
        # Narzędzie "moje zadania" Jira (ADR 0054): dokładane, gdy wiadomość niesie ``sender_id``
        # i tożsamość rozwiązuje się na konto Jira. Jak wyżej — opcjonalne wzbogacenie, błąd budowy
        # nie może zabić tury.
        if self._my_jira_tasks_factory is not None and message.sender_id:
            try:
                extra_tools.extend(self._my_jira_tasks_factory(message.sender_id))
            except Exception:
                logger.warning(
                    "Nie udało się zbudować narzędzia 'moje zadania' dla nadawcy %r — pomijam",
                    message.sender_id,
                )
        # Narzędzia ODCZYTU bazy wiedzy bramkowane nadawcą (ADR 0062): gdy bramka wpięta, narzędzia
        # odczytu są ZDJĘTE z katalogu bazowego, więc ta fabryka jest ich JEDYNĄ drogą — dokładamy
        # ją ZAWSZE, gdy jest (także przy pustym sender_id: fabryka zwróci wtedy odmowę, nie ciszę).
        # Błąd budowy degraduje do „brak narzędzi odczytu" (fail-closed) i loguje — nie zabija tury.
        if self._notes_read_factory is not None:
            try:
                extra_tools.extend(self._notes_read_factory(message.sender_id))
            except Exception:
                logger.warning(
                    "Nie udało się zbudować narzędzi odczytu bazy wiedzy dla nadawcy %r — pomijam",
                    message.sender_id,
                )
        # Błąd runtime propaguje się TU — nic nie utrwalono, brak osieroconej tury.
        # Nagłówek sesji (ADR 0056) składamy PER TURĘ, nie raz na starcie procesu: kontener
        # jest długożyjący (poller chodzi dobami), więc data zamrożona przy starcie rozjechałaby
        # się z rzeczywistością następnego dnia. ``now`` policzono wyżej — tura ma jedną chwilę,
        # wspólną z kryterium bezczynności.
        # Migawka skrzynki PRZED wywołaniem modelu — dopiero za chwilę dostanie powłokę.
        # Po turze nie dałoby się już odróżnić pliku, który wytworzył, od podłożonego wcześniej.
        if self._outbox_delivery is not None:
            try:
                self._outbox_delivery.snapshot(scope)
            except Exception:
                logger.warning(
                    "Nie udało się zrobić migawki skrzynki rozmowy %r — dostawa się wstrzyma",
                    external_id,
                    exc_info=True,
                )
        # Klasa POCHODZENIA tej tury (ADR 0066). Rozwiązanie nadawcy pada RAZ i zasila obie
        # osie: tę etykietę oraz — osobno, przez własne fabryki — bramki zdolności (0062/0063).
        # Bez rozszczepienia (fabryka nie podana) każda tura jest T1, czyli zachowanie dawne.
        trust: TrustClass = "T1"
        if self._sender_trust is not None:
            try:
                trust = self._sender_trust(message.sender_id)
            except Exception:
                # Fail-closed: nie umiemy rozstrzygnąć, kto pisze → traktujemy słowa jak dane.
                logger.warning("Nie rozstrzygnąłem klasy nadawcy %r — T2", message.sender_id)
                trust = "T2"
        # Nonce koperty: LOSOWY NA TURĘ i nigdy z treści. Stały znacznik dałoby się podrobić
        # plikiem, który sam zawiera znacznik zamykający.
        trust_nonce = secrets.token_hex(4) if self._trust_labels else ""
        # Rejestrator audytu (ADR 0067) domknięty PER TURĘ: pseudonim nadawcy/rozmowy liczony raz,
        # klasa zaufania z osi pochodzenia. ``None`` → audyt wyłączony. Runtime woła go dla
        # każdego tool-calla; rejestrator jest best-effort (nie wywróci tury).
        audit_recorder = (
            self._audit.turn_recorder(
                door=self._channel,
                raw_user=message.sender_id or message.sender,
                conversation_id=external_id,
                trust_class=trust if self._sender_trust is not None else "unknown",
            )
            if self._audit is not None
            else None
        )
        result = self._runtime.run_turn(
            message.text,
            attachments=message.attachments,
            history=transcript,
            extra_tools=extra_tools,
            session_header=build_session_header(
                now,
                channel=self._channel,
                thread=external_id,
                skills=self._skills,
                github_thread=self._thread_link(external_id),
                staged_files=staged_files,
                trust_nonce=trust_nonce,
            ),
            audit=audit_recorder,
            attachment_queue=attachment_queue,
            trust_nonce=trust_nonce,
            trust=trust,
        )
        # Lepka skaza (ADR 0066) — PO turze, bo dopiero teraz wiadomo, po co model sięgnął.
        # Skaza nie blokuje niczego; zapala się, żeby operacja konsekwentna w tej rozmowie
        # poszła później przez sędziego (ADR 0065) i wylądowała w audycie z klasą tury.
        self._mark_taint(conversation_id, message, trust, result)
        # Bezstratny zapis PEŁNEGO transkryptu tury (ADR 0011): wiadomość + tury
        # assistant/tool z blokami VERBATIM. Tura ucięta jest już wykluczona z ``entries``.
        with self._store_lock:
            self._conversations.record_run(
                conversation_id, result.entries, stop_reason=result.stop_reason
            )
        reply = _with_notices(result.reply, rolled_over=rolled_over, stop_reason=result.stop_reason)
        # Dostawa ze skrzynki nadawczej — PO utrwaleniu tury, żeby awaria wysyłki nie zabrała
        # rozmówcy odpowiedzi tekstowej ani nie osierociła zapisu. Jak pozostałe opcjonalne
        # wzbogacenia: błąd degraduje do „bez załączników" i idzie do logu, nie do użytkownika.
        if self._outbox_delivery is not None:
            try:
                notice = self._outbox_delivery.deliver(scope)
            except Exception:
                logger.warning(
                    "Nie udało się dostarczyć plików ze skrzynki rozmowy %r — pomijam",
                    external_id,
                    exc_info=True,
                )
            else:
                if notice:
                    reply = f"{reply}\n\n{notice}"
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

    def _mark_taint(
        self,
        conversation_id: str,
        message: InboundMessage,
        trust: TrustClass,
        result: AgentResult,
    ) -> None:
        """Zapal skazę, jeśli do TEJ tury weszła treść obca — pierwsze źródło wygrywa.

        Zbiór wyzwalaczy jest wąski ROZMYŚLNIE (ADR 0066 R2): gdyby skaziło wszystko, sygnał
        nie znaczyłby nic. Odczyt notatek i zdarzeń własnego pionu typowanymi narzędziami NIE
        skaża — to treść zza bramek zdolności. Skażają: załącznik, plik podany przez ``File``,
        tura nadawcy, który się nie rozwiązał, oraz treści z GitHuba (komentarze i opisy pisze
        ktokolwiek, a mapa tożsamości nie zna dziś loginów GitHuba, więc autora nie umiemy
        podnieść ponad T3).

        Best-effort: nieudany zapis skazy nie może zabrać użytkownikowi odpowiedzi, która
        właśnie powstała — ale idzie do logu, bo cicha utrata skazy to cicha utrata eskalacji.
        """
        if self._conversations is None:  # pragma: no cover — obrona przed refaktorem
            return
        source = ""
        if message.attachments:
            source = "attachment"
        elif trust == "T2":
            source = "guest"
        elif any(
            call.name in _TAINTING_TOOLS
            for entry in result.entries
            if isinstance(entry, AssistantTurn)
            for call in entry.tool_calls
        ):
            source = "tool"
        if not source:
            return
        try:
            with self._store_lock:
                self._conversations.mark_tainted(conversation_id, source)
        except Exception:
            logger.warning("Nie zapisałem skazy rozmowy %r (źródło %s)", conversation_id, source)

    def _thread_link(self, external_id: str) -> tuple[str, int] | None:
        """Powiązanie wątku z issue/PR albo ``None`` — opcjonalne wzbogacenie nagłówka.

        Jak przy narzędziach per turę: awaria odczytu mapowania (np. blokada SQLite) NIE ma
        prawa zabić tury odczytowej. Degradujemy do „wątek z niczym niepowiązany" i logujemy —
        agent traci wtedy tylko podpowiedź numeru, a nie zdolność komentowania.
        """
        if self._github_thread_link is None:
            return None
        try:
            return self._github_thread_link(external_id)
        except Exception:
            logger.warning("Nie udało się odczytać powiązania wątku %r — pomijam", external_id)
            return None


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
    daje tę granicę drzwiom async (Teams). Kontekst (nadawca, rozmowa) w logu.
    """

    _FALLBACK = "Przepraszam, wystąpił chwilowy błąd po mojej stronie. Spróbuj ponownie za chwilę."

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


def _attachment_bytes(message: InboundMessage) -> int:
    """Ile bajtów base64 drzwi już wstawiły do tury użytkownika (ADR 0064 — wspólny budżet).

    Liczymy SUROWE bajty (base64 ÷ 4 × 3), bo w tej samej jednostce wyrażony jest sufit
    materializacji. Pliki zamienione na tekst nie niosą base64 i słusznie ważą zero — nie idą
    do API jako bajty.
    """
    return sum(len(a.data_base64) * 3 // 4 for a in message.attachments)


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
                # Wiersz tury narzędziowej niesie DWA rodzaje bloków (ADR 0064): wyniki narzędzi
                # (mają ``call_id``) oraz pliki podane przez ``File`` w formie neutralnej. Bez
                # tego podziału plik wróciłby jako wynik bez ``call_id`` i wywrócił replay.
                entries.append(
                    ToolResults(
                        tuple(
                            ToolOutput(b["call_id"], b["content"], b.get("is_error", False))
                            for b in msg.blocks
                            if "call_id" in b
                        ),
                        tuple(attachment_from_row(b) for b in msg.blocks if "call_id" not in b),
                    )
                )
        elif msg.text or msg.blocks:
            # Wiersz użytkownika: ``blocks`` (gdy są) to NEUTRALNA forma załączników —
            # odtwarzamy je, by replay był bezstratny. Warunek ``or msg.blocks`` pilnuje,
            # by wiadomość z SAMYM plikiem (pusty caption) nie wypadła z transkryptu.
            attachments = tuple(attachment_from_row(b) for b in (msg.blocks or []))
            # Klasa pochodzenia (ADR 0066) wraca z wiersza; wiersze sprzed 0066 i drzwi bez
            # rozszczepienia mają NULL → T1, czyli dawne zachowanie. Bez tego tura gościa
            # wracałaby w kolejnych turach jako instrukcja — granica trzymałaby JEDNĄ turę.
            trust: TrustClass = "T2" if msg.trust == "T2" else "T1"
            entries.append(UserText(msg.text, attachments, trust))
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
        # ZACHOWUJEMY klasę pierwszej tury (ADR 0066): podsumowanie doklejamy do jej
        # tekstu, więc gdyby klasa przepadła, tura gościa awansowałaby do instrukcji
        # dokładnie w momencie kompaktowania — czyli tam, gdzie nikt by tego nie szukał.
        return [
            UserText(f"{header}\n\n{first.text}", first.attachments, first.trust),
            *entries[1:],
        ]
    return [UserText(header), *entries]
