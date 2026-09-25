"""Czysta logika CI dla mostu GitHub (ADR 0024, Faza 2) — deterministyczna, bez I/O i bez LLM.

Dwa czyste kawałki używane przez ``CiAutoCommentService``: wyłuskanie numeru PR z kanonicznego
``url`` zdarzenia CI (``…/pull/{n}``) oraz złożenie treści auto-komentarza. Treść jest SKŁADANA
z pól JUŻ zsanityzowanych zdarzenia (``title``/``summary`` przeszły ``reject_dangerous_content``
przy ingest) — bez interpretacji, bez modelu językowego, więc nie ma jak wstrzyknąć zmyślonej
treści ani sekretu (mapper CI i tak nie wciąga logów/tokenów — biała lista pól, ADR 0024 §D).
"""

from __future__ import annotations

import re

_PULL_NUMBER_RE = re.compile(r"/pull/(\d+)")


def pr_number_from_url(url: str) -> int | None:
    """Numer PR z kanonicznego ``url`` zdarzenia CI (``…/pull/{n}``); ``None`` gdy nie dotyczy PR.

    Zdarzenie ``ci_failure`` niezwiązane z PR ma ``url`` wskazujący na przebieg (bez ``/pull/``) —
    wtedy nie ma czego komentować, więc zwracamy ``None`` (serwis pomija takie zdarzenie).
    """
    match = _PULL_NUMBER_RE.search(url or "")
    return int(match.group(1)) if match else None


def render_ci_failure_comment(title: str, summary: str) -> str:
    """Złóż DETERMINISTYCZNY (nie-LLM) komentarz o porażce CI z pól zdarzenia.

    Czysta kompozycja Markdown — bierze gotowe, zsanityzowane ``title`` (nazwa workflow + PR) i
    ``summary`` (link do przebiegu). Stopka oznacza wiadomość jako automatyczną (transparentność:
    czytelnik wie, że to bot, nie człowiek). Żadnego zgadywania — treść pochodzi tylko ze zdarzenia.
    """
    parts = ["⚠️ **CI: porażka** (powiadomienie automatyczne)"]
    if title:
        parts.append(title)
    if summary:
        parts.append(summary)
    parts.append("_Komentarz wygenerowany automatycznie przez Sufler — bez modelu językowego._")
    return "\n\n".join(parts)
