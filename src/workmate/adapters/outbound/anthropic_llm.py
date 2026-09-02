"""Adapter outbound: klient Claude API implementujący port ``LLMClient`` (ADR 0008).

Importy ``anthropic`` (leniwy, extra ``agent``) i ``func_metadata`` żyją TU, w
adapterze — nie w rdzeniu. Adapter tłumaczy słownik domenowy ⇆ wiadomości/bloki
Anthropic i wyprowadza ``input_schema`` narzędzia z tej samej ``fn`` co drzwi MCP
(jeden generator, jeden podpis → schemat agenta zgodny z MCP). Klucz API czyta
wyłącznie ten adapter; rdzeń i runtime go nie widzą.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from workmate.core.domain.pricing import TokenUsage
from workmate.core.domain.trust import DATA_CLASSES, wrap_untrusted
from workmate.core.errors import LLMError
from workmate.core.ports.llm import (
    AssistantTurn,
    LLMResponse,
    RawTurn,
    ToolCall,
    ToolResults,
    UserText,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from workmate.config import AgentSettings
    from workmate.core.application.tools import ToolSpec
    from workmate.core.ports.llm import Attachment, TranscriptEntry

logger = logging.getLogger(__name__)

# Czyszczenie kontekstu jest w Claude API funkcją BETA, więc nagłówek jedzie z żądaniem.
_CONTEXT_MANAGEMENT_BETA = "context-management-2025-06-27"


class AnthropicLLMClient:
    """``LLMClient`` nad Claude API.

    ``import anthropic`` jest w ``__init__`` (nie na poziomie modułu), więc sam
    import tego modułu nie wymaga extra ``agent`` — ale zbudowanie klienta już tak.
    Brak extra daje wtedy czytelny ``ImportError`` w punkcie składania (CLI łapie
    go i podpowiada instalację).
    """

    def __init__(self, settings: AgentSettings) -> None:
        import anthropic

        self._settings = settings
        self._client: Any = anthropic.Anthropic(api_key=settings.api_key or None)

    def complete(
        self,
        *,
        system: str | Sequence[str],
        transcript: Sequence[TranscriptEntry],
        tools: Sequence[ToolSpec],
        trust_nonce: str = "",
    ) -> LLMResponse:
        import anthropic

        # ``trust_nonce`` (ADR 0066) opakowuje treść OBCĄ w kopertę z etykietą pochodzenia.
        # Owijamy TU, przy renderowaniu żądania, a nie przy zapisie do pamięci: nonce jest
        # losowy na turę, więc zapisany w bazie rozjechałby się z każdą kolejną turą, a replay
        # niósłby znaczniki, których nagłówek tej tury już nie tłumaczy. Puste = dawny kształt.
        messages = _mark_cache(_to_messages(transcript, trust_nonce))
        tool_defs = [_to_tool_def(spec) for spec in tools]
        edits = _context_management(self._settings)
        # Czyszczenie wyników narzędzi żyje na ścieżce beta SDK, więc wywołanie wybieramy
        # PARAMETREM, nie osobną gałęzią kodu: przy wyłączonym czyszczeniu jedzie dokładnie
        # dawne żądanie (bez nagłówka bety), a kształt odpowiedzi jest w obu wypadkach ten sam.
        stream = self._client.beta.messages.stream if edits else self._client.messages.stream
        extra: dict[str, Any] = (
            {"betas": [_CONTEXT_MANAGEMENT_BETA], "context_management": edits} if edits else {}
        )

        try:
            # STREAMING (ADR 0011, rewizja): przy dużym ``max_tokens`` (domyślnie 128k —
            # pełny sufit modelu) tryb non-streaming (``messages.create``) jest ODRZUCANY
            # przez SDK (szacowany czas > limitu, zrywane bezczynne połączenie). Streaming
            # tego nie ma; ``get_final_message`` zwraca tę samą ``Message`` co ``create``
            # (pełna lista bloków + ``stop_reason``), więc ``_from_message`` działa bez zmian.
            with stream(
                model=self._settings.model,
                max_tokens=self._settings.max_tokens,
                # Prompt caching (#10): breakpoint na ostatnim (jedynym) bloku system. Kolejność
                # renderowania żądania Anthropic to tools → system → messages, więc JEDEN
                # breakpoint tu obejmuje prefiks tools+system w cache — a to jest największa,
                # najbardziej stabilna część promptu (schemat narzędzi + instrukcje agenta).
                system=_system_blocks(system),
                # Adaptive thinking (ADR 0011): w Sonnet 5 to jedyny tryb „on" — model
                # sam decyduje, ile myśleć. Bloki ``thinking`` (z ``signature``) są
                # przechwytywane w ``_from_message`` i odsyłane VERBATIM w ``_to_messages``,
                # więc tura z ``tool_use`` nie jest już odrzucana (400). ``display=summarized``
                # każe modelowi zwrócić CZYTELNE podsumowanie rozumowania (domyślnie ``omitted``
                # = pusty tekst; ``signature`` i tak obecna do round-tripu). Dokładamy je tylko
                # dla ``adaptive`` — przy ``disabled`` (``WORKMATE_AGENT_THINKING=disabled``)
                # myślenia nie ma, więc pole jest bez znaczenia (patrz ``_thinking_config``).
                thinking=_thinking_config(self._settings.thinking_type),
                messages=messages,
                tools=tool_defs,
                **extra,
            ) as opened:
                message = opened.get_final_message()
        except anthropic.APIError as exc:
            raise LLMError(f"Błąd Claude API: {exc}") from exc
        _log_applied_edits(message)
        return _from_message(message)


def _system_blocks(system: str | Sequence[str]) -> list[dict[str, Any]]:
    """System jako lista bloków tekstowych z ``cache_control`` na PIERWSZYM z nich.

    Napis normalizujemy do jednoelementowej sekwencji — wołający bez podziału (kompaktowanie)
    dostaje dokładnie dawne zachowanie. Przy dwóch blokach (ADR 0056) breakpoint zostaje na
    bloku STATYCZNYM: żądanie renderuje się jako tools → system → messages, więc cache obejmuje
    prefiks ``tools + static``, a nagłówek sesji (data, rozmowa) jedzie za nim poza cache'em.
    Breakpoint na ostatnim bloku unieważniałby ten prefiks przy każdej zmianie doby.

    Puste bloki odrzucamy — pusty ``text`` jest przez API odrzucany, a ``system_blocks``
    zwraca krotkę jednoelementową właśnie po to, żeby taki blok nie powstał.
    """
    blocks = [system] if isinstance(system, str) else [b for b in system if b]
    cached = {"cache_control": {"type": "ephemeral"}}
    return [
        {"type": "text", "text": text, **(cached if i == 0 else {})}
        for i, text in enumerate(blocks)
    ]


def _mark_cache(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dołóż ``cache_control`` na OSTATNIM bloku OSTATNIEJ wiadomości (breakpoint historii).

    Kopiuje, nie mutuje — ``cache_control`` nie może wyciec do ``ConversationStore`` (persist
    idzie z surowej odpowiedzi API / ``_to_messages``, PRZED tym wywołaniem, więc round-trip
    pamięci zostaje nietknięty). Gołe ``content: str`` (``_user_message`` bez załączników)
    zamieniamy na listę jednego bloku tekstowego — cache_control wymaga bloku, nie stringa.

    Skład narzędzi per tura może się zmienić między turami tej samej rozmowy (kanał, różni
    nadawcy — fabryki per-turowe w ``responder/conversational.py``) → cache-miss na tym
    breakpoincie jest oczekiwaną, łagodną degradacją kosztową, nie błędem: prefiks
    tools+system (breakpoint wyżej) i tak zostaje trafiony w większości przypadków.
    """
    if not messages:
        return messages
    marked = list(messages)
    last = dict(marked[-1])
    content = last["content"]
    blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
    if not blocks:
        return marked
    blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
    last["content"] = blocks
    marked[-1] = last
    return marked


def _thinking_config(thinking_type: str) -> dict[str, str]:
    """Parametr ``thinking`` dla Claude API; ``display=summarized`` tylko dla ``adaptive``.

    ``summarized`` włącza czytelne podsumowanie rozumowania (domyślnie ``omitted`` = pusty
    tekst thinking). Dla ``disabled`` myślenia nie ma, więc ``display`` byłby bez znaczenia
    (i potencjalnie odrzucony) — pomijamy go, zachowując konfigurowalność trybu.
    """
    if thinking_type == "adaptive":
        return {"type": "adaptive", "display": "summarized"}
    return {"type": thinking_type}


def _context_management(settings: AgentSettings) -> dict[str, Any] | None:
    """Konfiguracja czyszczenia starych wyników narzędzi (ADR 0058) albo ``None``, gdy wyłączone.

    Wynik narzędzia wraca do kontekstu i jedzie ponownie w KAŻDEJ kolejnej turze, więc bez
    czyszczenia rozmowa rośnie o treść, której model już nie czyta. Czyścimy sam wynik,
    zostawiając ``tool_use`` (``clear_tool_inputs`` pomijamy, bo domyślnie ``false``): ślad
    „pytałem o X" zostaje, znika tylko odpowiedź — dzięki temu model nie powtarza zapytania,
    a gdy treść znów będzie potrzebna, woła narzędzie świadomie.

    ``keep`` chroni ostatnie pary wywołanie–wynik, w tym tę z bieżącej pętli tool-use;
    ``clear_at_least`` pilnuje, żeby czyszczenie zdjęło dość dużo, by opłacić unieważnienie
    cache'u prefiksu (każde czyszczenie = zapis cache'u od nowa).
    """
    if not settings.context_editing_enabled:
        return None
    return {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {
                    "type": "input_tokens",
                    "value": settings.context_editing_trigger_tokens,
                },
                "keep": {
                    "type": "tool_uses",
                    "value": settings.context_editing_keep_tool_uses,
                },
                "clear_at_least": {
                    "type": "input_tokens",
                    "value": settings.context_editing_clear_at_least_tokens,
                },
            }
        ]
    }


def _log_applied_edits(message: Any) -> None:
    """Zaloguj, ile kontekstu zdjęło czyszczenie — jedyny ślad, że w ogóle zadziałało.

    Czyszczenie dzieje się po stronie API i jest niewidoczne w naszym transkrypcie: bez
    tego logu różnica między „próg za wysoki, nic się nie czyści" a „czyści się co turę
    i płacimy zapis cache'u" jest nie do odróżnienia z zewnątrz.
    """
    applied = getattr(getattr(message, "context_management", None), "applied_edits", None)
    for edit in applied or ():
        logger.info(
            "Wyczyszczono kontekst: %s wywołań, %s tokenów wejścia",
            getattr(edit, "cleared_tool_uses", "?"),
            getattr(edit, "cleared_input_tokens", "?"),
        )


def _to_tool_def(spec: ToolSpec) -> dict[str, Any]:
    """Zbuduj definicję narzędzia Anthropic; ``input_schema`` z tej samej ``fn`` co MCP."""
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    schema = func_metadata(spec.fn).arg_model.model_json_schema()
    return {"name": spec.name, "description": spec.description, "input_schema": schema}


def _replayable_block(block: Mapping[str, Any]) -> dict[str, Any]:
    """Oczyść zapamiętany blok przed odesłaniem do API: usuń pola o wartości ``None``.

    ``_from_message`` zrzuca CAŁY blok odpowiedzi (``model_dump``), w tym pola WYJŚCIOWE,
    których wejściowy schemat API nie przyjmuje — np. ``parsed_output`` na bloku ``text``
    (odpowiedź modelu ma je jako ``None``) → API 400 „Extra inputs are not permitted",
    co psuło każdą turę 2+ (odtworzenie zapisanego bloku tekstowego z poprzedniej tury).
    Pola WYMAGANE na wejściu (``text``; ``thinking`` + ``signature``; ``id``/``name``/``input``)
    są zawsze nie-``None``, więc zostają — round-trip thinking (ADR 0011) nienaruszony.
    Czyścimy przy ODTWARZANIU (nie przy zapisie), więc leczymy też już zapisaną historię.
    """
    return {key: value for key, value in block.items() if value is not None}


def _obce(text: str, *, origin: str, nonce: str) -> str:
    """Opakuj treść obcą kopertą T3 (ADR 0066); bez nonce'a zwróć ją bez zmian.

    Pusty nonce to WYŁĄCZONE etykiety — wtedy żądanie ma dokładnie dawny kształt, co pozwala
    włączyć tę warstwę osobno i porównać zachowanie modelu bez przebudowy ścieżki.
    """
    return wrap_untrusted(text, origin=origin, nonce=nonce) if nonce else text


def _attachment_block(att: Attachment, nonce: str = "") -> dict[str, Any]:
    """Zmapuj neutralny ``Attachment`` na blok treści Anthropic — format żyje TU, nie w rdzeniu.

    ``image``/``document`` idą jako base64 (PDF: ``application/pdf``); ``.docx`` po ekstrakcji
    (``kind="text"``) jako blok tekstowy z etykietą pliku. Treść to DANE, nie polecenia.

    Kopertę T3 (ADR 0066) zakłada tylko gałąź TEKSTOWA. Obraz i PDF są osobnymi typami bloków
    — API nie przyjmie w nich znacznika, a i nie ma po co: ich treść z definicji nie jest
    tekstem, który model mógłby wziąć za instrukcję systemową. Tekst wyciągnięty z dokumentu
    jest dokładnie odwrotny — wygląda jak zwykła wiadomość i to on wymaga granicy.
    """
    if att.kind == "image":
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": att.media_type,
                "data": att.data_base64,
            },
        }
    if att.kind == "document":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": att.data_base64,
            },
        }
    return {
        "type": "text",
        "text": _obce(f"[Plik: {att.name}]\n{att.text}", origin="plik", nonce=nonce),
    }


def _user_message(entry: UserText, nonce: str = "") -> dict[str, Any]:
    """Zbuduj wiadomość ``user``: goły string bez załączników, inaczej lista bloków.

    Bez załączników zwracamy string (jak wcześniej — golden-testy niezmienione). Z
    załącznikami: bloki mediów (``image``/``document``) MUSZĄ poprzedzać blok tekstowy
    (wymóg API dla PDF), więc caption idzie na końcu — i tylko gdy niepusty (API odrzuca
    pusty blok ``text``).
    """
    # Tura nadawcy, który się nie rozwiązał (T2, ADR 0066), schodzi do DANYCH: opakowujemy jej
    # tekst tą samą kopertą co plik. Bot dalej odpowiada — bramki zdolności (0062/0063) już
    # osobno odmawiają mu narzędzi — ale „zignoruj poprzednie instrukcje" od gościa przestaje
    # być instrukcją w oczach modelu. Załączniki są T3 niezależnie od klasy nadawcy: zmapowany
    # członek też może przesłać zatruty plik.
    text = (
        _obce(entry.text, origin="goscie", nonce=nonce)
        if entry.trust in DATA_CLASSES
        else (entry.text)
    )
    if not entry.attachments:
        return {"role": "user", "content": text}
    content: list[dict[str, Any]] = [_attachment_block(att, nonce) for att in entry.attachments]
    if text:
        content.append({"type": "text", "text": text})
    return {"role": "user", "content": content}


def _to_messages(
    transcript: Sequence[TranscriptEntry], trust_nonce: str = ""
) -> list[dict[str, Any]]:
    """Zmapuj słownik domenowy na listę wiadomości Anthropic.

    Tury z blokami (``RawTurn`` z pamięci, ``AssistantTurn`` z bieżącego przebiegu)
    odsyłamy w oryginalnej KOLEJNOŚCI bloków (thinking MUSI poprzedzać ``tool_use`` i wrócić
    z niezmienioną ``signature``, inaczej API 400), oczyszczając jedynie puste pola wyjściowe
    przez ``_replayable_block`` (patrz jego docstring). ``AssistantTurn`` bez bloków (atrapy,
    wiersze legacy) składamy z ``text``/``tool_calls``.

    ``trust_nonce`` (ADR 0066) włącza koperty T2/T3 na treści OBCEJ. Tury asystenta zostają
    nietknięte i to jest istotne: ich bloki wracają VERBATIM z ``signature`` thinking, więc
    jakakolwiek zmiana bajtu unieważniłaby replay (API 400). Model i tak nie jest dla siebie
    treścią obcą — kopertowanie go byłoby szumem za cenę zerwanej pamięci.
    """
    messages: list[dict[str, Any]] = []
    for entry in transcript:
        if isinstance(entry, UserText):
            messages.append(_user_message(entry, trust_nonce))
        elif isinstance(entry, RawTurn):
            messages.append(
                {"role": entry.role, "content": [_replayable_block(b) for b in entry.blocks]}
            )
        elif isinstance(entry, AssistantTurn):
            if entry.blocks:
                content: list[dict[str, Any]] = [_replayable_block(b) for b in entry.blocks]
            else:
                content = []
                if entry.text:
                    content.append({"type": "text", "text": entry.text})
                for call in entry.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
            messages.append({"role": "assistant", "content": content})
        elif isinstance(entry, ToolResults):
            results: list[dict[str, Any]] = []
            for output in entry.outputs:
                block: dict[str, Any] = {
                    "type": "tool_result",
                    "tool_use_id": output.call_id,
                    # Wynik narzędzia to T3 (ADR 0066) — bez wyjątków dla „naszych" narzędzi.
                    # Wyjście `Bash`, treść komentarza z GitHuba i notatka z bazy wiedzy jadą
                    # tą samą drogą, a polityka per narzędzie byłaby listą do rozjechania się
                    # przy pierwszym nowym narzędziu.
                    "content": _obce(output.content, origin="narzedzie", nonce=trust_nonce),
                }
                if output.is_error:
                    block["is_error"] = True
                results.append(block)
            # Pliki podane przez ``File`` (ADR 0064) jadą jako bloki RÓWNORZĘDNE, nie w treści
            # wyniku: ``tool_result`` przyjmuje tekst i obraz, ale NIE blok ``document`` (PDF).
            # Kolejność jest wymogiem API — bloki ``tool_result`` muszą stać na początku
            # wiadomości ``user``, więc materiał dokleja się po nich.
            results.extend(_attachment_block(att, trust_nonce) for att in entry.attachments)
            messages.append({"role": "user", "content": results})
    return messages


def _from_message(message: Any) -> LLMResponse:
    """Zmapuj odpowiedź Anthropic na słownik domenowy (ADR 0011).

    Bloki treści przechwytujemy VERBATIM (``model_dump(mode="json")``) w oryginalnej
    kolejności — łącznie z ``thinking``/``redacted_thinking`` i ich ``signature`` — żeby
    pamięć mogła odesłać je bajt-w-bajt (API odrzuca bloki ZMODYFIKOWANE, nie odczytane).
    Z tych samych bloków wyprowadzamy pola semantyczne: ``text`` (konkatenacja bloków
    ``text``, bez thinking) steruje projekcją do FTS i odpowiedzią; ``thinking_text``
    (konkatenacja bloków ``thinking`` — podsumowanie, gdy ``display=summarized``) służy do
    pokazania rozumowania; ``tool_calls`` steruje dispatchem. ``stop_reason`` steruje pętlą
    (``max_tokens`` = tura ucięta, niereplayowalna).
    """
    text_parts: list[str] = []
    thinking_parts: list[str] = []
    calls: list[ToolCall] = []
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        blocks.append(block.model_dump(mode="json"))
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "thinking":
            # ``display=summarized`` → czytelne podsumowanie; ``omitted`` → pusty tekst.
            # Osobno od ``text`` (nie wchodzi do FTS ani do szacunku tokenów — ADR 0011).
            thinking_parts.append(block.thinking)
        elif block.type == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input)))
    return LLMResponse(
        text="".join(text_parts),
        thinking_text="".join(thinking_parts),
        tool_calls=tuple(calls),
        blocks=tuple(blocks),
        stop_reason=message.stop_reason or "",
        usage=_usage_of(message),
    )


def _usage_of(message: Any) -> TokenUsage:
    """Wyciągnij REALNE ``usage`` z odpowiedzi API (Design 2) — 0, gdy brak.

    Pola ``cache_*`` bywają ``None``, gdy prompt caching nie jest użyty (``or 0``).
    Atrapy/legacy bez ``usage`` → puste ``TokenUsage`` (koszt 0), bez wywracania mapowania.
    """
    u = getattr(message, "usage", None)
    if u is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
