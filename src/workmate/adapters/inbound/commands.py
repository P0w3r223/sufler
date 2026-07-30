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

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from workmate.core.application.conversations import ConversationService
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.conversation import Conversation

# Odpowiedzi komendy startu wątku (przeniesione z respondera — należą do handlera ``/nowa``).
_NEW_THREAD_ACK = "Zaczynam nową rozmowę. Poprzednia została zapisana w archiwum."
_NEW_THREAD_ALREADY_FRESH = "Jesteś już w nowej, pustej rozmowie — nie ma czego rozdzielać."

# Ile ostatnich rozmów pokazać w ``/historia``.
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

    def _search(self, args: str, ctx: CommandContext) -> str:
        if not args:
            return "Użycie: /szukaj <fraza> — np. /szukaj integracja SCADA"
        return _format_search(self._tools["search_notes"](query=args))

    def _projects(self, args: str, ctx: CommandContext) -> str:
        return _format_projects(self._tools["list_projects"]())

    def _status(self, args: str, ctx: CommandContext) -> str:
        if args:
            return _format_project_status(self._tools["get_project_status"](project=args))
        conv = self._conversations.active_conversation(ctx.channel, ctx.external_id)
        return self._thread_status(conv)

    def _history(self, args: str, ctx: CommandContext) -> str:
        conversations = self._conversations.list_conversations(channel=ctx.channel)
        if not conversations:
            return "Brak zapisanych rozmów."
        lines = ["Ostatnie rozmowy:"]
        for c in conversations[:_HISTORY_LIMIT]:
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
        return _format_my_tasks(tools[0].fn())

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


def _format_my_tasks(data: dict[str, Any]) -> str:
    if "error" in data:
        return f"Błąd: {data['error']}"
    tasks = data.get("tasks", [])
    if not tasks:
        return "Nie masz otwartych zadań w Jirze."
    lines = [f"Twoje otwarte zadania ({len(tasks)}):"]
    for t in tasks:
        priority = f" [{t['priority']}]" if t.get("priority") else ""
        due = f" · termin {t['due_date']}" if t.get("due_date") else ""
        lines.append(f"• {t['key']}{priority} — {t['summary']} ({t['status']}){due}")
        if t.get("url"):
            lines.append(f"  {t['url']}")
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
