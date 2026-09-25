"""Wiring dostawy do rozmówcy: plik w wątku, outbox, obraz i dokument 1:1, PDF wątku."""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape
from typing import TYPE_CHECKING

from sufler.config import (
    TeamsGraphSettings,
)

if TYPE_CHECKING:
    from sufler.core.application.tools import ToolSpec
    from sufler.core.ports.outbox import Deliverable

from sufler.adapters.inbound.teams_graph.wiring_common import _MISSING_TEAMS_GRAPH

logger = logging.getLogger(__name__)


def _build_file_reply_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``ReplyWithFile`` per turę (ADR 0026, A′2) — odpowiedź plikiem w wątku Teams.

    ``None``, gdy bramka ``enable_file_reply`` wyłączona (domyślnie, ADR 0006). Włączona: buduje
    SYNCHRONICZNY ``HttpxGraphFileSender`` (jak zapis GitHub Gate-4 — narzędzia agenta biegną
    synchronicznie w puli wątków) na TYM SAMYM delegowanym tokenie co poller, oraz renderer
    dokumentów. Cel dostawy (``team/channel/root``) wyłuskujemy z ``external_id`` wątku, NIE od
    modelu — plik ląduje wyłącznie w wątku bieżącej rozmowy (kontrola kompensująca, ADR 0026).
    """
    if not settings.enable_file_reply:
        return None
    try:
        import atexit

        import httpx

        from sufler.adapters.outbound.document_renderer import DefaultDocumentRenderer
        from sufler.adapters.outbound.graph_file_sender import HttpxGraphFileSender
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.core.application.tools import build_file_reply_catalog

    # Sync klient żyje przez proces (daemon); pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphFileSender(transport, token_provider)
    renderer = DefaultDocumentRenderer()
    max_bytes = settings.max_file_reply_kb * 1024
    logger.info(
        "Odpowiedź plikiem WŁĄCZONA (ADR 0026) — agent Teams może załączać md/txt/pdf/docx w "
        "wątku. Wymaga zakresu 'Files.ReadWrite.All' na tokenie; limit pliku %d KB.",
        settings.max_file_reply_kb,
    )

    def factory(external_id: str) -> list[ToolSpec]:
        parts = external_id.split("/")
        if len(parts) != 3:
            return []
        team_id, channel_id, root_id = parts
        return build_file_reply_catalog(
            sender, renderer, team_id, channel_id, root_id, max_bytes=max_bytes
        )

    return factory


def _build_outbox_send_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], Callable[[Deliverable], None] | None] | None:
    """Fabryka WYSYŁACZA skrzynki nadawczej rozmowy (ADR 0009 paczki) — plik z ``outputs/`` w wątek.

    ``None``, gdy bramka ``enable_file_reply`` wyłączona: to ta sama zdolność co ``ReplyWithFile``
    (ADR 0026) — załącznik w wątku Teams — więc dzieli z nią bramkę i limit rozmiaru. Osobna byłaby
    obietnicą, że operator włączył jedno, a dostał dwa.

    Cel dostawy (``team/channel/root``) wyłuskujemy z ``external_id`` wątku, NIE od modelu — plik
    trafia wyłącznie do wątku bieżącej rozmowy (kontrola kompensująca, ADR 0026 §Threat model).
    Wątek o innym kształcie ``external_id`` daje ``None``: nie ma dokąd wysłać, więc skrzynka
    zostaje nietknięta zamiast zostać opróżniona w próżnię.
    """
    if not settings.enable_file_reply:
        return None
    try:
        import atexit

        import httpx

        from sufler.adapters.outbound.graph_file_sender import HttpxGraphFileSender
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.core.errors import ThreadRootGone
    from sufler.core.ports.outbox import PermanentDeliveryError

    # Timeout ROZPISANY na fazy, nie skalarem: `httpx` rozdziela go i tak na connect/read/write,
    # a skalar 30 s znaczy tu 30 s NA KAŻDĄ z nich. Zapis 512 KB (sufit `max_file_reply_kb`)
    # potrzebuje więcej niż połączenie, więc `write` jest hojniejszy niż `connect`.
    transport = httpx.Client(timeout=httpx.Timeout(connect=5, read=15, write=15, pool=5))
    atexit.register(transport.close)
    # Bez ponawiania 5xx/timeoutów — skrzynka MA WŁASNĄ pętlę ponowień (plik zostaje, następna
    # tura próbuje, licznik prób kończy po trzeciej). Druga warstwa retry mnożyłaby najgorszy
    # przypadek czterokrotnie, a to opóźnienie płaci rozmówca czekający na odpowiedź tury.
    sender = HttpxGraphFileSender(transport, token_provider, retry_transient=False)

    def factory(external_id: str) -> Callable[[Deliverable], None] | None:
        parts = external_id.split("/")
        if len(parts) != 3:
            return None
        team_id, channel_id, root_id = parts

        def send(item: Deliverable) -> None:
            try:
                uploaded = sender.upload_channel_file(
                    team_id, channel_id, item.name, item.content, item.content_type
                )
                sender.post_reply_with_attachment(
                    team_id, channel_id, root_id, _outbox_html(uploaded.name), uploaded
                )
            except ThreadRootGone as exc:
                raise PermanentDeliveryError("wątek tej rozmowy już nie istnieje") from exc
            except httpx.HTTPStatusError as exc:
                # 4xx (poza 429) znaczy „Graph tego pliku nie przyjmie" — nazwa odrzucona przez
                # SharePoint, kanał bez folderu plików, brak zakresu. Ponowienie da to samo,
                # a plik zostawiony w skrzynce doklejałby „spróbuję ponownie" do KAŻDEJ kolejnej
                # odpowiedzi w tej rozmowie, płacąc przy tym dwa żądania Graph za turę.
                status = exc.response.status_code
                if 400 <= status < 500 and status != 429:
                    raise PermanentDeliveryError(f"Graph odrzucił plik (HTTP {status})") from exc
                raise
            except RuntimeError as exc:
                # `graph_file_sender._require` podnosi `RuntimeError` na odpowiedzi 200
                # z NIEPEŁNYM payloadem — kanał bez `filesFolder.driveId` (brak dysku plików)
                # albo `eTag` bez GUID-a. To własność KANAŁU i odpowiedzi, nie chwili: ponowienie
                # da to samo. Bez tej gałęzi plik zostawałby w skrzynce na zawsze, a każda tura
                # płaciłaby dwa żądania i doklejała „spróbuję ponownie" — czyli dokładnie zatrutą
                # wiadomość, której reguła sprzątania ma unikać.
                raise PermanentDeliveryError(f"kanał nie przyjmuje plików ({exc})") from exc

        return send

    return factory


def _outbox_html(filename: str) -> str:
    """Zaufany, ESCAPOWANY HTML wiadomości niosącej załącznik ze skrzynki (składany u nas)."""
    return f"<p>{escape(filename)}</p>"


def _build_user_push_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``SendImage`` per turę (ADR 0027, A′3) — push obrazu do rozmówcy 1:1.

    ``None``, gdy bramka ``enable_user_file_push`` wyłączona (domyślnie, ADR 0006). Włączona: buduje
    SYNCHRONICZNY ``HttpxGraphUserImagePush`` (jak plik ADR 0026 — narzędzia agenta biegną
    synchronicznie w puli wątków) na TYM SAMYM delegowanym tokenie co poller. Cel (odbiorca) NIE
    pochodzi z ``external_id`` wątku, lecz z ``sender_id`` bieżącej wiadomości — dlatego to OSOBNA
    fabryka (klucz = nadawca), nie składana z fabrykami wątkowymi. Obraz idzie INLINE
    (hostedContents), bez dysku SharePoint, więc bez zakresu ``Files.*`` — ale 1:1 wymaga czatu.
    """
    if not settings.enable_user_file_push:
        return None
    try:
        import atexit

        import httpx

        from sufler.adapters.outbound.graph_user_push import HttpxGraphUserImagePush
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.core.application.tools import build_user_image_push_catalog

    # Sync klient żyje przez proces (daemon); pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphUserImagePush(transport, token_provider)
    max_bytes = settings.max_user_image_kb * 1024
    logger.info(
        "Push obrazu do usera WŁĄCZONY (ADR 0027) — agent Teams może odesłać obraz rozmówcy 1:1. "
        "Wymaga zakresów czatu (Chat.Create/ChatMessage.Send) na tokenie; limit obrazu %d KB.",
        settings.max_user_image_kb,
    )

    def factory(sender_id: str) -> list[ToolSpec]:
        if not sender_id:
            return []
        return build_user_image_push_catalog(sender, sender_id, max_bytes=max_bytes)

    return factory


def _build_user_doc_push_factory(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str], list[ToolSpec]] | None:
    """Fabryka ``SendDocument`` per turę (ADR 0027, wariant plikowy) — push pliku 1:1.

    ``None``, gdy bramka ``enable_user_doc_push`` wyłączona (domyślnie, ADR 0006). Włączona: buduje
    SYNCHRONICZNY ``HttpxGraphUserDocPush`` (jak plik ADR 0026 — narzędzia agenta biegną
    synchronicznie w puli wątków) na TYM SAMYM delegowanym tokenie co poller, oraz renderer
    dokumentów. Cel (odbiorca) pochodzi z ``sender_id`` bieżącej wiadomości (jak wariant obrazowy),
    NIE od modelu. W odróżnieniu od obrazu plik ląduje na OneDrive bota → wymaga zakresu
    ``Files.ReadWrite.All`` obok zakresów czatu (walidacja fail-fast w ``config``).
    """
    if not settings.enable_user_doc_push:
        return None
    try:
        import atexit

        import httpx

        from sufler.adapters.outbound.document_renderer import DefaultDocumentRenderer
        from sufler.adapters.outbound.graph_user_doc_push import HttpxGraphUserDocPush
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.core.application.tools import build_user_doc_push_catalog

    # Sync klient żyje przez proces (daemon); pulę połączeń domykamy jawnie przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphUserDocPush(transport, token_provider)
    renderer = DefaultDocumentRenderer()
    max_bytes = settings.max_user_doc_kb * 1024
    logger.info(
        "Push dokumentu do usera WŁĄCZONY (ADR 0027) — agent Teams może odesłać plik (md/txt/pdf/"
        "docx) rozmówcy 1:1. Wymaga zakresów czatu ORAZ 'Files.ReadWrite.All' na tokenie; limit "
        "pliku %d KB.",
        settings.max_user_doc_kb,
    )

    def factory(sender_id: str) -> list[ToolSpec]:
        if not sender_id:
            return []
        return build_user_doc_push_catalog(sender, renderer, sender_id, max_bytes=max_bytes)

    return factory


def _build_thread_pdf_delivery(
    settings: TeamsGraphSettings, token_provider: Callable[[], str]
) -> Callable[[str, str, str], None] | None:
    """Współdzielone zamknięcie dostawy PLIKIEM PDF w wątku (ADR 0026) — brief (F4) i digest (F5).

    Reużywa kanał file-reply: renderer dokumentów + ``HttpxGraphFileSender`` na TYM SAMYM
    delegowanym tokenie co poller. Aktywne TYLKO przy włączonej bramce ``enable_file_reply`` (zakres
    ``Files.ReadWrite.All`` i sender) — inaczej ``None`` i ``| pdf`` degraduje do tekstu. Cel
    (``team/channel/root``) wyłuskujemy z ZAUFANEGO ``external_id`` wątku, NIE od modelu.
    """
    if not settings.enable_file_reply:
        return None
    try:
        import atexit

        import httpx

        from sufler.adapters.outbound.document_renderer import DefaultDocumentRenderer
        from sufler.adapters.outbound.graph_file_sender import HttpxGraphFileSender
    except ImportError as exc:
        raise SystemExit(_MISSING_TEAMS_GRAPH) from exc
    from sufler.core.application.tools import build_file_reply_catalog

    # Sync klient żyje przez proces (jak inne sync sendery tu); pulę domykamy przy wyjściu.
    transport = httpx.Client(timeout=30)
    atexit.register(transport.close)
    sender = HttpxGraphFileSender(transport, token_provider)
    renderer = DefaultDocumentRenderer()
    max_bytes = settings.max_file_reply_kb * 1024

    def deliver(external_id: str, base_name: str, content: str) -> None:
        parts = external_id.split("/")
        if len(parts) != 3:
            raise ValueError(f"zły external_id wątku (team/channel/root): {external_id!r}")
        team_id, channel_id, root_id = parts
        # Reuse JEDNOŹRÓDŁOWEGO pipeline'u file-reply (ADR 0026, reguła 6): render → walidacja →
        # ``_safe_doc_name`` (hardening nazwy) → upload → post. Bez duplikacji sekwencji tutaj.
        (spec,) = build_file_reply_catalog(
            sender, renderer, team_id, channel_id, root_id, max_bytes=max_bytes
        )
        result = spec.fn(content, "pdf", base_name)
        if "error" in result:
            # Błąd oczekiwany (zły format/za duży/ThreadRootGone) → wywal, router zdegraduje do
            # tekstu. Twarde awarie infrastruktury pipeline PUSZCZA wyżej (SafeResponder je złapie).
            raise RuntimeError(str(result["error"]))

    return deliver


def _compose_user_push_factories(
    *factories: Callable[[str], list[ToolSpec]] | None,
) -> Callable[[str], list[ToolSpec]] | None:
    """Złóż fabryki push-u 1:1 (obraz + dokument) w jedną; ``None`` gdy żadna bramka nie jest ON.

    Obie kluczowane ``sender_id`` (odbiorca = nadawca bieżącej wiadomości, ADR 0027) i niezależnie
    bramkowane, a responder przyjmuje JEDNĄ ``user_push_tool_factory`` — łączymy je konkatenacją
    wyników, by obie zdolności współistniały bez zmiany kontraktu respondera (jak
    ``_compose_thread_factories`` dla narzędzi wątkowych).
    """
    active = [f for f in factories if f is not None]
    if not active:
        return None
    if len(active) == 1:
        return active[0]

    def combined(sender_id: str) -> list[ToolSpec]:
        tools: list[ToolSpec] = []
        for factory in active:
            tools.extend(factory(sender_id))
        return tools

    return combined
