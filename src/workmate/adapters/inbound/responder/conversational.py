"""``ConversationalResponder`` — pełna tura agenta: pamięć, kompaktowanie, narzędzia, outbox."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import threading
from datetime import datetime
from typing import TYPE_CHECKING

from workmate.adapters.inbound.brief_command import BriefContext
from workmate.adapters.inbound.change_command import ChangeDigestContext
from workmate.adapters.inbound.commands import CommandContext
from workmate.adapters.inbound.thread_note_command import ThreadNoteContext
from workmate.core.agent.prompt import build_session_header
from workmate.core.domain.trust import TrustClass
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.ports.llm import (
    AgentResult,
    AssistantTurn,
    Attachment,
    AttachmentQueue,
)

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
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.conversation import ConversationMessage
    from workmate.core.domain.mutation import Verdict
    from workmate.core.ports.llm import TranscriptEntry

from workmate.adapters.inbound.responder.protocols import InboundMessage, OutboxDeliverer
from workmate.adapters.inbound.responder.transcript import (
    _attachment_bytes,
    _to_transcript,
    _to_transcript_with_summary,
    _utcnow,
    _with_notices,
    _with_thinking,
)

logger = logging.getLogger(__name__)

# Narzędzia, których WYNIK niesie treść spoza bramek zdolności (ADR 0066): komentarze i opisy
# z GitHuba, wyjście powłoki oraz pliki katalogu roboczego. `Notes`/`Jira`/`Schedule` tu NIE są —
# czytają treść zza bramek, więc skaziłyby każdą rozmowę i zamieniły sygnał w szum.
#
# ``read_file``/``list_files`` są na liście, choć brzmią niewinnie: katalog roboczy trzyma
# ODŁOŻONE ZAŁĄCZNIKI (ADR 0064) i przeżywa rollover, bo jest per (kanał, wątek). Bez nich
# ścieżka „załącznik w rozmowie A → rollover → `read_file` w czystej rozmowie B" wciągałaby tę
# samą zatrutą treść do rozmowy oznaczonej jako czysta — a `Bash` i `File` w tym samym
# scenariuszu skażają. Nazwy pilnuje sonda wiążąca ten zbiór z realnym katalogiem narzędzi;
# bez niej zmiana nazwy narzędzia po cichu gasiłaby wyzwalacz.
_TAINTING_TOOLS = frozenset({"Activity", "Bash", "File", "ReadFile", "ListFiles"})


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
            Callable[
                # Przedostatni argument to SKAZA rozmowy: ``Callable`` zamiast ``bool``, bo
                # fabryka czyta ją w chwili MUTACJI, nie budowy katalogu (ADR 0066) — patrz
                # miejsce wywołania niżej. Ostatni to ujście werdyktu sędziego do wiersza audytu
                # tej tury (ADR 0065 §8); ``None`` przy wyłączonym audycie.
                [
                    WorkspaceScope,
                    AttachmentQueue,
                    str,
                    str,
                    Callable[[], bool],
                    Callable[[Verdict, str], None] | None,
                ],
                Sequence[ToolSpec],
            ]
            | None
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
        # Sekret nonce'a: losowany RAZ przy składaniu drzwi, żyje tylko w pamięci procesu.
        # Nie zapisujemy go — nonce ma być nieprzewidywalny z treści, a nie trwały; restart
        # odświeża wszystkie znaczniki i to jest cecha, nie wada.
        self._nonce_secret = secrets.token_bytes(32)
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
                    mention_texts=message.mention_texts,
                ),
            )
            if reply is not None:
                return reply
        # One-pager „ogarnij mnie na <projekt>" (ADR 0051, F4) — POZA ``_store_lock`` (odczyt
        # notatek/statusu). Rusza TYLKO przy @wzmiance bota; ``None`` = zwykła wiadomość → tura
        # agenta niżej. ``external_id`` (team/channel/root) = cel ewentualnej dostawy PDF w wątku.
        # ``sender_id`` NIESIE tożsamość do bramki odczytu (ADR 0062): brief serwuje treść notatek,
        # a odpalał się przed jakąkolwiek autoryzacją — jedna @wzmianka obchodziła całą bramkę.
        if self._project_brief is not None:
            reply = self._project_brief.dispatch(
                message.text,
                BriefContext(
                    external_id=external_id,
                    mentions_bot=message.mentions_bot,
                    sender_id=message.sender_id,
                ),
            )
            if reply is not None:
                return reply
        # Digest „co się zmieniło od <data>" (ADR 0052, F5) — POZA ``_store_lock`` (fold zdarzeń).
        # Rusza TYLKO przy @wzmiance bota; ``None`` = zwykła wiadomość → tura agenta niżej.
        # ``sender_id`` jak wyżej — bramka jest w routerze, nie tutaj.
        if self._change_digest is not None:
            reply = self._change_digest.dispatch(
                message.text,
                ChangeDigestContext(
                    external_id=external_id,
                    mentions_bot=message.mentions_bot,
                    sender_id=message.sender_id,
                ),
            )
            if reply is not None:
                return reply
        now = self._clock()  # dla kryterium bezczynności (ADR 0012)
        with self._store_lock:
            conversation_id, history, rolled_over = self._conversations.prepare_turn(
                self._channel, external_id, message.text, now=now
            )
        transcript = self._build_transcript(conversation_id, external_id, history, rolled_over)
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

        # Rejestrator audytu (ADR 0067) domknięty PER TURĘ: pseudonim nadawcy/rozmowy liczony raz,
        # klasa zaufania z osi pochodzenia. ``None`` → audyt wyłączony. Runtime woła go dla
        # każdego tool-calla; rejestrator jest best-effort (nie wywróci tury).
        #
        # Powstaje PRZED katalogami, choć używa go dopiero ``run_turn``: fabryka ``File`` bierze
        # z niego ujście werdyktu sędziego (ADR 0065 §8), a katalogi składamy niżej. Zbudowany
        # po nich — jak było do tej zmiany — nie miałby jak trafić do bramki mutacji.
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

        # Skaza z faktów ZNANYCH PRZED turą (ADR 0066) — zapalana TU, przed budową katalogów.
        # Rozdzielenie na dwie połowy (druga niżej, po turze) nie jest kosmetyką: załącznik ląduje
        # na dysku rozmowy PRZED wywołaniem modelu, więc gdyby cała skaza czekała na wynik tury,
        # błąd API w pętli narzędzi zostawiałby zatruty plik w katalogu i rozmowę oznaczoną jako
        # czysta.
        #
        # Kolejność wobec katalogów jest istotą sprawy: fabryka ``File`` dostaje skazę jako
        # ARGUMENT (sędzia mutacji ma orzekać ze świadomością pochodzenia tury), więc zapalanie
        # skazy PO zbudowaniu katalogów znaczyło, że ``File(edit)`` w turze z zatrutym załącznikiem
        # widzi „rozmowa czysta" — dokładnie w turze, w której świadomość pochodzenia jest
        # najbardziej potrzebna.
        #
        # Dwa niezależne ``if``, nie ``if/elif``: gość Z załącznikiem ma DWA źródła skazy i oba są
        # faktem. ``elif`` gubił „guest", gdy tura miała też załącznik — a w audycie odróżnienie
        # „treść przyszła plikiem" od „pisze ktoś spoza pionu" jest tym, czego się szuka.
        if message.attachments:
            self._mark_taint(conversation_id, "attachment")
        if trust == "T2":
            self._mark_taint(conversation_id, "guest")
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
                # ``sender_id`` idzie do fabryki, bo akcje MUTUJĄCE (ADR 0065) wiążą się
                # z człowiekiem: bez rozpoznanego nadawcy nie ma komu przypisać zmiany ani
                # kogo zapytać o potwierdzenie, więc fabryka ich wtedy nie dokłada.
                # Klasa i skaza jadą do fabryki, bo sędzia mutacji (ADR 0065) ma orzekać
                # ze świadomością POCHODZENIA tury: „prośba padła w rozmowie, do której
                # weszła treść obca" to inny fakt niż ta sama prośba w rozmowie czystej.
                extra_tools.extend(
                    self._file_catalog_factory(
                        scope,
                        attachment_queue,
                        message.sender_id,
                        trust,
                        # LENIWA skaza: fabryka czyta ją w chwili MUTACJI, nie budowy katalogu.
                        # Wartość jest już poprawna w chwili budowy (zapalamy ją wyżej), więc to
                        # druga, niezależna warstwa — skaza z narzędzi tej samej tury (``Bash``,
                        # ``GitHub``) zapala się dopiero PO niej, a sędzia ma widzieć rozmowę
                        # taką, jaka jest w momencie orzekania.
                        lambda: self._is_tainted(conversation_id),
                        # Werdykt sędziego mutacji wraca TĄ drogą do wiersza audytu tego samego
                        # wywołania ``File`` (ADR 0065 §8) — bez audytu ujścia po prostu nie ma.
                        audit_recorder.record_verdict if audit_recorder is not None else None,
                    )
                )
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
        trust_nonce = self._trust_nonce(external_id)
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
                # Rozstrzygnięcie „ta tura MA powłokę" należy do tury, nie do składania drzwi
                # (ADR 0068 §2). Korpus statyczny i opisy narzędzi wybrano raz, z obecności
                # fabryki — a fabryka oddaje pustą listę nierozpoznanemu nadawcy (ADR 0063)
                # i degraduje przy błędzie budowy. Bez tego sprostowania gość czytał
                # o montażach `/mnt/system/…`, mając katalog bez `Bash` i bez narzędzi odczytu.
                shell_unavailable=self._shell_catalog_factory is not None
                and not any(spec.name == "Bash" for spec in extra_tools),
            ),
            audit=audit_recorder,
            attachment_queue=attachment_queue,
            trust_nonce=trust_nonce,
            trust=trust,
        )
        # Druga połowa skazy: to, co wiadomo dopiero PO turze — po co model sięgnął.
        self._mark_taint_from_tools(conversation_id, result)
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
        external_id: str,
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
            # Nonce tej ROZMOWY, nie tury — streszczacz ma dostać historię w kopercie
            # (ADR 0066), inaczej pierze treść obcą na prozę instrukcyjną.
            self._compaction.maybe_compact(
                conversation_id, trust_nonce=self._trust_nonce(external_id)
            )
        with self._store_lock:
            replay = self._conversations.replay_messages(conversation_id)
            summary = self._conversations.active_summary(conversation_id)
        return _to_transcript_with_summary(summary, replay)

    def _is_tainted(self, conversation_id: str) -> bool:
        """Czy do TEJ rozmowy weszła już treść obca (ADR 0066) — best-effort, fail-SAFE.

        Nieudany odczyt zwraca ``True``, nie ``False``: sędzia ma wtedy orzekać ostrożniej,
        a nie mniej ostrożnie. Domysł w drugą stronę byłby pocieszaniem się przy awarii bazy.
        """
        try:
            with self._store_lock:
                return self._conversations.is_tainted(conversation_id)
        except Exception:
            logger.warning("Nie odczytałem skazy rozmowy %r — zakładam skażoną", conversation_id)
            return True

    def _trust_nonce(self, external_id: str) -> str:
        """Nonce koperty (ADR 0066): stały w obrębie ROZMOWY, nieprzewidywalny z treści.

        Był losowany na turę i to psuło cache: żądanie niesie całą historię, więc gdyby każdy
        opakowany wynik narzędzia miał w kolejnej turze inne bajty, wspólny prefiks urywałby się
        na pierwszym z nich i praktycznie cały kontekst szedłby po pełnej cenie wejścia. Stały
        w rozmowie nonce zachowuje własność, która jest tu istotna — treść go nie zna, bo
        wywodzi się z sekretu wylosowanego przy starcie procesu, a nie z niczego, co czytamy.

        Cena: gdyby model wypisał znacznik w odpowiedzi na kanał, nonce byłby odtąd widoczny dla
        uczestników TEJ rozmowy. Przyjęte świadomie — ADR 0066 mówi wprost, że etykieta nie jest
        granicą bezpieczeństwa, więc płacenie za jej hartowanie kosztem każdej tury byłoby złym
        kursem. Sekret ginie z procesem, więc restart i tak odświeża wszystkie nonce.
        """
        if not self._trust_labels:
            return ""
        return hmac.new(
            self._nonce_secret, external_id.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:16]

    def _mark_taint(self, conversation_id: str, source: str) -> None:
        """Zapal skazę best-effort — nieudany zapis nie może zabrać użytkownikowi odpowiedzi.

        Idzie do logu, bo cicha utrata skazy to cicha utrata eskalacji.
        """
        try:
            with self._store_lock:
                self._conversations.mark_tainted(conversation_id, source)
        except Exception:
            logger.warning("Nie zapisałem skazy rozmowy %r (źródło %s)", conversation_id, source)

    def _mark_taint_from_tools(self, conversation_id: str, result: AgentResult) -> None:
        """Skaza z tego, po co model sięgnął w tej turze (znane dopiero PO turze).

        Zbiór wyzwalaczy jest wąski ROZMYŚLNIE (ADR 0066 R2): gdyby skaziło wszystko, sygnał
        nie znaczyłby nic. Odczyt notatek i zdarzeń własnego pionu typowanymi narzędziami NIE
        skaża — to treść zza bramek zdolności.
        """
        if any(
            call.name in _TAINTING_TOOLS
            for entry in result.entries
            if isinstance(entry, AssistantTurn)
            for call in entry.tool_calls
        ):
            self._mark_taint(conversation_id, "tool")

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
