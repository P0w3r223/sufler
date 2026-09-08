"""Podział dużego zapytania na partie po dacie rozpoczęcia działalności — moduł czysty.

UZUPELNIENIE_01 §C: powyżej progu program nie startuje sam, tylko proponuje zawężenie
kryteriów albo podział na partie z szacunkiem czasu dla każdej.

Dlaczego akurat data rozpoczęcia (ADR-0008, decyzja 3): to jedyne kryterium API, które
da się uporządkować, a partie są rozłączne i wyczerpujące na wybranym zakresie. Podział po
województwie nie jest wyczerpujący (wpisy bez adresu albo z adresem zagranicznym zniknęłyby
po cichu), a podział po statusie jest skrajnie nierówny.

Planer jest deterministyczny: te same kryteria dają te same partie, więc po przerwaniu
wystarczy przeliczyć plan i rozpoznać partie po odcisku kryteriów (`Criteria.fingerprint`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

from .criteria import Criteria

# Rejestr CEIDG zaczyna się w praktyce dużo później, ale stała musi być poza zakresem
# realnych wpisów i stała w czasie — inaczej plan przestałby być powtarzalny.
DATE_FLOOR = date(1990, 1, 1)

Granularity = Literal["dekada", "rok", "kwartal", "miesiac"]
GRANULARITIES: tuple[Granularity, ...] = ("dekada", "rok", "kwartal", "miesiac")

GRANULARITY_LABEL: dict[str, str] = {
    "dekada": "dekadami",
    "rok": "latami",
    "kwartal": "kwartałami",
    "miesiac": "miesiącami",
}

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Batch:
    """Jedna partia: zwykłe `Criteria` z zawężonym zakresem dat plus etykieta do tabeli."""

    criteria: Criteria
    label: str
    od: date
    do: date
    granularity: Granularity

    def fingerprint(self) -> str:
        return self.criteria.fingerprint()


@dataclass(frozen=True)
class BatchPlan:
    """Plan podziału: rozłączne partie pokrywające dokładnie zakres `covers`."""

    batches: tuple[Batch, ...]
    granularity: Granularity
    covers: tuple[date, date]
    count: int
    exhausted: bool = False
    """`True`, gdy nawet miesięczne partie nie schodzą poniżej progu — trzeba zawęzić
    kryteria inaczej niż datą."""

    @property
    def share(self) -> int:
        """Szacowana liczba trafień na partię przy równym rozłożeniu w czasie."""
        if not self.batches:
            return 0
        return -(-self.count // len(self.batches))


def _period_bounds(day: date, granularity: Granularity) -> tuple[date, date]:
    if granularity == "dekada":
        start_year = day.year - day.year % 10
        return date(start_year, 1, 1), date(start_year + 9, 12, 31)
    if granularity == "rok":
        return date(day.year, 1, 1), date(day.year, 12, 31)
    if granularity == "kwartal":
        first_month = 3 * ((day.month - 1) // 3) + 1
        start = date(day.year, first_month, 1)
        end_month = first_month + 3
        end = (date(day.year + 1, 1, 1) if end_month > 12 else date(day.year, end_month, 1)) - _DAY
        return start, end
    start = date(day.year, day.month, 1)
    end = (date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)) - _DAY
    return start, end


def _period_label(start: date, granularity: Granularity) -> str:
    if granularity == "dekada":
        return f"{start.year}-{start.year + 9}"
    if granularity == "rok":
        return str(start.year)
    if granularity == "kwartal":
        return f"{start.year}-Q{(start.month - 1) // 3 + 1}"
    return f"{start.year}-{start.month:02d}"


def _split(criteria: Criteria, od: date, do: date, granularity: Granularity) -> tuple[Batch, ...]:
    batches: list[Batch] = []
    cursor = od
    while cursor <= do:
        period_start, period_end = _period_bounds(cursor, granularity)
        batch_od = max(period_start, od)
        batch_do = min(period_end, do)
        batches.append(
            Batch(
                criteria=criteria.model_copy(update={"data_od": batch_od, "data_do": batch_do}),
                label=_period_label(period_start, granularity),
                od=batch_od,
                do=batch_do,
                granularity=granularity,
            )
        )
        cursor = period_end + _DAY
    return tuple(batches)


def plan_batches(
    criteria: Criteria,
    count: int,
    *,
    today: date,
    threshold: int,
    floor: date = DATE_FLOOR,
) -> BatchPlan:
    """Najgrubszy podział, przy którym średnia partia mieści się w progu.

    Brakujące granice zakresu uzupełnia `floor` i `today`, żeby plan był powtarzalny
    między sesjami — pytanie użytkownika o rok początkowy zepsułoby wznawianie.
    """
    od = criteria.data_od or floor
    do = criteria.data_do or today
    if od > do:
        raise ValueError(f"zakres dat jest pusty: {od} – {do}")

    chosen: Granularity = GRANULARITIES[-1]
    batches: tuple[Batch, ...] = ()
    for granularity in GRANULARITIES:
        batches = _split(criteria, od, do, granularity)
        chosen = granularity
        if count <= threshold * len(batches):
            break

    exhausted = count > threshold * len(batches)
    return BatchPlan(
        batches=batches, granularity=chosen, covers=(od, do), count=count, exhausted=exhausted
    )


def refine(batch: Batch) -> tuple[Batch, ...]:
    """Dzieli jedną partię o poziom drobniej. Pusta krotka = miesiąc, drobniej nie schodzimy."""
    index = GRANULARITIES.index(batch.granularity)
    if index + 1 >= len(GRANULARITIES):
        return ()
    finer = GRANULARITIES[index + 1]
    return _split(batch.criteria, batch.od, batch.do, finer)
