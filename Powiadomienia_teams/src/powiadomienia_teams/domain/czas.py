"""Znaczniki czasu w formacie ISO 8601 używanym przez Graph — parsowanie i formatowanie.

Mieszkało to w ``graph.mapping``, czyli w adapterze, mimo że ``reminders.lifecycle``
i ``reminders.replies`` (czysta logika, bez I/O) muszą parsować dokładnie te same znaczniki:
watermark rozmowy i ``createdDateTime`` wiadomości. Czysta logika importowała więc adapter,
domykając cykl na poziomie pakietów.

To są dwie funkcje bez żadnej wiedzy o HTTP — należą do warstwy, od której zależą wszyscy.
``graph.mapping`` re-eksportuje je, więc dotychczasowe importy nadal działają.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

_UTC = timezone.utc
# Graph bywa 7-cyfrowy w ułamku sekundy, a `fromisoformat` poniżej 3.11 przyjmuje najwyżej 6.
_FRACTION = re.compile(r"\.(\d+)")


def parse_graph_datetime(value: str) -> datetime:
    """ISO 8601 z Graph (``…Z``) → tz-aware ``datetime`` (UTC).

    Znosi ułamek sekundy dłuższy niż 6 cyfr (Graph bywa 7-cyfrowy, a ``fromisoformat``
    poniżej Pythona 3.11 tego nie przyjmuje).
    """
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    v = _FRACTION.sub(lambda m: "." + m.group(1)[:6], v)
    return datetime.fromisoformat(v)


def to_graph_iso(dt: datetime) -> str:
    """tz-aware ``datetime`` → ISO 8601 UTC z sufiksem ``Z`` (format oczekiwany przez Graph).

    Odwrotność ``parse_graph_datetime``. Wspólny formatter dla klienta Graph (ciała żądań) i
    watermarku przypomnień — jedno źródło formatu, brak rozjazdu między adapterem a orkiestracją.
    """
    return dt.astimezone(_UTC).isoformat().replace("+00:00", "Z")
