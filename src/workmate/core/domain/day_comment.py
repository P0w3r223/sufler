"""Komentarz dnia do arkusza z danych ``claude_summary`` (ADR 0036) — co osoba robiła danego dnia.

CZYSTA logika: bez I/O. Preferujemy gotowy opis prozą (``llm_prose``); gdy go brak, syntezujemy
z tego, co KONKRETNIE opisuje pracę — komunikatów commitów, a w ich braku ze skrótu promptów.
Treść ``claude_summary`` jest już ZREDAGOWANA po stronie tamtego narzędzia (sekrety/IP/GUID), a
arkusz i tak sanityzuje znaki sterujące i blokuje wstrzyknięcie formuły — komentarz to DANE.
"""

from __future__ import annotations

# Sufit długości komentarza. Trzymaj ZGODNIE z ``timesheet_sheet._MAX_COMMENT`` (też 500): projekcja
# arkusza dodatkowo przycina, ale BEZ wielokropka — gdyby ten sufit urósł ponad tamten, arkusz
# ucinałby po cichu. Tu przycinamy z wielokropkiem, więc to jest właściwe miejsce na limit.
_MAX_LEN = 500
_MAX_PROMPTS = 3


def build_comment(
    *,
    llm_prose: str | None,
    commit_messages: list[str],
    prompt_texts: list[str],
    max_len: int = _MAX_LEN,
) -> str:
    """Opis dnia: proza LLM > komunikaty commitów > skrót promptów; przycięty do ``max_len``."""
    prose = (llm_prose or "").strip()
    if prose:
        return _cap(prose, max_len)
    parts = [_oneline(message) for message in commit_messages if message.strip()]
    if not parts:
        parts = [_oneline(text) for text in prompt_texts[:_MAX_PROMPTS] if text.strip()]
    return _cap("; ".join(parts).strip(), max_len)


def _oneline(text: str) -> str:
    return " ".join(text.split())


def _cap(text: str, max_len: int) -> str:
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"
