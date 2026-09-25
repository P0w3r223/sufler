"""Entry point lokalnego harnessu runtime'u agenta: ``uv run sufler-agent``.

Zaufane lokalne drzwi (jak stdio dev): buduje katalog READ+WRITE nad tymi samymi
serwisami co MCP i wpina klient Claude API. Trzy tryby:

- ``uv run sufler-agent "pytanie"`` — jednorazowe zapytanie z argumentów.
- ``echo "pytanie" | uv run sufler-agent`` — jednorazowe zapytanie z potoku.
- ``uv run sufler-agent`` (w terminalu, bez argumentu) — INTERAKTYWNY CZAT z pamięcią
  rozmowy (bezstratna, ADR 0010/0011): piszesz, Claude odpowiada, w kółko.
- ``uv run sufler-agent --history [kanał]`` — PODGLĄD archiwum rozmów (wszystkie drzwi,
  ADR 0010): odczyt bazy SQLite, bez klucza API. Opcjonalny filtr kanału. Uwaga: pierwsze
  otwarcie ISTNIEJĄCEJ starej bazy dokona jednorazowej migracji schematu (zapis).

Bez Teams, bez Azure. Import Claude API jest leniwy — brak extra ``agent`` kończy się
czytelnym komunikatem, nie surowym ``ImportError``.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sufler.adapters.inbound import env
from sufler.adapters.inbound.agent_wiring import (
    build_agent_runtime_or_exit,
    build_conversational_responder,
)
from sufler.adapters.inbound.responder import InboundMessage
from sufler.adapters.outbound.sqlite_conversations import SqliteConversationStore
from sufler.config import AgentSettings, ConversationSettings, Settings
from sufler.core.agent.prompt import build_session_header
from sufler.core.application.conversations import ConversationService
from sufler.core.domain.pricing import cost_usd
from sufler.core.errors import LLMError, SuflerError

if TYPE_CHECKING:
    from sufler.core.agent.runtime import AgentRuntime
    from sufler.core.domain.conversation import (
        Conversation,
        ConversationMessage,
        ConversationSummary,
    )

_USAGE = 'Podaj zapytanie, np.: uv run sufler-agent "co ustalono z mpwik?"'
_EXIT_WORDS = ("exit", "quit", ":q", "wyjdz", "wyjdź")
_BANNER = (
    "Sufler — czat z Claude (pamięć rozmowy włączona, katalog read+write).\n"
    "Pisz i naciśnij Enter. Komendy: '/pomoc'. Nowy wątek: '/nowa'. "
    "Zakończ: Ctrl-D (Ctrl-Z+Enter na Windows) albo 'exit'.\n"
)


def main() -> None:
    """Odpal runtime: jednorazowe zapytanie (argv/potok), czat (TTY) albo podgląd historii.

    ``--history [kanał]`` to czysty ODCZYT archiwum rozmów (SQLite) — nie wymaga klucza
    API ani runtime, więc rozgałęziamy przed ich budową i walidacją sekretu.
    """
    env.force_utf8_io()
    env.load_dotenv()

    argv = sys.argv[1:]
    if argv and argv[0] in ("--history", "history"):
        _print_history(channel=argv[1] if len(argv) > 1 else None)
        return

    settings = Settings.from_env()
    agent_settings = AgentSettings.from_env()
    agent_settings.validate()

    argv_query = " ".join(argv).strip()
    if argv_query:
        runtime = build_agent_runtime_or_exit(settings, agent_settings, enable_write=True)
        _run_once(runtime, argv_query)
    elif not sys.stdin.isatty():
        # Wejście z potoku (nie-TTY) — jednorazowe zapytanie z stdin.
        piped = sys.stdin.read().strip()
        if not piped:
            raise SystemExit(_USAGE)
        runtime = build_agent_runtime_or_exit(settings, agent_settings, enable_write=True)
        _run_once(runtime, piped)
    else:
        # Terminal bez argumentu — interaktywny czat z pamięcią rozmowy.
        _run_chat(settings, agent_settings)


def _run_once(runtime: AgentRuntime, query: str) -> None:
    """Jednorazowe zapytanie: wypisz odpowiedź (bez pamięci między uruchomieniami)."""
    try:
        # Nagłówek sesji (ADR 0056) także tutaj: zapytanie jednorazowe równie dobrze może
        # dotyczyć „ostatniego tygodnia", a bez daty model odtwarza ją z cutoffu treningowego.
        print(runtime.run(query, session_header=build_session_header(datetime.now())))
    except LLMError as exc:
        # Błąd sieci/limitu/auth Claude API → czytelny komunikat, nie surowy traceback.
        raise SystemExit(f"Błąd komunikacji z Claude API: {exc}") from exc


def _run_chat(settings: Settings, agent_settings: AgentSettings) -> None:
    """Interaktywny czat: pętla wiadomość↔odpowiedź nad jedną, trwałą rozmową.

    Pamięć (SQLite, ADR 0010/0011) wątkuje kanał ``cli`` — rozmowa jest CIĄGŁA także
    między uruchomieniami (do rolloveru na limicie kontekstu albo kompaktowania, ADR 0014).
    Recepta ``ConversationalResponder`` (z komendami read-only i kompaktowaniem) składana jest
    wspólnym builderem — CLI to drzwi ZAUFANE (``enable_write=True``, ``show_thinking=True``),
    ale niewłożone w ``SafeResponder`` (błąd tury łapiemy lokalnie i kontynuujemy czat).
    """
    conv_settings = ConversationSettings.from_env()
    conv_settings.validate()
    responder = build_conversational_responder(
        settings,
        agent_settings,
        conv_settings,
        channel="cli",
        enable_write=True,
        safe=False,
        show_thinking=True,
    )

    print(_BANNER)
    while True:
        try:
            text = input("Ty> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nDo zobaczenia.")
            return
        if not text:
            continue
        if text.lower() in _EXIT_WORDS:
            print("Do zobaczenia.")
            return
        try:
            reply = asyncio.run(
                responder.respond(InboundMessage(text=text, sender="cli", conversation_id="cli"))
            )
        except SuflerError as exc:
            # Oczekiwany błąd (Claude API/repo/zapis) — pokaż i kontynuuj, nie wywracaj czatu.
            print(f"[błąd] {exc}\n")
            continue
        print(f"\n{reply}\n")


_ROLE_LABEL = {"user": "Ty", "assistant": "Claude", "tool": "narzędzie"}
_BODY_WIDTH = 400
# Sufit tur drukowanych na rozmowę: długa rozmowa (dziesiątki par assistant/tool,
# ADR 0011) nie zamienia podglądu w wielotysięczny zrzut. Pokazujemy OSTATNIE tury.
_MAX_TURNS_PER_CONV = 40


def _print_history(*, channel: str | None = None) -> None:
    """Wypisz archiwum rozmów agenta (wszystkie drzwi) — odczyt bazy SQLite.

    Wspólna baza rozmów jest niezależna od drzwi (ADR 0010): ten podgląd pokazuje
    rozmowy z CLI i Teams jednakowo (rozróżnia je ``channel``).
    Sam podgląd nie dopisuje rozmów, ale pierwsze otwarcie istniejącej starej bazy
    uruchamia jednorazową migrację schematu (dodanie kolumn/FK, ADR 0011/0012) — więc
    na bazie tylko-do-odczytu na dysku może się nie powieść. Świeżej bazy nie tworzy
    (patrz gałąź ``db_path.exists()`` niżej).
    """
    conv_settings = ConversationSettings.from_env()
    conv_settings.validate()
    # Podgląd jest ODCZYTEM: gdy bazy jeszcze nie ma (żadne drzwi nie rozmawiały),
    # nie twórz pliku ani schematu — po prostu zgłoś pusty stan. Bez tego samo otwarcie
    # store'a (mkdir + WAL + CREATE/ALTER) zostawiłoby pustą bazę po komendzie podglądu.
    if not conv_settings.db_path.exists():
        print(f"Brak zapisanych rozmów — baza jeszcze nie istnieje: {conv_settings.db_path}")
        return
    service = ConversationService(
        SqliteConversationStore(conv_settings.db_path),
        max_context_tokens=conv_settings.max_context_tokens,
    )
    conversations = service.list_conversations(channel=channel)
    if not conversations:
        scope = f" dla kanału {channel!r}" if channel else ""
        print(f"Brak zapisanych rozmów{scope}. Baza: {conv_settings.db_path}")
        return

    print(f"Historia rozmów — {len(conversations)} rozmów (baza: {conv_settings.db_path})\n")
    for conversation in conversations:
        _print_conversation(
            conversation,
            service.messages(conversation.id),
            service.active_summary(conversation.id),
        )


def _print_conversation(
    conversation: Conversation,
    messages: list[ConversationMessage],
    summary: ConversationSummary | None = None,
) -> None:
    # Realne tokeny + KOSZT (Design 2) z ``usage``; cennik wg dnia utworzenia rozmowy
    # (rozmowa jest krótka — rollover ją bramkuje — więc jednodniowy cennik wystarcza).
    cost = cost_usd(conversation.usage, on=conversation.created_at.date())
    print(
        f"━━ [{conversation.channel}] {conversation.external_id} · {conversation.status} · "
        f"{conversation.updated_at:%Y-%m-%d %H:%M} UTC · {conversation.usage.total_tokens} tok · "
        f"${cost:.4f} · id={conversation.id[:8]}"
    )
    # Podsumowanie kompaktowania (ADR 0014): zastępuje w kontekście tury oznaczone [zarch.].
    if summary is not None:
        archived = sum(1 for m in messages if m.archived)
        body = _shorten(" ".join(summary.summary.split()), 800)
        print(f"  ▤ podsumowanie ({archived} tur zarchiwizowanych): {body}")
    shown = messages
    if len(messages) > _MAX_TURNS_PER_CONV:
        omitted = len(messages) - _MAX_TURNS_PER_CONV
        print(f"  (… pominięto {omitted} wcześniejszych tur — ostatnie {_MAX_TURNS_PER_CONV})")
        shown = messages[-_MAX_TURNS_PER_CONV:]
    for message in shown:
        label = _ROLE_LABEL.get(message.role, message.role)
        mark = " [zarch.]" if message.archived else ""
        print(f"  {label}{mark}: {_format_body(message)}")
    print()


def _format_body(message: ConversationMessage) -> str:
    """Zwięzła, jednolinijkowa projekcja tury do podglądu (pełna treść jest w bazie)."""
    if message.role == "tool":
        return _format_tool(message)
    text = " ".join(message.text.split())
    if text:
        return _shorten(text)
    # Tura asystenta bez tekstu to zwykle samo wywołanie narzędzia (ADR 0011) —
    # pokaż które, zamiast mylącego „brak treści”.
    tools = _tool_calls(message.blocks) if message.role == "assistant" else []
    if tools:
        return f"(wywołuje narzędzia: {', '.join(tools)})"
    return "(brak treści)"


def _tool_calls(blocks: list[dict[str, Any]] | None) -> list[str]:
    """Nazwy narzędzi wywołanych w turze asystenta (bloki ``tool_use``, VERBATIM)."""
    if not blocks:
        return []
    return [b.get("name", "?") for b in blocks if b.get("type") == "tool_use"]


def _format_tool(message: ConversationMessage) -> str:
    """Podgląd tury narzędziowej: skrót każdego wyniku (tekst tury jest pusty, ADR 0011)."""
    if not message.blocks:
        return "(wynik narzędzia)"
    parts = []
    for block in message.blocks:
        content = " ".join(str(block.get("content", "")).split())
        flag = " [błąd]" if block.get("is_error") else ""
        parts.append(_shorten(content) + flag)
    return "→ " + " | ".join(parts)


def _shorten(text: str, width: int = _BODY_WIDTH) -> str:
    return text if len(text) <= width else text[:width] + " […]"


if __name__ == "__main__":
    main()
