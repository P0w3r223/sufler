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

from typing import TYPE_CHECKING

from workmate.core.agent.prompt import SUMMARY_SYSTEM_PROMPT
from workmate.core.ports.llm import UserText

if TYPE_CHECKING:
    from collections.abc import Sequence

    from workmate.core.domain.conversation import (
        ConversationMessage,
        ConversationSummary,
    )
    from workmate.core.ports.conversations import ConversationStore
    from workmate.core.ports.llm import LLMClient

# Etykiety ról w spłaszczonym transkrypcie podawanym modelowi podsumowującemu.
_ROLE_LABELS = {"user": "Użytkownik", "assistant": "Asystent", "tool": "Narzędzie"}


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

    def maybe_compact(self, conversation_id: str) -> ConversationSummary | None:
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
        response = self._llm.complete(
            system=SUMMARY_SYSTEM_PROMPT,
            transcript=[UserText(self._flatten(previous, to_summarize))],
            tools=[],
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
        jest) + tury z etykietą roli. Tury bez tekstu (narzędziowe) pomijamy."""
        lines: list[str] = []
        if previous is not None:
            lines.append("[Dotychczasowe podsumowanie]")
            lines.append(previous.summary)
            lines.append("")
        for m in messages:
            if not m.text:
                continue
            lines.append(f"{_ROLE_LABELS.get(m.role, m.role)}: {m.text}")
        return "\n".join(lines)
