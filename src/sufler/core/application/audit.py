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
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sufler.core.domain.audit import project_arguments, project_verdict
from sufler.core.domain.metrics import pseudonymize

if TYPE_CHECKING:
    from sufler.core.domain.mutation import Verdict
    from sufler.core.ports.audit import AuditStore

logger = logging.getLogger(__name__)

# Rejestrator jednego wywołania narzędzia: (nazwa, argumenty od modelu, status "ok"/"error").
# Runtime widzi WYŁĄCZNIE ten kształt — ``TurnAudit`` niżej jest jego wołalną implementacją
# z dodatkowym wejściem dla werdyktu sędziego, którego runtime nie zna i znać nie musi.
ToolCallRecorder = Callable[[str, Mapping[str, Any], str], None]


def _utcnow() -> datetime:
    """Chwila zdarzenia (UTC, tz-aware) — wstrzykiwalna w testach przez ``clock``."""
    return datetime.now(tz=UTC)


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
    ) -> TurnAudit:
        """Domknij kontekst tury i zwróć rejestrator pojedynczego wywołania narzędzia.

        Pseudonim nadawcy i rozmowy liczymy RAZ na turę (nieodwracalny ``sha256[:16]``), nie przy
        każdym tool-callu. ``trust_class`` niesie realną klasę pochodzenia tury (T1/T2, ADR 0066
        §5); "unknown" zostaje dla drzwi bez rozszczepienia nadawcy — tam nie ma tożsamości do
        rozwiązania, więc udawanie klasy byłoby gorsze niż jej brak. Rejestrator jest best-effort.

        Zwracany typ jest KONKRETNY (``TurnAudit``), nie sam ``ToolCallRecorder``: runtime bierze
        go jako wołalny trzyargumentowy, a drzwi wołają na nim dodatkowo ``record_verdict``
        (ADR 0065 §8). Zwężenie do protokołu ukryłoby przed drzwiami to drugie wejście.
        """
        return TurnAudit(
            self._store,
            clock=self._clock,
            door=door,
            actor_key=pseudonymize(raw_user),
            conversation_key=pseudonymize(conversation_id),
            trust_class=trust_class,
        )

    def recent(self, limit: int = 200) -> list[dict[str, Any]]:
        """Ostatnie wpisy dziennika — do odtworzenia przebiegu po incydencie."""
        return self._store.recent(limit)


class TurnAudit:
    """Rejestrator wywołań narzędzi JEDNEJ tury — wołalny, z gniazdem na werdykt sędziego.

    Wołalny, bo runtime zna tylko ``ToolCallRecorder`` (trzy argumenty) i nie ma powodu wiedzieć
    o mutacjach bazy wiedzy. Obiekt zamiast domknięcia, bo werdykt sędziego (ADR 0065 §8) rodzi
    się WEWNĄTRZ narzędzia ``File``, a wiersz audytu powstaje dopiero po jego powrocie, w
    ``AgentRuntime._dispatch`` — potrzebne jest więc miejsce, w którym werdykt poczeka na swój
    wiersz. ADR 0065 nazywa to „czwartym argumentem rejestratora"; gniazdo daje to samo, nie
    zmuszając runtime'u do przenoszenia wartości, której nie rozumie, przez trzy warstwy.

    **Dlaczego to bezpieczne mimo stanu.** Gniazdo żyje w obiekcie JEDNEJ tury (``turn_recorder``
    tworzy nowy przy każdej wiadomości), a tura biegnie w jednym wątku: ``_dispatch`` woła
    narzędzia po kolei, generatorem, nie równolegle. Gniazdo jest **zużywane** przy zapisie —
    czyszczone bezwarunkowo, także gdy zapis padnie — więc werdykt jednego wywołania nie ma jak
    dokleić się do wiersza następnego. To jest cały inwariant: jeden werdykt, jeden wiersz.
    """

    def __init__(
        self,
        store: AuditStore,
        *,
        clock: Callable[[], datetime],
        door: str,
        actor_key: str,
        conversation_key: str,
        trust_class: str,
    ) -> None:
        self._store = store
        self._clock = clock
        self._door = door
        self._actor_key = actor_key
        self._conversation_key = conversation_key
        self._trust_class = trust_class
        self._pending_verdict: str | None = None

    def record_verdict(self, verdict: Verdict, reason: str = "") -> None:
        """Odłóż werdykt sędziego dla WŁAŚNIE wykonywanego wywołania narzędzia.

        Wołane z bramki mutacji (``build_file_catalog``), zanim narzędzie wróci do ``_dispatch``.
        Best-effort jak cały audyt: gdyby projekcja rzuciła, mutacja nie może przez to paść.
        """
        try:
            self._pending_verdict = project_verdict(verdict, reason)
        except Exception:
            logger.warning("Nie udało się zredagować werdyktu sędziego do audytu — pomijam")

    def __call__(self, tool_name: str, arguments: Mapping[str, Any], status: str) -> None:
        """Dopisz wiersz audytu; zużyj odłożony werdykt, jeśli jakiś czeka."""
        verdict, self._pending_verdict = self._pending_verdict, None
        try:
            self._store.record_tool_call(
                occurred_at=self._clock(),
                actor_key=self._actor_key,
                conversation_key=self._conversation_key,
                door=self._door,
                tool_name=tool_name,
                arg_summary=json.dumps(
                    project_arguments(arguments), ensure_ascii=False, default=str
                ),
                status=status,
                trust_class=self._trust_class,
                judge_verdict=verdict,
            )
        except Exception:
            logger.warning("Nie udało się zapisać wpisu audytu narzędzia %r — pomijam", tool_name)
