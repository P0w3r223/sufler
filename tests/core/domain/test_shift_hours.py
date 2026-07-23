"""Godziny z bloków zmian → minuty per (osoba, dzień): okno, podział doby, DST (ADR 0036)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from workmate.core.domain.shift_hours import ShiftBlock, minutes_by_person_day

WARSAW = ZoneInfo("Europe/Warsaw")
SINCE = date(2026, 7, 13)  # poniedziałek
UNTIL = date(2026, 7, 20)  # następny poniedziałek (wykluczony — okno półotwarte)


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _block(user_id: str, start: datetime, end: datetime) -> ShiftBlock:
    return ShiftBlock(user_id=user_id, start=start, end=end)


def test_single_day_shift_sums_minutes() -> None:
    # 08:00–16:30 lokalnie (CEST=UTC+2) = 06:00–14:30 UTC → 510 min, dzień 15.
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 7, 15, 6), _utc(2026, 7, 15, 14, 30))],
        since=SINCE,
        until=UNTIL,
        tz=WARSAW,
    )
    assert len(rows) == 1
    assert (rows[0].user_id, rows[0].day, rows[0].minutes) == ("A", date(2026, 7, 15), 510)


def test_shift_crossing_midnight_splits_across_two_days() -> None:
    # 22:00 15-go → 06:00 16-go lokalnie = 20:00 UTC 15 → 04:00 UTC 16: 2h dnia 15 + 6h dnia 16.
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 7, 15, 20), _utc(2026, 7, 16, 4))],
        since=SINCE,
        until=UNTIL,
        tz=WARSAW,
    )
    by_day = {r.day: r.minutes for r in rows}
    assert by_day[date(2026, 7, 15)] == 120
    assert by_day[date(2026, 7, 16)] == 360


def test_portion_outside_window_is_clipped() -> None:
    # Zmiana niedziela 19 22:00 → poniedziałek 20 06:00 lokalnie (20:00 UTC 19 → 04:00 UTC 20).
    # Tylko część do północy 20-go, ale północ 20-go = koniec okna → poniedziałkowa część OBCIĘTA.
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 7, 19, 20), _utc(2026, 7, 20, 4))],
        since=SINCE,
        until=UNTIL,
        tz=WARSAW,
    )
    by_day = {r.day: r.minutes for r in rows}
    assert by_day.get(date(2026, 7, 19)) == 120  # 22:00–00:00 lokalnie
    assert date(2026, 7, 20) not in by_day  # poza oknem


def test_portion_before_window_start_is_clipped() -> None:
    # Druga krawędź półotwartego okna: zmiana niedziela 12 22:00 → poniedziałek 13 06:00 lokalnie
    # (20:00 UTC 12 → 04:00 UTC 13). Poniedziałek 13 to POCZĄTEK okna (włącznie), więc część przed
    # północą 13-go (niedzielna) jest OBCIĘTA — liczy się tylko 00:00–06:00 poniedziałku = 360 min.
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 7, 12, 20), _utc(2026, 7, 13, 4))],
        since=SINCE,
        until=UNTIL,
        tz=WARSAW,
    )
    by_day = {r.day: r.minutes for r in rows}
    assert by_day.get(date(2026, 7, 13)) == 360  # 00:00–06:00 poniedziałku
    assert date(2026, 7, 12) not in by_day  # niedziela poza oknem — obcięta na krawędzi since


def test_shift_entirely_before_window_is_ignored() -> None:
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 7, 5, 6), _utc(2026, 7, 5, 14))],
        since=SINCE,
        until=UNTIL,
        tz=WARSAW,
    )
    assert rows == []


def test_empty_blocks_return_no_rows() -> None:
    assert minutes_by_person_day([], since=SINCE, until=UNTIL, tz=WARSAW) == []


def test_sub_minute_segment_is_dropped() -> None:
    # Blok krótszy niż minuta (40 s) daje 0 minut po ucięciu — strażnik ``minutes > 0`` odsiewa
    # go, żeby nie tworzyć pustego wiersza (osoba, dzień, 0 min) w zestawieniu.
    start = _utc(2026, 7, 15, 6)
    rows = minutes_by_person_day(
        [_block("A", start, start + timedelta(seconds=40))],
        since=SINCE,
        until=UNTIL,
        tz=WARSAW,
    )
    assert rows == []


def test_multiple_people_same_day_sums_and_sorts() -> None:
    blocks = [
        _block("B", _utc(2026, 7, 16, 6), _utc(2026, 7, 16, 10)),
        _block("A", _utc(2026, 7, 15, 6), _utc(2026, 7, 15, 10)),
        _block("A", _utc(2026, 7, 15, 11), _utc(2026, 7, 15, 13)),  # ten sam dzień → sumuje
    ]
    rows = minutes_by_person_day(blocks, since=SINCE, until=UNTIL, tz=WARSAW)
    assert [(r.user_id, r.day, r.minutes) for r in rows] == [
        ("A", date(2026, 7, 15), 360),  # 4h + 2h
        ("B", date(2026, 7, 16), 240),
    ]


def test_dst_autumn_day_counts_real_elapsed_time() -> None:
    # Cofnięcie zegara 2026-10-25 (03:00→02:00 w Europe/Warsaw): doba ma 25 h. Blok obejmujący
    # całą lokalną dobę = 25 h realnie → 1500 min (odporność na DST: różnica świadomych datetime).
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 10, 24, 22), _utc(2026, 10, 25, 23))],
        since=date(2026, 10, 19),
        until=date(2026, 10, 26),
        tz=WARSAW,
    )
    by_day = {r.day: r.minutes for r in rows}
    assert by_day[date(2026, 10, 25)] == 1500


def test_dst_spring_day_counts_real_elapsed_time() -> None:
    # Przesunięcie zegara 2026-03-29 (02:00→03:00 w Europe/Warsaw): doba ma 23 h. Blok obejmujący
    # całą lokalną dobę = 23 h realnie → 1380 min. To DRUGA strona bugu DST: wall-clock zawyżyłby
    # do 1440 (24 h). Autumn locka zaniżenie, spring zawyżenie — obie ścieżki muszą trzymać się UTC.
    rows = minutes_by_person_day(
        [_block("A", _utc(2026, 3, 28, 23), _utc(2026, 3, 29, 22))],
        since=date(2026, 3, 23),
        until=date(2026, 3, 30),
        tz=WARSAW,
    )
    by_day = {r.day: r.minutes for r in rows}
    assert by_day[date(2026, 3, 29)] == 1380
