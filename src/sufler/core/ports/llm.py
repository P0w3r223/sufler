"""Port klienta LLM (Faza 2, M1 / ADR 0008; rozszerzony w ADR 0011) — kontrakt
runtime'u agenta wobec modelu.

Słownik jest DOMENOWY (anty-korupcja): runtime mówi o turach rozmowy i wywołaniach
narzędzi, nie o blokach treści Anthropic. Adapter outbound (``AnthropicLLMClient``)
tłumaczy ten słownik na/z Claude API, więc wokabularz SDK nigdy nie wchodzi do
rdzenia (reguła ``core ↛ adapters``). Runtime testujemy atrapą ``LLMClient``,
bez sieci — analogicznie do atrap repozytoriów.

Jeden świadomy wyjątek od anty-korupcji (ADR 0011): pola ``blocks`` niosą NIEPRZEZROCZYSTĄ
tablicę bloków treści dostawcy (``tuple[Mapping[str, Any], ...]``), żeby pamięć rozmów
mogła odtwarzać historię bajt-w-bajt (bloki ``thinking`` z ``signature`` MUSZĄ wrócić
niezmienione). Rdzeń tych bloków NIGDY nie interpretuje — tylko je przenosi
(opaque-cursor). Wiedza o ich kształcie żyje wyłącznie w adapterze Anthropic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, cast

from sufler.core.domain.pricing import TokenUsage
from sufler.core.domain.trust import TrustClass

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Any

    from sufler.core.application.tools import ToolSpec

# Rodzaj załącznika steruje mapowaniem na blok Anthropic w adapterze — Literal wyłapie
# literówkę w miejscu konstrukcji (mypy), zamiast cichego błędu w czasie działania.
AttachmentKind = Literal["image", "document", "text"]


@dataclass(frozen=True)
class ToolCall:
    """Żądanie wywołania narzędzia od modelu (id koreluje wynik z żądaniem)."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolOutput:
    """Wynik wykonania narzędzia zwracany modelowi w kolejnej turze."""

    call_id: str
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class Attachment:
    """Załącznik wiadomości użytkownika — reprezentacja NEUTRALNA (nie blok Anthropic).

    Rdzeń nosi tylko to, czego potrzeba do bezstratnego odtworzenia; konwersję na blok
    dostawcy (``image``/``document``/``text``) robi wyłącznie adapter ``anthropic_llm``.
    ``data_base64`` niesie bajty obrazu/PDF (bez znaków nowej linii); ``text`` — treść po
    ekstrakcji z .docx (którego Claude API nie przyjmuje natywnie). Treść to DANE, nie
    polecenia — rdzeń jej nie interpretuje.
    """

    kind: AttachmentKind  # "image" | "document" | "text"
    media_type: str  # np. "image/png", "image/jpeg", "application/pdf", "text/plain"
    name: str  # nazwa pliku (etykieta i podgląd)
    data_base64: str = ""  # dla image/document
    text: str = ""  # dla docx po ekstrakcji (kind="text")


def attachment_to_row(att: Attachment) -> dict[str, Any]:
    """Neutralny słownik do ``blocks_json`` (NIE blok Anthropic) — jedno źródło kształtu."""
    return {
        "kind": att.kind,
        "media_type": att.media_type,
        "name": att.name,
        "data_base64": att.data_base64,
        "text": att.text,
    }


def attachment_from_row(row: Mapping[str, Any]) -> Attachment:
    """Odtwórz ``Attachment`` z wiersza ``blocks_json`` — ignoruje nieznane klucze.

    ``kind`` pochodzi z NASZEJ serializacji, więc rzutujemy na ``AttachmentKind`` (wartość
    kontrolowana); nieznana wartość zdegraduje najwyżej do gałęzi tekstowej w adapterze.
    """
    return Attachment(
        kind=cast(AttachmentKind, str(row.get("kind", "text"))),
        media_type=str(row.get("media_type", "")),
        name=str(row.get("name", "")),
        data_base64=str(row.get("data_base64", "")),
        text=str(row.get("text", "")),
    )


@dataclass(frozen=True)
class UserText:
    """Wpis transkryptu: tekst od użytkownika (opcjonalnie z załącznikami).

    ``attachments`` (addytywne, domyślnie puste — zgodność wsteczna) niosą treść
    multimodalną wysłaną przez użytkownika. Adapter odsyła je co turę jako bloki treści
    ``user`` (obraz/dokument PRZED tekstem); rdzeń trzyma tylko formę neutralną.

    ``trust`` (ADR 0066) to klasa POCHODZENIA tej tury, nadawana przy DRZWIACH i nigdy przez
    model: ``T1`` — nadawca rozwiązał się do osoby z mapy tożsamości (instrukcja), ``T2`` —
    nie rozwiązał się (gość, bot, konto spoza mapy), więc jego słowa są danymi. Domyślne
    ``T1`` zachowuje dawne zachowanie tam, gdzie rozszczepienie jest wyłączone albo drzwi
    nie mają pojęcia nadawcy (CLI z jednym operatorem).
    """

    text: str
    attachments: tuple[Attachment, ...] = ()
    trust: TrustClass = "T1"


@dataclass(frozen=True)
class AssistantTurn:
    """Wpis transkryptu: tura modelu (tekst i/lub żądane wywołania narzędzi).

    ``blocks`` (ADR 0011) to VERBATIM tablica bloków treści dostawcy dla tej tury
    (thinking + text + tool_use, w oryginalnej kolejności). Gdy niepusta, adapter
    odsyła ją bajt-w-bajt; gdy pusta (atrapy, wiersze legacy), adapter składa treść
    z ``text`` i ``tool_calls`` (kompatybilność wsteczna).
    """

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    blocks: tuple[Mapping[str, Any], ...] = ()
    # Rzeczywiste użycie tokenów wywołania API, które wyprodukowało tę turę (Design 2) —
    # zapisywane per tura asystenta; puste dla atrap/wierszy legacy.
    usage: TokenUsage = field(default_factory=TokenUsage)


@dataclass(frozen=True)
class RawTurn:
    """Wpis transkryptu: odtworzona tura z pamięci — NIEPRZEZROCZYSTE bloki (ADR 0011).

    Nośnik historii: ``blocks`` to verbatim tablica bloków (dla ``assistant`` —
    bloki dostawcy z ``signature``). Rdzeń jej nie interpretuje; adapter odsyła jako
    ``{"role": role, "content": blocks}`` bez filtrowania ani zmiany kolejności.
    """

    role: str
    blocks: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class ToolResults:
    """Wpis transkryptu: wyniki narzędzi wykonanych przez runtime.

    ``attachments`` (ADR 0064) niosą plik zmaterializowany przez ``File(action='read')``.
    NIE idą przez ``ToolOutput.content``, bo wynik narzędzia unosi tylko tekst i obraz —
    blok ``document`` (PDF) jest tam nielegalny, a treść wyniku bywa czyszczona przez
    edycję kontekstu (ADR 0058). Adapter renderuje je jako bloki RÓWNORZĘDNE wobec
    ``tool_result``, w tej samej wiadomości ``user``: bloki wyników muszą stać na jej
    początku, więc plik dokleja się PO nich i przed ewentualnym tekstem.
    """

    outputs: tuple[ToolOutput, ...]
    attachments: tuple[Attachment, ...] = ()


class AttachmentQueue:
    """Kolejka plików zmaterializowanych przez narzędzie w trakcie JEDNEJ tury (ADR 0064).

    Narzędzie i runtime stoją po dwóch stronach: ``File`` (domknięty w closurze drzwi) tylko
    dokłada, runtime tylko zabiera po rundzie wywołań. Ta klasa jest całym kontraktem między
    nimi — jawnym obiektem zamiast współdzielonej listy, żeby budżet miał gdzie mieszkać.

    ``budget_bytes`` to sufit dla WSZYSTKICH pobrań tej tury, POMNIEJSZONY przez drzwi o to,
    co same wstawiły do tury użytkownika: model i drzwi materializują do tego samego żądania
    API, więc dzielą jeden budżet. Wyczerpany budżet zwraca ``False`` — narzędzie zamienia to
    na rzeczową odmowę dla modelu, nigdy na wyjątek.
    """

    def __init__(self, *, budget_bytes: int) -> None:
        self._pending: list[Attachment] = []
        self._remaining = budget_bytes

    def offer(self, attachment: Attachment, size_bytes: int) -> bool:
        """Dołóż plik, jeśli mieści się w pozostałym budżecie; ``False`` gdy nie."""
        if size_bytes > self._remaining:
            return False
        self._remaining -= size_bytes
        self._pending.append(attachment)
        return True

    def remaining_bytes(self) -> int:
        """Ile bajtów budżetu zostało (do komunikatu odmowy — model ma wiedzieć ile brakuje)."""
        return self._remaining

    def drain(self) -> tuple[Attachment, ...]:
        """Zabierz i wyczyść to, co narzędzie odłożyło w tej rundzie wywołań."""
        drained = tuple(self._pending)
        self._pending.clear()
        return drained


TranscriptEntry = UserText | AssistantTurn | RawTurn | ToolResults


@dataclass(frozen=True)
class LLMResponse:
    """Jedna tura modelu: tekst, żądane narzędzia oraz VERBATIM bloki i ``stop_reason``.

    ``blocks`` (ADR 0011) to nieprzezroczysta tablica bloków treści tury (thinking z
    ``signature``, text, tool_use) do bezstratnego zapisu i odtworzenia. ``stop_reason``
    steruje pętlą runtime (``tool_use`` / ``end_turn`` / ``max_tokens`` / ``refusal``).
    """

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    blocks: tuple[Mapping[str, Any], ...] = ()
    stop_reason: str = ""
    # Podsumowanie rozumowania z bloków ``thinking`` (gdy ``display=summarized``); trzymane
    # OSOBNO od ``text``, by NIE wchodziło do projekcji FTS ani do rozliczenia tokenów.
    thinking_text: str = ""
    # Rzeczywiste użycie tokenów tego wywołania API (Design 2) — z pola ``usage`` odpowiedzi.
    usage: TokenUsage = field(default_factory=TokenUsage)

    @property
    def wants_tools(self) -> bool:
        """Czy model chce wywołać narzędzia (True), czy to już odpowiedź końcowa?"""
        return bool(self.tool_calls)


@dataclass(frozen=True)
class AgentResult:
    """Wynik jednej tury runtime'u (ADR 0011): odpowiedź + tury DO ZAPISU + ``stop_reason``.

    ``reply`` to tekst dla użytkownika. ``entries`` to NOWE wpisy transkryptu tej tury
    (wiadomość użytkownika + tury assistant/tool), już przefiltrowane do REPLAYOWALNYCH
    — tura ucięta na ``max_tokens`` albo wisząca ``tool_use`` jest wykluczana, bo jej
    odtworzenie dałoby API 400. Drzwi zapisują ``entries`` na ślepo.
    """

    reply: str
    entries: tuple[TranscriptEntry, ...] = ()
    stop_reason: str = ""
    # Podsumowanie rozumowania końcowej tury (gdy ``display=summarized``) — do pokazania na
    # zaufanych drzwiach (CLI). Puste, gdy myślenie wyłączone albo tura ucięta/bez odpowiedzi.
    thinking: str = ""
    # Zsumowane realne użycie tokenów CAŁEJ tury (wszystkie wywołania pętli tool-use).
    usage: TokenUsage = field(default_factory=TokenUsage)


class LLMClient(Protocol):
    """Kontrakt: z (prompt systemowy, transkrypt, katalog narzędzi) → tura modelu.

    ``system`` przyjmuje POJEDYNCZY tekst albo SEKWENCJĘ bloków (ADR 0056). Sekwencja
    pozwala rozdzielić część stałą od zmiennej — pierwszy blok niesie breakpoint cache'u,
    kolejne (np. nagłówek sesji z datą) zostają poza nim. Wołający, który podziału nie
    potrzebuje (kompaktowanie), podaje sam napis; adapter normalizuje oba kształty.
    """

    def complete(
        self,
        *,
        system: str | Sequence[str],
        transcript: Sequence[TranscriptEntry],
        tools: Sequence[ToolSpec],
        trust_nonce: str = "",
    ) -> LLMResponse: ...
