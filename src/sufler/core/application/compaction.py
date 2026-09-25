"""Kompaktowanie historii rozmowy (ADR 0014) — streszczanie starych tur.

Gdy WEJŚCIE ostatniej tury (``last_input_tokens``) przekroczy próg, zostawiamy verbatim
ostatnie N wymian, a wszystko starsze (plus dotychczasowe podsumowanie) zastępujemy JEDNYM
podsumowaniem z OSOBNEGO wywołania modelu. Oryginałów nie usuwamy — oznaczamy je jako
zarchiwizowane (historia i wyszukiwanie zostają nietknięte); podsumowanie idzie do osobnego
rekordu. Powtarzalne: kolejne kompaktowanie obejmuje poprzednie podsumowanie + nowe tury.

Logika bez I/O w rdzeniu: zależy tylko od portów ``ConversationStore`` i ``LLMClient`` —
testujemy na atrapach, bez sieci. „Wymiana" liczona jest od wiadomości użytkownika do
następnej wiadomości użytkownika (tak odmierzamy N tur do zachowania verbatim).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sufler.core.agent.prompt import SUMMARY_SYSTEM_PROMPT
from sufler.core.domain.trust import wrap_untrusted
from sufler.core.ports.llm import UserText, attachment_from_row

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sufler.core.domain.conversation import (
        ConversationMessage,
        ConversationSummary,
    )
    from sufler.core.ports.conversations import ConversationStore
    from sufler.core.ports.llm import LLMClient

# Etykiety ról w spłaszczonym transkrypcie podawanym modelowi podsumowującemu.
_ROLE_LABELS = {"user": "Użytkownik", "assistant": "Asystent", "tool": "Narzędzie"}

# Ile znaków wyniku narzędzia wchodzi do streszczacza (ADR 0068 §7). Wyniki bywają ogromne
# (setki zdarzeń, pełna treść notatki), więc verbatim nie wchodzą — ale całkowite pominięcie
# znaczyło, że klucz Jiry i identyfikator notatki przeżywały kompaktowanie WYŁĄCZNIE wtedy, gdy
# model powtórzył je własnymi słowami. Prompt streszczacza prosi o identyfikatory (sekcja
# „Kluczowe fakty i encje"), a materiału do ich odczytania nie dostawał. Prefiks, bo wyniki
# narzędzi są JSON-em i identyfikatory stoją w pierwszych polach rekordu.
_MAX_TOOL_RESULT_CHARS = 500


class CompactionService:
    """Streszcza starą część wątku, gdy wejście ostatniej tury przekroczy próg (ADR 0014)."""

    def __init__(
        self,
        store: ConversationStore,
        summarizer: LLMClient,
        *,
        threshold_tokens: int,
        keep_turns: int,
    ) -> None:
        if threshold_tokens < 1:
            raise ValueError("threshold_tokens musi być >= 1")
        if keep_turns < 1:
            raise ValueError("keep_turns musi być >= 1")
        self._store = store
        self._llm = summarizer
        self._threshold = threshold_tokens
        self._keep_turns = keep_turns

    def maybe_compact(
        self, conversation_id: str, *, trust_nonce: str = ""
    ) -> ConversationSummary | None:
        """Skompaktuj wątek, jeśli trzeba; zwróć nowe podsumowanie albo ``None``.

        Trigger: ``last_input_tokens`` (wejście + cache ostatniej tury) > próg. Zachowuje
        verbatim ostatnie N wymian, streszcza resztę (razem z poprzednim podsumowaniem),
        archiwizuje zestreszczone tury i zapisuje nowe podsumowanie. ``None``, gdy próg nie
        przekroczony, za mało starych tur do streszczenia albo model zwrócił pusty wynik
        (wtedy NIC nie archiwizujemy — nie ryzykujemy utraty kontekstu).

        Sekwencja get→replay→save→archive biegnie POZA zamkiem drzwi (woła LLM). Przy dwóch
        turach TEJ SAMEJ rozmowy naraz (rzadkie — użytkownik czeka na odpowiedź) obie mogą
        streścić: skutkiem jest zmarnowane wywołanie modelu i zastąpione podsumowanie, bez
        korupcji (``save_summary`` supersede'uje, ``archive_through`` idempotentne, druga tura
        na przyciętym replayu trafi na ``boundary <= 0``). Redundancję świadomie dopuszczamy.
        """
        conv = self._store.get(conversation_id)
        if conv is None or conv.last_input_tokens <= self._threshold:
            return None

        messages = self._store.replay_messages(conversation_id)
        boundary = self._split_boundary(messages)
        if boundary <= 0:
            return None  # same N wymian (albo mniej) — nie ma czego streszczać

        to_summarize = messages[:boundary]
        previous = self._store.active_summary(conversation_id)
        # Historia idąca do streszczacza jest z jego punktu widzenia treścią OBCĄ w całości
        # (ADR 0066): siedzą w niej wyniki narzędzi i tury nadawców spoza mapy, spłaszczone do
        # jednego tekstu. Bez koperty streszczacz czytał to jako instrukcje — a jego wynik wraca
        # potem do rozmowy doklejony do PIERWSZEJ tury, więc zatruta treść awansowała do rangi
        # prozy instrukcyjnej dokładnie tam, gdzie nikt by jej nie szukał. Nonce podają drzwi,
        # bo to one nim zarządzają; puste = dawne zachowanie.
        historia = self._flatten(previous, to_summarize)
        if trust_nonce:
            historia = wrap_untrusted(historia, origin="historia", nonce=trust_nonce)
        response = self._llm.complete(
            system=SUMMARY_SYSTEM_PROMPT,
            transcript=[UserText(historia)],
            tools=[],
            trust_nonce=trust_nonce,
        )
        summary_text = response.text.strip()
        if not summary_text:
            return None  # pusty wynik modelu — nie archiwizuj (unikamy utraty kontekstu)

        covers_through = to_summarize[-1].id
        # Zapis podsumowania PRZED archiwizacją: to dwa osobne commity, więc gdyby proces
        # padł między nimi, chcemy DUPLIKACJI (skrót + wciąż odtwarzalne tury), którą kolejne
        # kompaktowanie naprawia — a nie NIEODWRACALNEJ utraty (tury zarchiwizowane bez skrótu).
        summary = self._store.save_summary(
            conversation_id, summary_text, covers_through, usage=response.usage
        )
        self._store.archive_through(conversation_id, covers_through)
        return summary

    def _split_boundary(self, messages: Sequence[ConversationMessage]) -> int:
        """Indeks początku N ostatnich wymian (granica: ``[:boundary]`` do streszczenia).

        Wymiana zaczyna się wiadomością użytkownika; szukamy początku wymiany
        (liczba_wymian - N)-tej od końca. Wszystko przed nią idzie do streszczenia, reszta
        zostaje verbatim. 0, gdy wymian jest <= N (nie ma czego streszczać).
        """
        user_indices = [i for i, m in enumerate(messages) if m.role == "user"]
        if len(user_indices) <= self._keep_turns:
            return 0
        return user_indices[len(user_indices) - self._keep_turns]

    def _flatten(
        self,
        previous: ConversationSummary | None,
        messages: Sequence[ConversationMessage],
    ) -> str:
        """Złóż streszczaną część w jeden tekst dla modelu: poprzednie podsumowanie (jeśli
        jest) + tury z etykietą roli. Wyniki narzędzi wchodzą PRZYCIĘTE do
        ``_MAX_TOOL_RESULT_CHARS`` — tyle, by identyfikatory z ich początku miały szansę
        wejść do podsumowania. Załączniki użytkownika opisujemy TEKSTOWO — base64 obrazu/PDF
        NIGDY nie wchodzi do streszczacza (tylko nazwa+typ; dla .docx pełny tekst)."""
        lines: list[str] = []
        if previous is not None:
            lines.append("[Dotychczasowe podsumowanie]")
            lines.append(previous.summary)
            lines.append("")
        for m in messages:
            label = _ROLE_LABELS.get(m.role, m.role)
            if m.text:
                lines.append(f"{label}: {m.text}")
            if m.role == "user" and m.blocks:
                lines.extend(_describe_attachment(b) for b in m.blocks)
            elif m.role == "tool" and m.blocks:
                # Wiersz tury narzędziowej niesie DWA rodzaje bloków (ADR 0064): wyniki narzędzi
                # (mają ``call_id``) oraz PLIKI podane przez ``File``. Oba wchodzą — wyniki
                # przycięte. Do ADR 0068 wyniki były pomijane w całości, więc do streszczacza
                # nie docierał ANI JEDEN identyfikator zwrócony przez narzędzie: klucze Jiry
                # i identyfikatory notatek przeżywały kompaktowanie tylko wtedy, gdy model
                # powtórzył je w swojej odpowiedzi. Wiersz tury narzędziowej ma pusty ``text``
                # z definicji (``conversations._row_of``), więc gałąź ``if m.text`` wyżej
                # nigdy go nie widziała.
                for block in m.blocks:
                    if "call_id" in block:
                        lines.append(f"{label}: {_przytnij(str(block.get('content', '')))}")
                    else:
                        lines.append(_describe_attachment(block))
        return "\n".join(lines)


def _przytnij(content: str) -> str:
    """Prefiks wyniku narzędzia z jawnym znacznikiem ucięcia — cisza po ucięciu myliłaby."""
    if len(content) <= _MAX_TOOL_RESULT_CHARS:
        return content
    return f"{content[:_MAX_TOOL_RESULT_CHARS]}… [wynik przycięty]"


def _describe_attachment(block: dict[str, Any]) -> str:
    """Tekstowy opis załącznika do streszczacza (bez base64); dla .docx dołącza treść."""
    att = attachment_from_row(block)
    if att.kind == "text" and att.text:
        return f"[Załącznik {att.name} ({att.media_type})]\n{att.text}"
    return f"[Załącznik {att.name} ({att.media_type})]"
