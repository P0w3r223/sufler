"""Limiter żądań: dwa okna przesuwne + odstęp minimalny + blokada po 429 (ADR-0003).

`acquire()` jest jedyną bramką dla każdego żądania, także ponowień. Historia żądań
jest protokołem — w produkcji trwała (SQLite), w testach w pamięci. Obliczenia idą
w dziedzinie zegara monotonicznego: własne żądania procesu są pamiętane monotonicznie,
a znaczniki z historii (czas ścienny, także z poprzednich procesów) przeliczane przy
każdym wywołaniu. Skok zegara systemowego nie skraca więc ani odstępu, ani okien,
ani blokady po 429 dla żądań wysłanych w tym procesie.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from .clock import Clock
from .errors import ResumableError
from .progress import Events, NullEvents

REASON_SPACING = "odstep"
REASON_WINDOW = "okno_limitu"
REASON_COOLDOWN = "blokada_429"
REASON_BACKOFF = "ponowienie"
REASON_RESUME = "wznowienie"
REASON_BUDGET = "budzet_api"
# Powód spoza limitera, ale z tego samego zbioru: `console` rozpoznaje wszystkie po nazwie
# i musi je brać z jednego miejsca, inaczej zmiana wartości ucisza komunikat zamiast go zmienić.
REASON_NO_CONNECTION = "brak_polaczenia"
# Ponowienie po odmowie modelu (429 albo 5xx). Własny powód, bo pauza jest krótka i inaczej
# uzasadniona niż limit CEIDG — operator ma wiedzieć, że czeka na asystenta, nie na rejestr.
REASON_MODEL_RETRY = "model"

BUDGET_RESERVE_DEFAULT = 10
"""Ile żądań z budżetu godzinnego zgłaszanego przez serwer zostawiamy nietkniętych.

Własne okna limitera liczą wyłącznie żądania, które przeszły przez `request_log`. Limit jest
jednak nakładany na **token**, więc sonda, druga maszyna albo Postman zużywają ten sam budżet,
a limiter ich nie widzi. `X-Rate-Limit-Remaining` to jedyne źródło prawdy o tym zużyciu —
rezerwa zostaje na ponowienia, których jeszcze nie znamy."""

# Najdłuższy pojedynczy sen limitera. Postój bywa przycięty do najdłuższego okna, czyli 3600 s,
# a dzierżawa blokady bazy gaśnie po 600 s — jedno `sleep(3600)` zabijało ją pod pracującym
# procesem i zostawiało w logu dziurę nie do odróżnienia od śpiącej maszyny. Sen w plastrach
# daje `heartbeat` szansę odezwać się w środku czekania, a nie dopiero po nim. 300 s to
# połowa dzierżawy: mieści się pod nią z zapasem, a nie tnie postojów, które i tak są krótsze.
WAIT_SLICE_S = 300.0
"""Najdłuższy pojedynczy sen limitera; musi być **mniejszy** niż
`store.DEFAULT_LOCK_STALE_S`, inaczej dzierżawa gaśnie w środku czekania.
Zależności nie da się tu zapisać importem (reguła granic: `ratelimit` nie zna bazy),
więc pilnuje jej test — samo porównanie wyniku z tą stałą byłoby tautologią."""
# Znaczniki epoch (~1.7e9) mają w float precyzję ~2.4e-7 s; bez tolerancji pętla
# oczekiwania mogłaby kręcić się na resztkach zaokrągleń.
_WAIT_EPSILON_S = 0.005
_MAX_WAIT_ITERATIONS = 1000


class LimiterStalledError(ResumableError):
    """Limiter nie doszedł do wolnego slotu — niespójny zegar albo historia."""


@dataclass(frozen=True)
class RequestStamp:
    """Jedno żądanie w historii: czas ścienny (epoch), endpoint, status (None = w toku)."""

    ts_epoch: float
    endpoint: str
    status: int | None = None


class RequestHistory(Protocol):
    """Historia żądań tokenu na danym środowisku."""

    def recent(self, since_epoch: float) -> Sequence[RequestStamp]: ...

    def record(self, ts_epoch: float, endpoint: str) -> None: ...

    def mark(self, ts_epoch: float, status: int) -> None: ...


class InMemoryHistory:
    """Historia w pamięci procesu — do testów i trybów bez bazy."""

    def __init__(self, initial: Sequence[RequestStamp] = ()) -> None:
        self._items: list[RequestStamp] = list(initial)

    def recent(self, since_epoch: float) -> Sequence[RequestStamp]:
        return [s for s in self._items if s.ts_epoch >= since_epoch]

    def record(self, ts_epoch: float, endpoint: str) -> None:
        self._items.append(RequestStamp(ts_epoch, endpoint))

    def mark(self, ts_epoch: float, status: int) -> None:
        for i in range(len(self._items) - 1, -1, -1):
            if self._items[i].ts_epoch == ts_epoch:
                self._items[i] = RequestStamp(ts_epoch, self._items[i].endpoint, status)
                return

    def __len__(self) -> int:
        return len(self._items)


class RateLimiter:
    """Jedna bramka dla wszystkich żądań HTTP."""

    def __init__(
        self,
        *,
        windows: Sequence[tuple[int, float]],
        min_spacing_s: float,
        cooldown_s: float,
        clock: Clock,
        history: RequestHistory,
        events: Events | None = None,
        budget_reserve: int = BUDGET_RESERVE_DEFAULT,
        # Wywołanie zwrotne, a nie `Events`: niesie bicie serca blokady, a blokada należy do
        # `pipeline` (reguła granic 5) — limiter nie ma prawa znać bazy. Ten sam wzorzec co
        # `DownloadProgress` w `client`.
        heartbeat: Callable[[], object] | None = None,
    ) -> None:
        if not windows:
            raise ValueError("limiter wymaga co najmniej jednego okna")
        for limit, span in windows:
            if limit <= 0 or span <= 0:
                raise ValueError(f"okno limitera musi być dodatnie: ({limit}, {span})")
        self._windows = tuple((int(limit), float(span)) for limit, span in windows)
        self._min_spacing_s = float(min_spacing_s)
        self._cooldown_s = float(cooldown_s)
        self._clock = clock
        self._history = history
        self._events: Events = events or NullEvents()
        self._own: list[tuple[float, float, int | None]] = []  # (mono, wall, status) własne
        self._last_attempt_mono: float | None = None
        self._last_attempt_wall: float | None = None
        self._blocked_until_mono: float | None = None
        self._resume_until_mono: float | None = None
        self._budget_reserve = int(budget_reserve)
        self._budget_until_mono: float | None = None
        self._heartbeat = heartbeat

    def _sleep_in_slices(self, wait: float) -> None:
        """Przesypia `wait` w plastrach, bijąc w blokadę przed każdym z nich.

        Bicie idzie **przed** plastrem, nie po nim — tak samo jak `on_wait` wyprzedza całe
        czekanie. Dzięki temu najdłuższa przerwa między dwoma biciami to `WAIT_SLICE_S`,
        a nie długość postoju: dzierżawa nie gaśnie pod czekającym procesem, a wykrywanie
        snu maszyny znów mierzy to, co ma mierzyć — skok zegara **bez** naszego udziału."""
        pozostalo = wait
        while pozostalo > _WAIT_EPSILON_S:
            if self._heartbeat is not None:
                self._heartbeat()
            plaster = min(pozostalo, WAIT_SLICE_S)
            self._clock.sleep(plaster)
            pozostalo -= plaster

    @property
    def lookback_s(self) -> float:
        return max(max(span for _, span in self._windows), self._cooldown_s)

    def acquire(self, endpoint: str, *, extra_delay_s: float = 0.0) -> None:
        """Blokuje do chwili, gdy żądanie jest dozwolone, i zapisuje próbę w historii."""
        backoff_until: float | None = (
            self._clock.monotonic() + extra_delay_s if extra_delay_s > 0 else None
        )
        for _ in range(_MAX_WAIT_ITERATIONS):
            wait, reason, resume_at_wall = self._next_slot(backoff_until)
            if wait <= _WAIT_EPSILON_S:
                break
            self._events.on_wait(wait, reason, resume_at_wall)
            self._sleep_in_slices(wait)
        else:
            raise LimiterStalledError(
                "Limiter nie doszedł do wolnego slotu — sprawdź zegar systemowy i bazę "
                "historii żądań, potem wznów pobieranie."
            )

        wall = self._clock.wall()
        mono = self._clock.monotonic()
        self._history.record(wall, endpoint)
        self._own.append((mono, wall, None))
        self._own = [entry for entry in self._own if entry[0] > mono - self.lookback_s]
        self._last_attempt_mono = mono
        self._last_attempt_wall = wall
        self._blocked_until_mono = None

    def enforce_resume_gap(self, gap_s: float) -> float:
        """Po wznowieniu: brakująca reszta `gap_s` od ostatniego żądania z historii.

        Zwraca liczbę sekund, które trzeba odczekać (0, gdy ostatnie żądanie było dawno).
        """
        mono_now = self._clock.monotonic()
        wall_now = self._clock.wall()
        stamps = [
            s.ts_epoch for s in self._history.recent(wall_now - gap_s) if s.ts_epoch <= wall_now
        ]
        if not stamps:
            return 0.0
        last_mono = mono_now - (wall_now - max(stamps))
        self._resume_until_mono = last_mono + gap_s
        return max(0.0, self._resume_until_mono - mono_now)

    def note_response(self, status: int, retry_after_s: float | None = None) -> None:
        """Rejestruje status; 429 uruchamia blokadę liczoną od ostatniej próby."""
        if self._last_attempt_wall is not None:
            self._history.mark(self._last_attempt_wall, status)
        if self._own:
            mono, wall, _ = self._own[-1]
            self._own[-1] = (mono, wall, status)
        if status == 429:
            base = (
                self._last_attempt_mono
                if self._last_attempt_mono is not None
                else self._clock.monotonic()
            )
            self._blocked_until_mono = base + max(self._cooldown_s, retry_after_s or 0.0)

    def note_budget(self, remaining: int | None, reset_epoch: float | None) -> float:
        """Hamulec oparty na budżecie, który raportuje serwer (`X-Rate-Limit-*`).

        Własne okna widzą tylko żądania z `request_log`, a limit jest na token — sonda albo
        druga maszyna zużywają go niewidzialnie. Gdy serwer mówi, że zostało mniej niż
        rezerwa, czekamy do jego własnego momentu resetu zamiast dobijać do 429.

        Hamuje wyłącznie, gdy znane są **obie** liczby: bez czasu resetu nie wiadomo, jak
        długo czekać, a zgadywanie godziny postoju na podstawie samego licznika byłoby gorsze
        od jednego 429. Postój przycinamy do najdłuższego okna — błędny albo odległy
        `reset` nie może zatrzymać pracy na dłużej, niż trwa całe okno limitu.

        Zwraca długość ustawionego postoju w sekundach (0 = brak hamowania)."""
        if remaining is None or reset_epoch is None or remaining > self._budget_reserve:
            return 0.0
        longest = max(span for _, span in self._windows)
        wait = min(max(0.0, reset_epoch - self._clock.wall()), longest)
        if wait <= 0.0:
            return 0.0
        candidate = self._clock.monotonic() + wait
        if self._budget_until_mono is None or candidate > self._budget_until_mono:
            self._budget_until_mono = candidate
        return wait

    def _next_slot(self, backoff_until: float | None) -> tuple[float, str, float]:
        """Zwraca (ile czekać, powód, kiedy wznowić [epoch]); 0 = można wysyłać."""
        mono_now = self._clock.monotonic()
        wall_now = self._clock.wall()
        since = wall_now - self.lookback_s
        own_walls = {wall for _, wall, _ in self._own}

        # znaczniki cudze (inny proces) z historii → dziedzina monotoniczna, „przyszłe”
        # (skok zegara) pomijamy; własne bierzemy wprost z pamięci procesu
        history_mono: list[tuple[float, int | None]] = [
            (max(0.0, mono_now - (wall_now - s.ts_epoch)), s.status)
            for s in self._history.recent(since)
            if s.ts_epoch <= wall_now and s.ts_epoch not in own_walls
        ]
        history_mono.extend((mono, status) for mono, _, status in self._own)
        history_mono.sort(key=lambda pair: pair[0])

        earliest = mono_now
        reason = ""

        last_mono = max((t for t, _ in history_mono), default=None)
        if self._last_attempt_mono is not None:
            last_mono = (
                self._last_attempt_mono
                if last_mono is None
                else max(last_mono, self._last_attempt_mono)
            )
        if last_mono is not None and last_mono + self._min_spacing_s > earliest:
            earliest, reason = last_mono + self._min_spacing_s, REASON_SPACING

        for limit, span in self._windows:
            in_window = [t for t, _ in history_mono if t > mono_now - span]
            if len(in_window) >= limit:
                candidate = in_window[-limit] + span
                if candidate > earliest:
                    earliest, reason = candidate, REASON_WINDOW

        blocked = self._blocked_until_mono
        for t, status in history_mono:
            if status == 429:
                candidate = t + self._cooldown_s
                blocked = candidate if blocked is None else max(blocked, candidate)
        if blocked is not None and blocked > earliest:
            earliest, reason = blocked, REASON_COOLDOWN

        if self._budget_until_mono is not None and self._budget_until_mono > earliest:
            earliest, reason = self._budget_until_mono, REASON_BUDGET

        if self._resume_until_mono is not None and self._resume_until_mono > earliest:
            earliest, reason = self._resume_until_mono, REASON_RESUME

        if backoff_until is not None and backoff_until > earliest:
            earliest, reason = backoff_until, REASON_BACKOFF

        wait = earliest - mono_now
        return wait, reason, wall_now + max(wait, 0.0)
