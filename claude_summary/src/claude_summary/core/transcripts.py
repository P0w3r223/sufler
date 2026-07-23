"""Dyskryminator i parser realnych promptów człowieka z linii transkryptu Claude Code.

Sedno narzędzia: linia ``type:"user"`` NIE jest automatycznie promptem człowieka — mogą to
być tool-resulty, powiadomienia sub-agentów (``task-notification``), rozwinięcia slash-komend
czy tury sub-agenta (``isSidechain``). ``is_human_prompt`` przepuszcza wyłącznie realnie wpisany
tekst; ``strip_injected`` defensywnie ucina doklejone przez harness bloki (``<system-reminder>``…).

Funkcje są CZYSTE (operują na już-sparsowanym słowniku), więc pełny zestaw wariantów da się
przetestować bez dotykania dysku.
"""

from __future__ import annotations

import re
from typing import Any

from claude_summary.core.models import Prompt
from claude_summary.core.redaction import redact_text, sanitize_prompt
from claude_summary.core.timeutil import parse_iso

# Źródła promptu uznawane za realny wkład człowieka. Samo "typed" wystarcza do odsiania
# tool-resultów/komend/powiadomień; "suggestion_accepted" i "queued" to również akcje człowieka.
_HUMAN_PROMPT_SOURCES = frozenset({"typed", "suggestion_accepted", "queued"})

# Doklejane przez harness bloki, które NIE są tekstem człowieka. Domknięte pary wycinamy
# w dowolnym miejscu; niedomknięty ogon obcinamy tylko dla <system-reminder> (jedyny, który
# harness dokleja do realnie wpisanego promptu — reszta przychodzi jako osobne, odsiane linie).
_INJECTED_TAGS = (
    "system-reminder",
    "command-name",
    "command-message",
    "command-args",
    "local-command-stdout",
    "local-command-caveat",
    "bash-input",
    "bash-stdout",
)
_INJECTED_BLOCK = re.compile(r"<(" + "|".join(_INJECTED_TAGS) + r")>.*?</\1>", re.DOTALL)
_SYSTEM_REMINDER_TAIL = re.compile(r"<system-reminder>.*\Z", re.DOTALL)


def is_human_prompt(line: dict[str, Any]) -> bool:
    """Czy linia transkryptu to realnie wpisany przez człowieka prompt (nie wstrzyknięta treść)."""
    if line.get("type") != "user":
        return False
    if line.get("isSidechain") is True:
        return False
    if "toolUseResult" in line:  # tool-result ma listę bloków w content i to pole na górze
        return False
    if line.get("promptSource") not in _HUMAN_PROMPT_SOURCES:
        return False
    origin = line.get("origin")
    if not isinstance(origin, dict) or origin.get("kind") != "human":
        return False
    message = line.get("message")
    if not isinstance(message, dict):
        return False
    return isinstance(message.get("content"), str)


def strip_injected(text: str) -> str:
    """Usuń doklejone bloki harnessu i przytnij białe znaki (zostaje sam tekst człowieka)."""
    cleaned = _INJECTED_BLOCK.sub("", text)
    cleaned = _SYSTEM_REMINDER_TAIL.sub("", cleaned)
    return cleaned.strip()


def parse_prompt(line: dict[str, Any], *, project: str) -> Prompt | None:
    """Zbuduj ``Prompt`` z linii; ``None``, gdy treść/znacznik czasu są niepoprawne (pomiń wpis).

    Zakłada wstępny przesiew ``is_human_prompt``, ale sam też waliduje kształt — dzięki temu
    jest bezpieczny w użyciu samodzielnym i odporny na pojedyncze uszkodzone linie.
    """
    message = line.get("message")
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    stripped = strip_injected(content)
    if not stripped:
        return None
    # Redakcja NA GRANICY: surowa treść nie wchodzi do modelu. Sam zrzut bez instrukcji → pomiń.
    sanitized = sanitize_prompt(stripped)
    if sanitized.dropped or not sanitized.text:
        return None
    timestamp_raw = line.get("timestamp")
    if not isinstance(timestamp_raw, str):
        return None
    try:
        timestamp = parse_iso(timestamp_raw)
    except ValueError:
        return None
    return Prompt(
        timestamp=timestamp,
        text=sanitized.text,
        session_id=str(line.get("sessionId", "")),
        cwd=str(line.get("cwd", "")),
        project=redact_text(project),  # nazwa folderu niesie nazwę użytkownika (ścieżkę)
        redactions=tuple(sorted(sanitized.categories)),
    )
