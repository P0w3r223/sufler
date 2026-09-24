"""Podział dużego zapytania na partie po dacie rozpoczęcia działalności — moduł czysty.

§C uzupelnienie-01.md: powyżej progu program nie startuje sam, tylko proponuje zawężenie
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
    """Jedna partia: zwykłe `Criteria` z zawężonym zakresem dat plus etykieta do tabeli.

    `od`/`do` to granice **kafla**, zawsze konkretne — na nich stoi arytmetyka `refine()`
    i etykieta. `otwarty_od`/`otwarty_do` mówią co innego: że tej granicy nie ma w zapytaniu
    wysyłanym do API, bo operator jej nie podał (ADR-0015). Rozdzielenie tych dwóch pojęć
    jest tu celowe — gdyby `od`/`do` mogły być `None`, każdy konsument planu musiałby
    obsłużyć brak daty, a `refine()` straciłby zakres do podziału."""

    criteria: Criteria
    label: str
    od: date
    do: date
    granularity: Granularity
    otwarty_od: bool = False
    otwarty_do: bool = False

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

    otwarty_od: bool = False
    otwarty_do: bool = False
    """Czy skrajne granice `covers` są planistyczne (uzupełnione), czy podane przez operatora.
    Ekran musi umieć napisać „…", bo inaczej obiecuje zakres, którego zapytanie nie ma."""

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


def _split(
    criteria: Criteria,
    od: date,
    do: date,
    granularity: Granularity,
    *,
    otwarty_od: bool = False,
    otwarty_do: bool = False,
) -> tuple[Batch, ...]:
    """Kafelkuje `[od, do]`. Skrajne kafle mogą być **otwarte**: nie wysyłają granicy,
    której operator nie podał (ADR-0015).

    Do audytu 2026-09-08 (A3) każda partia niosła obie granice, także te uzupełnione przez
    planer. Zapytanie podzielone wysyłało więc filtr, którego to samo zapytanie niepodzielone
    nie wysyła — a na bazie operatora poza oknem `1990-01-01…dziś` leżały **482 rekordy
    z 16 310 (2,96 %)**: 76 sprzed 1990 i 406 z datą rozpoczęcia w przyszłości, którą CEIDG
    przyjmuje. Podział jest proponowany właśnie dla dużych wyników, więc strata trafiała
    dokładnie w przebiegi, w których najbardziej boli."""
    kafle: list[tuple[date, date, date]] = []
    cursor = od
    while cursor <= do:
        period_start, period_end = _period_bounds(cursor, granularity)
        kafle.append((period_start, max(period_start, od), min(period_end, do)))
        cursor = period_end + _DAY

    batches: list[Batch] = []
    for i, (period_start, batch_od, batch_do) in enumerate(kafle):
        pierwszy = i == 0
        ostatni = i == len(kafle) - 1
        bez_dolnej = pierwszy and otwarty_od
        bez_gornej = ostatni and otwarty_do
        batches.append(
            Batch(
                criteria=criteria.model_copy(
                    update={
                        "data_od": None if bez_dolnej else batch_od,
                        "data_do": None if bez_gornej else batch_do,
                    }
                ),
                label=_label(
                    period_start, granularity, otwarty_od=bez_dolnej, otwarty_do=bez_gornej
                ),
                od=batch_od,
                do=batch_do,
                granularity=granularity,
                otwarty_od=bez_dolnej,
                otwarty_do=bez_gornej,
            )
        )
    return tuple(batches)


def _label(
    period_start: date, granularity: Granularity, *, otwarty_od: bool, otwarty_do: bool
) -> str:
    """Etykieta kafla, a przy otwartej krawędzi — tego, co partia naprawdę pobiera.

    Sam okres („1990-1999") byłby przy otwartej krawędzi nieprawdą tego samego rodzaju co
    defekt, który ADR-0015 naprawia: tabela podziału obiecywałaby zakres węższy niż zapytanie."""
    base = _period_label(period_start, granularity)
    if otwarty_od:
        return f"{base} i wcześniej"
    if otwarty_do:
        return f"{base} i później"
    return base


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
    otwarty_od = criteria.data_od is None
    otwarty_do = criteria.data_do is None
    od = criteria.data_od or floor
    do = criteria.data_do or today
    if od > do:
        raise ValueError(f"zakres dat jest pusty: {od} – {do}")

    chosen: Granularity = GRANULARITIES[-1]
    batches: tuple[Batch, ...] = ()
    for granularity in GRANULARITIES:
        batches = _split(
            criteria, od, do, granularity, otwarty_od=otwarty_od, otwarty_do=otwarty_do
        )
        chosen = granularity
        if count <= threshold * len(batches):
            break

    exhausted = count > threshold * len(batches)
    return BatchPlan(
        batches=batches,
        granularity=chosen,
        covers=(od, do),
        count=count,
        exhausted=exhausted,
        otwarty_od=otwarty_od,
        otwarty_do=otwarty_do,
    )


def refine(batch: Batch) -> tuple[Batch, ...]:
    """Dzieli jedną partię o poziom drobniej. Pusta krotka = miesiąc, drobniej nie schodzimy.

    Otwarta krawędź przechodzi na tę podpartię, która ją dziedziczy — pierwszą albo ostatnią.
    Bez tego podział partii skrajnej przywracałby granicę, której operator nie podał, czyli
    odtwarzałby defekt A3 o poziom niżej."""
    index = GRANULARITIES.index(batch.granularity)
    if index + 1 >= len(GRANULARITIES):
        return ()
    finer = GRANULARITIES[index + 1]
    return _split(
        batch.criteria,
        batch.od,
        batch.do,
        finer,
        otwarty_od=batch.otwarty_od,
        otwarty_do=batch.otwarty_do,
    )
