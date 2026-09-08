"""Kanoniczna postać identyfikatora wpisu — moduł czysty, bez sieci i bez bazy.

Rejestr zwraca **jeden** identyfikator w **dwóch** pisowniach: `/firmy` i `/firma` wielkimi
literami, `/zmiana` małymi (zmierzone, `docs/decisions.md`). Program traktował pisownię jak
tożsamość, więc `aktualizuj` zapisywał każdą zmienioną firmę dwa razy — raz jako pustą
zaślepkę przypiętą do runu, raz jako komplet danych przypięty do niczego — a `stale_detail_ids`
nie mógł trafić w cache ani razu. Nocny przebieg z 2026-09-08 kupił 13 401 rekordów za 2 681
żądań i nie pokazał żadnego (ADR-0013).

Stąd niezmiennik: **`id` wpisu jest wartością, nie napisem.** Dwa rekordy opisują ten sam
wpis wtedy i tylko wtedy, gdy ich identyfikatory są równe po kanonizacji; kanonizacja ma
jedno miejsce (ten moduł), stosuje się ją na wejściu (`client._records_of`), a `KanonicznyId`
sprawia, że mypy strict pyta o nią za nas przy każdym przejściu do bazy.

Wielkie litery, bo taką pisownię rejestr sam produkuje na obu endpointach niosących dane —
narzędzie nigdy nie wysyła zapisu, którego API by nie zwróciło.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import NewType

KanonicznyId = NewType("KanonicznyId", str)

GUID_WPISU = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
"""Kształt identyfikatora wpisu: 8-4-4-4-12 i **wyłącznie szesnastkowo**.

Ta ostatnia część jest nośna, nie ozdobna. Identyfikator z `/raporty` ma ten sam kształt
(`F3Aj3APe-9rud-A9rR-IId9-jMe3FedeR3e9`), ale nie jest szesnastkowy i jest istotny co do
wielkości liter, bo wchodzi wprost do adresu pobrania archiwum. Identyfikatory wierszy
raportu (`NIP:…`, `REGON:…`, `HASH:<małe hex>`) też nie są GUID-ami. Rozluźnienie tego
wzorca do `[0-9A-Za-z]` zepsułoby pobieranie raportów i dorobiło wierszom raportu drugą
tożsamość — czyli ten sam defekt, tylko na drugim źródle.
"""


def kanoniczny_id(value: str) -> KanonicznyId:
    """Identyfikator wpisu w postaci kanonicznej; cokolwiek innego przechodzi bez zmian."""
    return KanonicznyId(value.upper() if GUID_WPISU.fullmatch(value) else value)


def kanoniczne_id(values: Iterable[str]) -> list[KanonicznyId]:
    """Kanonizuje sekwencję identyfikatorów, zachowując kolejność i powtórzenia."""
    return [kanoniczny_id(v) for v in values]
