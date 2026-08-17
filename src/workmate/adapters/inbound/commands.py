"""Dispatcher prostych komend read-only w szwie drzwi (styl Claude Code).

Komendy (``/pomoc``, ``/nowa``, ``/szukaj``, ``/projekty``, ``/status``, ``/historia``) są
rozpoznawane po PIERWSZYM tokenie z ukośnikiem — jednakowo na WSZYSTKICH drzwiach, bo router
żyje tu, we wspólnym szwie ``adapters/inbound/`` (a nie w kodzie pojedynczych drzwi). Reguła
``core ↛ adapters`` stoi: router woła serwisy/narzędzia rdzenia, rdzeń o komendach nie wie.

Komenda ≠ tura rozmowy: ``ConversationalResponder`` wykonuje dispatch PRZED pętlą agenta i robi
wczesny return, więc komenda nie jest zapisywana do pamięci ani do FTS i nie liczy się do limitu
kontekstu. Wszystkie komendy są READ-ONLY — nie omijają bramki zapisu (ADR 0006); router dostaje
wyłącznie narzędzia odczytu. Argumenty komend to dane wejściowe (np. ``/szukaj`` → parametryzowany
FTS), nie polecenia.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from workmate.core.errors import NoteAuthorizationError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from workmate.core.application.conversations import ConversationService
    from workmate.core.application.note_read_authz import NoteReadAuthorizer
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.conversation import Conversation

# Odpowiedzi komendy startu wątku (przeniesione z respondera — należą do handlera ``/nowa``).
_NEW_THREAD_ACK = "Zaczynam nową rozmowę. Poprzednia została zapisana w archiwum."
_NEW_THREAD_ALREADY_FRESH = "Jesteś już w nowej, pustej rozmowie — nie ma czego rozdzielać."

# Ile ostatnich rozmów pokazać w ``/historia``. Idzie wprost do magazynu jako ``limit``, bo
# zawężenie po wątku robi już zapytanie — nie ma czego odsiewać po fakcie.
_HISTORY_LIMIT = 10


@dataclass(frozen=True)
class CommandSpec:
    """Definicja komendy: tokeny (pierwszy = kanoniczny, reszta to aliasy) + opis do ``/pomoc``."""

    tokens: tuple[str, ...]
    summary: str


# JEDNO ŹRÓDŁO komend: zasila ``/pomoc`` i wiązanie handlerów.
COMMAND_SPECS: tuple[CommandSpec, ...] = (
    CommandSpec(("/pomoc", "/help"), "Lista dostępnych komend."),
    CommandSpec(("/nowa", "/nowy", "/new"), "Rozpocznij nowy wątek rozmowy."),
    CommandSpec(("/szukaj",), "Wyszukaj notatki: /szukaj <fraza>."),
    CommandSpec(("/projekty",), "Lista projektów pionu."),
    CommandSpec(("/status",), "Status projektu: /status [projekt] (bez arg — bieżący wątek)."),
    CommandSpec(("/historia",), "Ostatnie rozmowy z archiwum."),
    CommandSpec(("/moje-zadania", "/zadania"), "Twoje otwarte zadania z Jiry (ADR 0054)."),
)


@dataclass(frozen=True)
class CommandContext:
    """Kontekst wykonania komendy: kanał drzwi i identyfikator rozmowy (klucz pamięci wątku).

    ``sender_id`` (AAD id nadawcy, addytywne, domyślnie puste) niesie TOŻSAMOŚĆ do autoryzacji
    zapisu (``/notatka``, B2 / ADR 0042) ORAZ do odczytu zawężonego do nadawcy (``/moje-zadania``,
    ADR 0054). Większość komend odczytu go ignoruje — pole jest tu, bo wszystkie routery dzielą ten
    sam kontekst szwu drzwi.
    """

    channel: str
    external_id: str
    sender_id: str = ""


class CommandRouter:
    """Rozpoznaje i wykonuje komendy read-only; ``dispatch`` zwraca odpowiedź albo ``None``.

    ``read_tools`` to mapa ``nazwa → fn`` z katalogu narzędzi ODCZYTU (search_notes/list_projects/
    get_project_status) — wołane bezpośrednio, z pominięciem pętli agenta. ``None`` z ``dispatch``
    znaczy „to nie komenda, obsłuż jako normalną turę".
    """

    def __init__(
        self,
        conversations: ConversationService,
        read_tools: Mapping[str, Callable[..., dict[str, Any]]],
        *,
        supports_attachments: bool = False,
        my_jira_tasks: Callable[[str], Sequence[ToolSpec]] | None = None,
        note_read_authorizer: NoteReadAuthorizer | None = None,
    ) -> None:
        self._conversations = conversations
        self._tools = read_tools
        # F8: przykład o załącznikach w /pomoc tylko na drzwiach, które je materializują
        # (teams-graph) — na drzwiach tekstowych byłby mylną obietnicą.
        self._supports_attachments = supports_attachments
        # Fabryka narzędzia "moje zadania" (ADR 0054), PER NADAWCA (jak thread/user-push factory)
        # — ``None`` gdy Jira/tożsamość nie są skonfigurowane na tych drzwiach (komenda odpowiada
        # czytelną odmową zamiast crashować). Zwraca gotowy ``ToolSpec``, którego ``fn()`` router
        # woła bezpośrednio — ta sama fabryka zasila per-turowy katalog agenta.
        self._my_jira_tasks = my_jira_tasks
        # Autoryzacja ODCZYTU bazy wiedzy (ADR 0062), bramka członkostwa nadawcy — ``None`` gdy
        # bramka wyłączona / inne drzwi (wtedy komendy odczytu jak dawniej). Egzekwowana w
        # ``/szukaj`` i ``/projekty`` (czytają katalog notatek pionu); ``/status`` idzie przez
        # ``get_project_status`` (poza katalogiem ODCZYTU z ADR 0062 — kandydat na kolejny etap).
        self._note_read_authorizer = note_read_authorizer
        handlers = {
            "/pomoc": self._help,
            "/nowa": self._new_thread,
            "/szukaj": self._search,
            "/projekty": self._projects,
            "/status": self._status,
            "/historia": self._history,
            "/moje-zadania": self._my_tasks,
        }
        # Rozwiń aliasy z rejestru; KeyError = rejestr i handlery się rozjechały (guard w testach).
        self._by_token = {
            token: handlers[spec.tokens[0]] for spec in COMMAND_SPECS for token in spec.tokens
        }

    def dispatch(self, text: str, ctx: CommandContext) -> str | None:
        """Wykonaj komendę, jeśli tekst nią jest; inaczej ``None`` (idzie do normalnej tury)."""
        stripped = text.strip()
        if not stripped:
            return None
        parts = stripped.split(None, 1)
        # Pierwszy token; obcięcie sufiksu ``@bot`` (konwencja komend grupowych); lowercase.
        token = parts[0].split("@", 1)[0].lower()
        handler = self._by_token.get(token)
        if handler is None:
            return None  # nieznany ukośnik / nie-komenda → normalna tura (bez porywania)
        args = parts[1].strip() if len(parts) > 1 else ""  # argumenty ZACHOWANE
        return handler(args, ctx)

    # --- handlery (formatowanie dict → tekst to warstwa adaptera) ---------------

    def _help(self, args: str, ctx: CommandContext) -> str:
        lines = [
            "Jestem WorkMate — wspólna baza wiedzy pionu (notatki ze spotkań i status "
            "projektów). Zapytaj mnie zwykłym zdaniem albo użyj komendy.",
            "",
            "Dostępne komendy:",
        ]
        lines += [f"{' / '.join(spec.tokens)} — {spec.summary}" for spec in COMMAND_SPECS]
        lines += [
            "",
            "Przykłady pytań:",
            "• czy robiliśmy już integrację SCADA?",
            "• jaki jest status projektu smart-metering?",
            "• co ustaliliśmy na ostatnim spotkaniu w omnichannel?",
        ]
        if self._supports_attachments:
            lines.append("• wrzuć zrzut ekranu HMI lub PDF specyfikacji i zapytaj o jego treść")
        return "\n".join(lines)

    def _new_thread(self, args: str, ctx: CommandContext) -> str:
        started = self._conversations.start_new_thread(ctx.channel, ctx.external_id)
        return _NEW_THREAD_ACK if started else _NEW_THREAD_ALREADY_FRESH

    def _read_authz_refusal(self, ctx: CommandContext) -> str | None:
        """Odmowa odczytu bazy wiedzy (bramka członkostwa, ADR 0062) albo ``None``.

        ``None`` znaczy „wolno" — także gdy authorizera nie ma (bramka wyłączona / inne drzwi),
        więc komendy odczytu zachowują się jak przed ADR 0062. Fail-closed: nierozpoznany nadawca
        (w tym pusty ``sender_id``) → czytelna odmowa zamiast wyniku.
        """
        if self._note_read_authorizer is None:
            return None
        try:
            self._note_read_authorizer.authorize(ctx.sender_id)
        except NoteAuthorizationError as exc:
            return f"Brak uprawnień do odczytu bazy wiedzy: {exc}"
        return None

    def _search(self, args: str, ctx: CommandContext) -> str:
        if not args:
            return "Użycie: /szukaj <fraza> — np. /szukaj integracja SCADA"
        refusal = self._read_authz_refusal(ctx)
        if refusal is not None:
            return refusal
        return _format_search(self._tools["search_notes"](query=args))

    def _projects(self, args: str, ctx: CommandContext) -> str:
        refusal = self._read_authz_refusal(ctx)
        if refusal is not None:
            return refusal
        return _format_projects(self._tools["list_projects"]())

    def _status(self, args: str, ctx: CommandContext) -> str:
        if args:
            # Bramka odczytu (ADR 0062) także TUTAJ: ``get_project_status`` zwraca syntezę stanu
            # projektu złożoną z notatek pionu (streszczenie, liczba notatek, otwarte action items),
            # więc jest tą samą treścią co ``/szukaj``. ``_status`` był jedynym handlerem odczytu
            # bez tego sprawdzenia — czyli drogą OBOK bramki, na tych samych drzwiach.
            refusal = self._read_authz_refusal(ctx)
            if refusal is not None:
                return refusal
            return _format_project_status(self._tools["get_project_status"](project=args))
        conv = self._conversations.active_conversation(ctx.channel, ctx.external_id)
        return self._thread_status(conv)

    def _history(self, args: str, ctx: CommandContext) -> str:
        # Zawężone do TEGO wątku, nie do całego kanału, i zawężone W ZAPYTANIU. Bez ``external_id``
        # magazyn zwracał rozmowy WSZYSTKICH wątków kanału, więc ``/historia`` w wątku A pokazywała
        # metadane wątku B (kiedy, ile tur, ile tokenów) — treści nie, ale sam fakt i rozmiar
        # cudzej rozmowy to informacja, której uczestnik tego wątku nie miał prawa dostać.
        #
        # Filtr MUSI iść do magazynu, a nie za nim: ``limit`` przycina PO filtrach, więc odsiewanie
        # w Pythonie kazałoby policzyć koszt rozmów, które zaraz odpadną, a przy okazji myliło
        # „wątek bez historii" z „historia wypadła poza okno". Puste znaczy tu jedno.
        conversations = self._conversations.list_conversations(
            channel=ctx.channel, external_id=ctx.external_id, limit=_HISTORY_LIMIT
        )
        if not conversations:
            return "Brak zapisanych rozmów."
        lines = ["Ostatnie rozmowy:"]
        for c in conversations:
            when = c.updated_at.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"• {when} · {c.status} · {c.message_count} tur · {c.usage.total_tokens} tok"
            )
        return "\n".join(lines)

    def _my_tasks(self, args: str, ctx: CommandContext) -> str:
        if self._my_jira_tasks is None:
            return "Ta komenda nie jest skonfigurowana na tych drzwiach."
        tools = self._my_jira_tasks(ctx.sender_id)
        if not tools:
            return (
                "Nie udało się ustalić Twojego konta Jira — zgłoś się do administratora "
                "(fail-closed, ADR 0054)."
            )
        # Router jest DRUGIM konsumentem tej fabryki, obok runtime'u agenta — i konsumentem
        # NIE-modelowym, więc żadna sonda na `input_schema` ani golden-test powierzchni go nie
        # widzi. Krok 5.3 (ADR 0009 paczki) zmienił tu trzy rzeczy naraz: nazwę narzędzia
        # (`get_my_jira_tasks` → `Jira`), sposób wywołania (doszła wymagana `action`) i klucze
        # wyniku. Sonda kontraktowa na ten szew, zbudowana z PRAWDZIWEGO `build_jira_catalog`,
        # jest w `test_commands.py` — atrapy po obu stronach przepuściły tę regresję w całości.
        tool = next((t for t in tools if t.name == "Jira"), None)
        if tool is None:
            return "Ta komenda nie jest skonfigurowana na tych drzwiach."
        return _format_my_tasks(tool.fn(action="my_tasks"))

    def _thread_status(self, conv: Conversation | None) -> str:
        if conv is None or conv.message_count == 0:
            return "Brak aktywnego wątku. Napisz coś, aby zacząć rozmowę."
        parts = [f"Bieżący wątek: {conv.message_count} tur."]
        if self._conversations.active_summary(conv.id) is not None:
            parts.append("Starsza część rozmowy jest streszczona (kompaktowanie aktywne).")
        parts.append("Nowy wątek: /nowa · status projektu: /status <projekt>")
        return "\n".join(parts)


def _format_search(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"Błąd wyszukiwania: {data['error']}"
    results = data.get("results", [])
    if not results:
        return "Brak notatek pasujących do zapytania."
    lines = [f"Znaleziono {data.get('count', len(results))}:"]
    for r in results:
        lines.append(f"• {r['date']} [{r['project']}] {r['title']}")
        snippet = str(r.get("snippet", "")).strip()
        if snippet:
            lines.append(f"  {snippet}")
        lines.append(f"  id: {r['id']}")
    return "\n".join(lines)


def _format_projects(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"Błąd: {data['error']}"
    projects = data.get("projects", [])
    if not projects:
        return "Brak projektów w rejestrze."
    lines = ["Projekty:"]
    for p in projects:
        company = f" ({p['company']})" if p.get("company") else ""
        lines.append(f"• {p['key']}{company} — {p['name']}")
    return "\n".join(lines)


def _task_lines(tasks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for t in tasks:
        priority = f" [{t['priority']}]" if t.get("priority") else ""
        due = f" · termin {t['due_date']}" if t.get("due_date") else ""
        lines.append(f"• {t['key']}{priority} — {t['summary']} ({t['status']}){due}")
        if t.get("url"):
            lines.append(f"  {t['url']}")
    return lines


def _format_my_tasks(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"Błąd: {data['error']}"
    # Klucze wspólne dla „moich" i „cudzych" zadań (krok 5.3 ADR 0009 paczki je ujednolicił).
    # Pomyłka w nazwie klucza NIE daje tu błędu, tylko ciche „nie masz otwartych zadań" —
    # najgorszy możliwy tryb awarii, bo wygląda jak poprawna odpowiedź. Stąd sonda na realnym
    # builderze zamiast atrapy zwracającej wymyślony kształt.
    assigned = data.get("assigned", [])
    unassigned = data.get("reported_unassigned", [])
    if not assigned and not unassigned:
        return "Nie masz otwartych zadań w Jirze."
    lines: list[str] = []
    if assigned:
        lines.append(f"Twoje otwarte zadania ({len(assigned)}):")
        lines.extend(_task_lines(assigned))
    if unassigned:
        if lines:
            lines.append("")
        lines.append(f"Zgłoszone przez Ciebie, nieprzypisane do nikogo ({len(unassigned)}):")
        lines.extend(_task_lines(unassigned))
    return "\n".join(lines)


def _format_project_status(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"Błąd: {data['error']}"
    return "\n".join(
        [
            f"[{data['key']}] {data['name']} — {data['status']} / {data['health']} "
            f"/ faza: {data['phase']}",
            str(data["summary"]),
            f"Notatki: {data['notes_count']} · otwarte action items: {data['open_action_items']}",
        ]
    )
