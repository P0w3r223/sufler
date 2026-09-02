"""Składanie transkryptu dla modelu i doklejanie notek do gotowej odpowiedzi."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from workmate.core.domain.trust import TrustClass
from workmate.core.ports.llm import (
    AssistantTurn,
    RawTurn,
    ToolOutput,
    ToolResults,
    UserText,
    attachment_from_row,
)

if TYPE_CHECKING:
    from workmate.core.domain.conversation import ConversationMessage, ConversationSummary
    from workmate.core.ports.llm import TranscriptEntry

from workmate.adapters.inbound.responder.protocols import InboundMessage

# ``stop_reason`` oznaczający uciętą odpowiedź (ADR 0011) — drzwi dokładają notkę.
_TRUNCATED_STOP = "max_tokens"

# Prefiks wiadomości z podsumowaniem kompaktowania (ADR 0014). Sonnet 5 nie ma systemowych
# wiadomości w środku rozmowy, więc podsumowanie idzie jako treść użytkownika z tym nagłówkiem.
_SUMMARY_PREFIX = "[Podsumowanie wcześniejszej rozmowy]"


def _utcnow() -> datetime:
    """Bieżąca chwila jako NAIVE UTC — spójna z timestampami bazy rozmów.

    Magazyn zapisuje ``updated_at`` przez ``CURRENT_TIMESTAMP`` (UTC, bez strefy),
    a serwis liczy bezczynność jako ``now - updated_at`` (ADR 0012). ``now`` musi
    więc być w tej samej postaci (naive UTC), inaczej odejmowanie aware−naive rzuca
    ``TypeError``. Zegar jest w adapterze — rdzeń nie woła zegara.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _with_thinking(reply: str, thinking: str) -> str:
    """Poprzedź odpowiedź podsumowaniem rozumowania modelu (tylko drzwi zaufane — CLI).

    ``thinking`` (gdy ``display=summarized``) to czytelne streszczenie toku myślenia.
    Pokazujemy je nad odpowiedzią, wyraźnie oznaczone; puste — nic nie dodajemy.
    """
    if not thinking.strip():
        return reply
    return f"[rozumowanie modelu]\n{thinking.strip()}\n\n{reply}"


def _with_notices(reply: str, *, rolled_over: bool, stop_reason: str) -> str:
    """Dołóż notki systemowe przed odpowiedź (rollover rozmowy, ucięcie na limicie).

    Notka rolloveru jest NEUTRALNA co do powodu: nowy wątek startuje albo po limicie
    kontekstu, albo po dłuższej przerwie (bezczynność, ADR 0012) — ``prepare_turn`` nie
    rozróżnia tych przyczyn, a użytkownikowi wystarczy wiedza, że zaczęła się nowa rozmowa.
    """
    notices: list[str] = []
    if rolled_over:
        notices.append(
            "(Zaczynam nową rozmowę — poprzednia dobiegła limitu kontekstu "
            "albo minęła dłuższa przerwa.)"
        )
    if stop_reason == _TRUNCATED_STOP:
        notices.append("(Odpowiedź została ucięta — przekroczyła limit długości.)")
    if not notices:
        return reply
    return "\n".join(notices) + "\n\n" + reply


def _attachment_bytes(message: InboundMessage) -> int:
    """Ile bajtów base64 drzwi już wstawiły do tury użytkownika (ADR 0064 — wspólny budżet).

    Liczymy SUROWE bajty (base64 ÷ 4 × 3), bo w tej samej jednostce wyrażony jest sufit
    materializacji. Pliki zamienione na tekst nie niosą base64 i słusznie ważą zero — nie idą
    do API jako bajty.
    """
    return sum(len(a.data_base64) * 3 // 4 for a in message.attachments)


def _to_transcript(messages: list[ConversationMessage]) -> list[TranscriptEntry]:
    """Zmapuj tury rozmowy na wpisy transkryptu LLM — bezstratnie (ADR 0011).

    Tury asystenta z zapisanymi blokami odtwarzamy jako ``RawTurn`` (bloki dostawcy
    VERBATIM — thinking z ``signature`` wraca 1:1); tury ``tool`` jako ``ToolResults``
    z formy domenowej. Wiersze sprzed 0011 (bez bloków) degradują do text-only
    ``AssistantTurn`` / ``UserText`` — zawsze poprawne do odesłania do API.
    """
    entries: list[TranscriptEntry] = []
    for msg in messages:
        if msg.role == "assistant":
            if msg.blocks:
                entries.append(RawTurn("assistant", tuple(msg.blocks)))
            elif msg.text:
                entries.append(AssistantTurn(msg.text, ()))
        elif msg.role == "tool":
            if msg.blocks:
                # Wiersz tury narzędziowej niesie DWA rodzaje bloków (ADR 0064): wyniki narzędzi
                # (mają ``call_id``) oraz pliki podane przez ``File`` w formie neutralnej. Bez
                # tego podziału plik wróciłby jako wynik bez ``call_id`` i wywrócił replay.
                entries.append(
                    ToolResults(
                        tuple(
                            ToolOutput(b["call_id"], b["content"], b.get("is_error", False))
                            for b in msg.blocks
                            if "call_id" in b
                        ),
                        tuple(attachment_from_row(b) for b in msg.blocks if "call_id" not in b),
                    )
                )
        elif msg.text or msg.blocks:
            # Wiersz użytkownika: ``blocks`` (gdy są) to NEUTRALNA forma załączników —
            # odtwarzamy je, by replay był bezstratny. Warunek ``or msg.blocks`` pilnuje,
            # by wiadomość z SAMYM plikiem (pusty caption) nie wypadła z transkryptu.
            attachments = tuple(attachment_from_row(b) for b in (msg.blocks or []))
            # Klasa pochodzenia (ADR 0066) wraca z wiersza; wiersze sprzed 0066 i drzwi bez
            # rozszczepienia mają NULL → T1, czyli dawne zachowanie. Bez tego tura gościa
            # wracałaby w kolejnych turach jako instrukcja — granica trzymałaby JEDNĄ turę.
            trust: TrustClass = "T2" if msg.trust == "T2" else "T1"
            entries.append(UserText(msg.text, attachments, trust))
    return entries


def _to_transcript_with_summary(
    summary: ConversationSummary | None, messages: list[ConversationMessage]
) -> list[TranscriptEntry]:
    """Jak ``_to_transcript``, ale z doklejonym aktywnym podsumowaniem (ADR 0014).

    Podsumowanie idzie jako treść UŻYTKOWNIKA z prefiksem (Sonnet 5 nie ma systemowych
    wiadomości w środku rozmowy). Doklejamy je do PIERWSZEJ tury użytkownika w replayu —
    po kompaktowaniu replay zaczyna się właśnie turą użytkownika — zamiast wstawiać osobną
    wiadomość, żeby nie powstały dwie tury ``user`` z rzędu. Gdy replay nie zaczyna się od
    użytkownika (sytuacja defensywna), podsumowanie idzie jako osobna wiadomość na początku.
    """
    entries = _to_transcript(messages)
    if summary is None:
        return entries
    header = f"{_SUMMARY_PREFIX}\n{summary.summary}"
    if entries and isinstance(entries[0], UserText):
        first = entries[0]
        # Doklejamy nagłówek do tekstu, ale ZACHOWUJEMY załączniki pierwszej tury.
        # ZACHOWUJEMY klasę pierwszej tury (ADR 0066): podsumowanie doklejamy do jej
        # tekstu, więc gdyby klasa przepadła, tura gościa awansowałaby do instrukcji
        # dokładnie w momencie kompaktowania — czyli tam, gdzie nikt by tego nie szukał.
        return [
            UserText(f"{header}\n\n{first.text}", first.attachments, first.trust),
            *entries[1:],
        ]
    return [UserText(header), *entries]
