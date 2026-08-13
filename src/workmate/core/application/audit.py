"""Przypadek użycia dziennika audytu (Faza 0, ADR 0067).

Cienka orkiestracja nad portem ``AuditStore`` + czyste funkcje domeny (pseudonim + projekcja
argumentów). ``turn_recorder`` domyka kontekst tury — pseudonim nadawcy i rozmowy policzone RAZ,
drzwi, klasa zaufania — i zwraca rejestrator POJEDYNCZEGO wywołania narzędzia, wołany z pętli
runtime'u (``AgentRuntime._dispatch``), gdzie widać nazwę/argumenty/status każdego tool-calla.

Ten szew (rejestrator per turę, wołany w ``_dispatch``) zastąpił pierwotny pomysł owijki na
``ToolSpec.fn``: koercja argumentów runtime'u introspekcjuje ``fn`` (``inspect.signature`` /
``get_type_hints`` po napisowych adnotacjach), a owinięcie to psuje; dodatkowo katalog bazowy żyje
we współdzielonym runtime, więc nie da się go owinąć kontekstem PER TURĘ. Rejestrator podany do
``run_turn`` jest addytywny — jak ``session_header``/``extra_tools`` — i obejmuje oba katalogi w
jednym punkcie. Poprawka wykonawcza zapisana w ADR 0067.

Zapis jest BEST-EFFORT (ADR 0067 §1.1 / wzorzec metryk): audyt nie może wywrócić tury, więc
rejestrator łapie WŁASNE wyjątki i loguje — runtime woła go bez osłony.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from workmate.core.domain.audit import project_arguments
from workmate.core.domain.metrics import pseudonymize

if TYPE_CHECKING:
    from workmate.core.ports.audit import AuditStore

logger = logging.getLogger(__name__)

# Rejestrator jednego wywołania narzędzia: (nazwa, argumenty od modelu, status "ok"/"error").
ToolCallRecorder = Callable[[str, Mapping[str, Any], str], None]


def _utcnow() -> datetime:
    """Chwila zdarzenia (UTC, tz-aware) — wstrzykiwalna w testach przez ``clock``."""
    return datetime.now(tz=timezone.utc)


class AuditService:
    """Rejestruje wywołania narzędzi per turę (pseudonim nadawcy/rozmowy, argumenty zredagowane)."""

    def __init__(self, store: AuditStore, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._store = store
        self._clock = clock

    def turn_recorder(
        self,
        *,
        door: str,
        raw_user: str,
        conversation_id: str,
        trust_class: str = "unknown",
    ) -> ToolCallRecorder:
        """Domknij kontekst tury i zwróć rejestrator pojedynczego wywołania narzędzia.

        Pseudonim nadawcy i rozmowy liczymy RAZ na turę (nieodwracalny ``sha256[:16]``), nie przy
        każdym tool-callu. ``trust_class`` jest dziś jednolite ("unknown") — realną klasę T0–T3
        dowiąże ADR 0066 (to jest jego pole-szew, ADR 0066 §5). Rejestrator jest best-effort.
        """
        actor_key = pseudonymize(raw_user)
        conversation_key = pseudonymize(conversation_id)

        def record(tool_name: str, arguments: Mapping[str, Any], status: str) -> None:
            try:
                self._store.record_tool_call(
                    occurred_at=self._clock(),
                    actor_key=actor_key,
                    conversation_key=conversation_key,
                    door=door,
                    tool_name=tool_name,
                    arg_summary=json.dumps(
                        project_arguments(arguments), ensure_ascii=False, default=str
                    ),
                    status=status,
                    trust_class=trust_class,
                    judge_verdict=None,
                )
            except Exception:
                logger.warning(
                    "Nie udało się zapisać wpisu audytu narzędzia %r — pomijam", tool_name
                )

        return record

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Ostatnie wpisy dziennika — do odtworzenia przebiegu po incydencie."""
        return self._store.recent(limit)
