"""Testy serwisu rozmów (``ConversationService``, ADR 0010) — wątek, historia, rollover.

Logika bez I/O: atrapa ``ConversationStore`` w pamięci. Sprawdzamy trzy rzeczy:
otwieranie wątku, zwracanie historii SPRZED bieżącej wiadomości oraz rollover do
nowej rozmowy po przekroczeniu limitu kontekstu.
"""
from __future__ import annotations

from datetime import datetime

from workmate.core.application.conversations import ConversationService, estimate_tokens
from workmate.core.domain.conversation import (
    Conversation,
    ConversationMessage,
    ConversationSearchHit,
)

_TS = datetime(2025, 1, 1, 12, 0, 0)


class _FakeStore:
    """Atrapa magazynu rozmów w pamięci (kaczo-typowana wobec ``ConversationStore``)."""

    def __init__(self) -> None:
        self.conversations: dict[str, Conversation] = {}
        self.msgs: dict[str, list[ConversationMessage]] = {}
        self._conv_seq = 0
        self._msg_seq = 0

    def active_conversation(
        self, channel: str, external_id: str
    ) -> Conversation | None:
        actives = [
            c
            for c in self.conversations.values()
            if c.channel == channel
            and c.external_id == external_id
            and c.status == "active"
        ]
        if not actives:
            return None
        conv = actives[-1]
        total = sum(m.token_estimate for m in self.msgs[conv.id])
        return conv.model_copy(update={"token_estimate": total})

    def open_conversation(self, channel: str, external_id: str) -> Conversation:
        self._conv_seq += 1
        cid = f"c{self._conv_seq}"
        conv = Conversation(
            id=cid,
            channel=channel,
            external_id=external_id,
            status="active",
            token_estimate=0,
            created_at=_TS,
            updated_at=_TS,
        )
        self.conversations[cid] = conv
        self.msgs[cid] = []
        return conv

    def close_conversation(self, conversation_id: str) -> None:
        conv = self.conversations[conversation_id]
        self.conversations[conversation_id] = conv.model_copy(
            update={"status": "closed"}
        )

    def append_message(
        self, conversation_id: str, role: str, text: str, token_estimate: int
    ) -> ConversationMessage:
        self._msg_seq += 1
        msg = ConversationMessage(
            id=self._msg_seq,
            conversation_id=conversation_id,
            role=role,
            text=text,
            token_estimate=token_estimate,
            created_at=_TS,
        )
        self.msgs[conversation_id].append(msg)
        return msg

    def messages(self, conversation_id: str) -> list[ConversationMessage]:
        return list(self.msgs[conversation_id])

    def search(
        self,
        query: str,
        *,
        channel: str | None = None,
        external_id: str | None = None,
        limit: int = 20,
    ) -> list[ConversationSearchHit]:
        return []


def test_estimate_tokens_is_deterministic_and_positive():
    assert estimate_tokens("") == 1
    assert estimate_tokens("12345678") == 2  # 8 // 4


def test_first_message_opens_conversation_with_empty_history():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    conv_id, history, rolled_over = service.prepare_turn("telegram", "chat1", "czesc")

    assert history == []
    assert rolled_over is False
    # Wiadomość użytkownika trafiła do magazynu.
    assert [m.text for m in store.msgs[conv_id]] == ["czesc"]


def test_history_returns_prior_turns_not_current_message():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=1000)

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "pierwsza")
    service.record_reply(cid1, "odpowiedz-1")
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "druga")

    assert cid2 == cid1  # ten sam wątek (limit nieprzekroczony)
    assert rolled_over is False
    assert [(m.role, m.text) for m in history] == [
        ("user", "pierwsza"),
        ("assistant", "odpowiedz-1"),
    ]


def test_rollover_starts_new_conversation_on_limit():
    store = _FakeStore()
    service = ConversationService(store, max_context_tokens=3)

    cid1, _, _ = service.prepare_turn("telegram", "chat1", "12345678")  # est 2
    service.record_reply(cid1, "87654321")  # est 2 → suma 4 (> limit 3)
    cid2, history, rolled_over = service.prepare_turn("telegram", "chat1", "1234")

    assert rolled_over is True
    assert cid2 != cid1
    assert history == []  # świeży kontekst nowej rozmowy
    # Stara rozmowa domknięta (nadal w magazynie, wyszukiwalna).
    assert store.conversations[cid1].status == "closed"
