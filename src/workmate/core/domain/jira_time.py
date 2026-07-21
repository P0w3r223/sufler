"""Znaczniki czasu na granicy Jiry — parsowanie odpowiedzi i składanie ``started`` worklogu.

Jira używa ISO z milisekundami i offsetem BEZ dwukropka (``2026-07-20T09:15:00.000+0200``);
``datetime.fromisoformat`` na Pythonie 3.10 tego wariantu nie łyka, stąd jawne formaty.
Rdzeń nie importuje z adapterów, więc parsowanie mieszka tu — współdzielone przez zapis
(``application/jira.py``: echo zdarzeń) i ewidencję czasu (``application/worklog.py``: pole
``started`` oraz strażnik duplikatów). Czyste funkcje, bez zegara.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

# Kolejność ma znaczenie: wariant z milisekundami jest u Jiry domyślny, więc próbujemy go pierwszy.
_JIRA_TS_FORMATS = ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z")
# Godzina, na którą stempurjemy wpis czasu, gdy znamy tylko DZIEŃ. Południe (nie północ) chroni
# przed przeskokiem doby przy konwersji stref u odbiorcy — wpis zostaje w zamierzonym dniu.
_WORKLOG_HOUR = 12


def parse_jira_timestamp(value: Any) -> datetime | None:
    """Znacznik Jiry → aware ``datetime``; wartość pusta lub niezrozumiała → ``None``.

    Zwracamy ``None`` zamiast rzucać, bo wołający (echo zdarzenia, strażnik duplikatów) mają
    sensowne zachowanie awaryjne — nieudany zapis echa nie może wywrócić udanej mutacji.
    """
    if not value:
        return None
    text = str(value).strip()
    for fmt in _JIRA_TS_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def format_worklog_started(day: date, tz_offset_minutes: int) -> str:
    """Złóż pole ``started`` worklogu dla całego DNIA w strefie o podanym offsecie.

    Jira wymaga milisekund i offsetu bez dwukropka — ``%z`` daje dokładnie taki kształt.
    """
    tz = timezone(timedelta(minutes=tz_offset_minutes))
    moment = datetime.combine(day, time(hour=_WORKLOG_HOUR), tzinfo=tz)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000%z")
