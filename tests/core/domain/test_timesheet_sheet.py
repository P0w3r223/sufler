"""Testy projekcji arkusza WorklogPRO (ADR 0035) — format, strażniki, determinizm.

Ten moduł produkuje plik, który człowiek zaimportuje do Jiry. Po imporcie wpisy są
create-only i nieusuwalne narzędziem (ADR 0034), więc błąd tutaj jest praktycznie nieodwracalny —
stąd nacisk na strażniki tożsamości i jednoznaczność formatów.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pytest

from workmate.core.domain.guards import CrossPersonLeak
from workmate.core.domain.timesheet import Person, WorkEntry, build_timesheet
from workmate.core.domain.timesheet_sheet import (
    WORKLOGPRO_HEADERS,
    format_started,
    format_time_spent,
    project_sheet,
    sheet_filename,
)

_TZ = ZoneInfo("Europe/Warsaw")
_PERSON = Person(
    source_id="EMP-042",
    aad_user_id="8a1f",
    jira_user="mikolaj@example.org",
    display_name="Mikołaj Anonimowicz",
)


def _entry(day: int, issue: str, minutes: int, comment: str = "") -> WorkEntry:
    return WorkEntry(
        source_id="EMP-042",
        day=date(2026, 7, day),
        issue_key=issue,
        minutes=minutes,
        comment=comment,
    )


def _sheet(entries: list[WorkEntry], person: Person = _PERSON, **kw):
    timesheet = build_timesheet(
        person,
        entries,
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
        week_label="2026-W29",
    )
    return project_sheet(timesheet, start_hour=8, tz=_TZ, **kw)


# --- nagłówki (HIPOTEZA do potwierdzenia w Etapie 0) ------------------------------


def test_headers_are_not_changed_by_accident() -> None:
    """DETEKTOR ZMIANY, nie bramka poprawności — i to rozróżnienie jest tu istotne.

    Test nie wie, jak wyglądają prawdziwe nagłówki WorklogPRO; przypina jedynie bieżącą
    HIPOTEZĘ, żeby nikt nie zmienił jej mimochodem. Poprawność weryfikuje CZŁOWIEK, porównując
    z szablonem z kreatora importu, a potwierdzenie zapisuje jako
    ``WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true`` — bez tego tryb bojowy nie startuje (D6).
    Po pobraniu szablonu zaktualizuj TU i w module.
    """
    assert WORKLOGPRO_HEADERS == (
        "Issue Key/ID",
        "User",
        "Time Spent",
        "Start Date & Time",
        "Comment",
    )


def test_sheet_carries_the_module_headers() -> None:
    assert _sheet([_entry(15, "WT-12", 180)]).headers == WORKLOGPRO_HEADERS


# --- format czasu ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("minutes", "expected"),
    [(45, "45m"), (60, "1h"), (90, "1h 30m"), (180, "3h"), (455, "7h 35m")],
)
def test_time_spent_uses_jira_duration_notation(minutes: int, expected: str) -> None:
    assert format_time_spent(minutes) == expected


def test_time_spent_never_uses_day_units() -> None:
    """``1d`` znaczy co innego na każdej instancji (konfigurowalna długość dnia roboczego)."""
    assert "d" not in format_time_spent(24 * 60)


def test_time_spent_rejects_non_positive() -> None:
    with pytest.raises(ValueError, match="dodatni"):
        format_time_spent(0)


@pytest.mark.parametrize("minutes", [-1, -60, -455])
def test_time_spent_rejects_negative_minutes(minutes: int) -> None:
    """Ujemny czas to osobna granica niż zero — bez strażnika ``divmod(-5, 60)`` dałoby „-1h 55m".

    ``build_timesheet`` odsiewa ujemne wpisy wcześniej, ale ta funkcja jest publiczna i musi bronić
    się sama: gdyby przyszłe źródło ominęło agregację, do CUDZEJ Jiry nie może wjechać worklog
    z ujemnym czasem zamiast twardego błędu.
    """
    with pytest.raises(ValueError, match="dodatni"):
        format_time_spent(minutes)


# --- znacznik startu -------------------------------------------------------------


def test_started_is_full_iso_with_explicit_offset() -> None:
    """Bez offsetu WorklogPRO użyłby strefy PRZEGLĄDARKI — plik byłby niedeterministyczny."""
    assert format_started(date(2026, 7, 15), start_hour=8, tz=_TZ) == "2026-07-15T08:00:00.000+0200"


def test_started_offset_follows_dst() -> None:
    zima = format_started(date(2026, 1, 15), start_hour=8, tz=_TZ)
    lato = format_started(date(2026, 7, 15), start_hour=8, tz=_TZ)
    assert zima.endswith("+0100") and lato.endswith("+0200")


def test_started_honours_configured_hour() -> None:
    assert format_started(date(2026, 7, 15), start_hour=9, tz=_TZ).startswith("2026-07-15T09:00")


def test_started_at_midnight_stamps_a_clean_zero_hour() -> None:
    """``start_hour=0`` to skraj dozwolonego (config 0..23) — musi dać czysty ``T00:00:00``.

    Godzina zero nie może się zgubić ani przewinąć na poprzedni dzień: znacznik startu wpisu trafia
    do cudzej Jiry, więc dryf o dobę fałszowałby datę pracy.
    """
    assert format_started(date(2026, 7, 15), start_hour=0, tz=_TZ) == "2026-07-15T00:00:00.000+0200"


def test_started_offset_is_correct_on_the_dst_changeover_days() -> None:
    """Sam DZIEŃ zmiany czasu — docstring obiecuje offset „poprawny po obu stronach".

    W Polsce 2026 zegar skacze do przodu 29 III, cofa się 25 X (obie zmiany nocą, o 02:00/03:00).
    O 8:00 jesteśmy już po przeskoku, więc dzień wiosenny ma nieść ``+0200``, a jesienny ``+0100``.
    Zły offset akurat w te dni = zły znacznik godziny w cudzej, nieodwracalnej Jirze.
    """
    wiosna = format_started(date(2026, 3, 29), start_hour=8, tz=_TZ)
    jesien = format_started(date(2026, 10, 25), start_hour=8, tz=_TZ)
    assert wiosna.endswith("+0200")
    assert jesien.endswith("+0100")


# --- wiersze ---------------------------------------------------------------------


def test_one_row_per_entry() -> None:
    entries = [_entry(15, "WT-12", 180), _entry(15, "WT-14", 90), _entry(16, "WT-12", 60)]
    assert len(_sheet(entries).rows) == 3


def test_row_columns_line_up_with_headers() -> None:
    (row,) = _sheet([_entry(15, "WT-12", 90, "przegląd kodu")]).rows
    assert row == (
        "WT-12",
        "mikolaj@example.org",
        "1h 30m",
        "2026-07-15T08:00:00.000+0200",
        "przegląd kodu",
    )


def test_zero_minute_entries_are_skipped() -> None:
    """Wpis urlopowy nie jest pracą — WorklogPRO i tak by go odrzucił."""
    assert _sheet([_entry(15, "WT-12", 0), _entry(16, "WT-14", 60)]).rows == (
        ("WT-14", "mikolaj@example.org", "1h", "2026-07-16T08:00:00.000+0200", ""),
    )


def test_comment_prefix_is_prepended() -> None:
    (row,) = _sheet([_entry(15, "WT-12", 60, "praca")], comment_prefix="[auto] ").rows
    assert row[4] == "[auto] praca"


def test_control_characters_from_the_source_are_stripped() -> None:
    """``WorkEntry`` deklarował sanityzację treści ze źródła i NIKT jej nie robił.

    Kontrakt zadeklarowany i niezaimplementowany jest gorszy niż jego brak — nikt go nie
    szuka. Nowa linia i tab w komórce rozbijają wiersz (a w eksporcie CSV cały plik), znaki
    sterujące nie mają w arkuszu żadnego legalnego zastosowania. Wycinamy, nie rzucamy:
    jeden dziwny wpis nie może pozbawić arkuszy całego zespołu.
    """
    rows = _sheet([_entry(15, "WT-12", 60, comment="dwie\nlinie\ti\x00zero")]).rows
    assert rows[0][4] == "dwie linie i zero"


def test_control_characters_in_the_issue_key_are_stripped() -> None:
    rows = _sheet([_entry(15, "WT-12\r\n", 60)]).rows
    assert rows[0][0] == "WT-12"


def test_formula_prefix_survives_untouched_in_the_projection() -> None:
    """Rdzeń NIE kaleczy treści — przed formułą broni kontrakt ``SheetWriter`` (zapis jako tekst).

    Apostrof czy obcięcie wjechałyby do Jiry razem z komentarzem, a rdzeń i tak nie wie, czym
    plik zostanie zapisany; gwarancja musi stać tam, gdzie typ komórki naprawdę powstaje.
    """
    rows = _sheet([_entry(15, "WT-12", 60, comment="=SUMA(A1:A2)")]).rows
    assert rows[0][4] == "=SUMA(A1:A2)"


def test_long_comment_is_truncated() -> None:
    (row,) = _sheet([_entry(15, "WT-12", 60, "x" * 900)]).rows
    assert len(row[4]) == 500


def test_rows_are_deterministic_for_the_same_week() -> None:
    entries = [_entry(16, "WT-14", 60), _entry(15, "WT-99", 60)]
    assert _sheet(entries).rows == _sheet(list(reversed(entries))).rows


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("treść\x85z\x9fkontrolnymi", "treść z kontrolnymi"),  # C1: NEL (0x85) + APC (0x9F)
        ("koniec\x7frekordu", "koniec rekordu"),  # DEL (0x7F)
    ],
)
def test_high_control_characters_are_stripped_too(raw: str, expected: str) -> None:
    """Sanityzacja obejmuje też DEL (0x7F) i pas C1 (0x80–0x9F), nie tylko klasyczne poniżej 0x20.

    Te znaki wpadają przy mojibake z Windows-1252; w komórce nie mają legalnego zastosowania,
    a w eksporcie CSV potrafią rozjechać parser importu tak samo jak wstrzyknięta nowa linia.
    Istniejące testy dotykały wyłącznie ``\\n``/``\\t``/``\\x00`` — tu domykamy pozostałe gałęzie.
    """
    (row,) = _sheet([_entry(15, "WT-12", 60, comment=raw)]).rows
    assert row[4] == expected


def test_legitimate_unicode_survives_sanitisation() -> None:
    """Sanityzacja tnie WYŁĄCZNIE znaki sterujące — polskie litery i emoji zostają nietknięte.

    ``cell_text`` to nie ``_slug`` (ten spłaszcza treść do ASCII na potrzeby NAZWY PLIKU). Komentarz
    „źdźbło ćmy" ma dojść do Jiry w całości; regresja „sanityzacja = tylko ASCII" okaleczyłaby treść
    wpisu każdego, kto pisze po polsku.
    """
    (row,) = _sheet([_entry(15, "WT-12", 60, comment="źdźbło ćmy 🌾")]).rows
    assert row[4] == "źdźbło ćmy 🌾"


def test_projection_of_only_zero_minute_entries_is_headerful_but_rowless() -> None:
    """Osoba z samym urlopem daje arkusz z nagłówkami i BEZ wierszy — nie wyjątek i nie pusty plik.

    Filtr zerowych wpisów redukuje kolumnę ``User`` do zera komórek; strażnik tożsamości nie może
    się na tym wywrócić (pusty zbiór jest spójny), a przebieg ma iść dalej dla pozostałych osób.
    """
    sheet = _sheet([_entry(15, "WT-12", 0), _entry(16, "WT-14", 0)])
    assert sheet.headers == WORKLOGPRO_HEADERS
    assert sheet.rows == ()


# --- strażniki tożsamości --------------------------------------------------------


def test_every_user_cell_carries_the_same_identity() -> None:
    rows = _sheet([_entry(15, "WT-12", 60), _entry(16, "WT-14", 60)]).rows
    assert {row[1] for row in rows} == {"mikolaj@example.org"}


def test_projection_rejects_a_timesheet_holding_foreign_entries() -> None:
    """Obejście ``build_timesheet`` nie może przejść — to ostatni strażnik przed plikiem."""
    timesheet = build_timesheet(
        _PERSON,
        [_entry(15, "WT-12", 60)],
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
    )
    obcy = WorkEntry(source_id="EMP-017", day=date(2026, 7, 15), issue_key="WT-99", minutes=60)
    podmieniony = timesheet.model_copy(update={"entries": (*timesheet.entries, obcy)})
    with pytest.raises(CrossPersonLeak, match="EMP-017"):
        project_sheet(podmieniony, start_hour=8, tz=_TZ)


# --- nazwa pliku -----------------------------------------------------------------


def test_filename_is_ascii_slug_with_week_label() -> None:
    timesheet = build_timesheet(
        _PERSON,
        [_entry(15, "WT-12", 60)],
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
        week_label="2026-W29",
    )
    assert sheet_filename(timesheet) == "worklog_mikolaj-anonimowicz_emp-042_2026-w29.xlsx"


def test_filename_strips_polish_diacritics() -> None:
    person = _PERSON.model_copy(update={"display_name": "Łukasz Żółć"})
    timesheet = build_timesheet(
        person, [], week_start=date(2026, 7, 13), week_end=date(2026, 7, 20), week_label="2026-W29"
    )
    assert sheet_filename(timesheet) == "worklog_lukasz-zolc_emp-042_2026-w29.xlsx"


def test_filename_falls_back_to_source_id() -> None:
    person = _PERSON.model_copy(update={"display_name": ""})
    timesheet = build_timesheet(
        person, [], week_start=date(2026, 7, 13), week_end=date(2026, 7, 20), week_label="2026-W29"
    )
    assert sheet_filename(timesheet).startswith("worklog_emp-042_")


def test_filename_is_unique_for_people_sharing_a_display_name() -> None:
    """Imiennicy dostawali TĘ SAMĄ ścieżkę: drugi arkusz nadpisywał pierwszy, a wiadomość
    pierwszej osoby wskazywała już cudze godziny. Nazwa człowieka nie jest różnowartościowa —
    rozstrzyga ``source_id``, po którym i tak rozdzielamy wpisy.
    """
    other = _PERSON.model_copy(update={"source_id": "EMP-999", "jira_user": "m2@example.org"})
    week = {
        "week_start": date(2026, 7, 13),
        "week_end": date(2026, 7, 20),
        "week_label": "2026-W29",
    }
    first = build_timesheet(_PERSON, [], **week)
    second = build_timesheet(other, [], **week)

    assert sheet_filename(first) != sheet_filename(second)


def test_filename_is_unique_even_when_the_slug_collapses() -> None:
    """Nazwa bez znaków ASCII daje stałe ``bez-nazwy`` — kolizja także przy RÓŻNYCH nazwiskach."""
    week = {
        "week_start": date(2026, 7, 13),
        "week_end": date(2026, 7, 20),
        "week_label": "2026-W29",
    }
    first = build_timesheet(_PERSON.model_copy(update={"display_name": "李"}), [], **week)
    second = build_timesheet(
        _PERSON.model_copy(update={"display_name": "王", "source_id": "EMP-999"}), [], **week
    )

    assert sheet_filename(first) != sheet_filename(second)


def test_filename_is_deterministic_across_repeated_runs() -> None:
    """Powtórzony przebieg MUSI dać tę samą nazwę — nadrobienie NADPISUJE plik, nie kładzie drugi.

    To rdzeń obrony przed podwójnym importem (krok 3 Etapu 0, którego świadomie nie robimy na żywo):
    gdyby nazwa dryfowała między przebiegami, człowiek dostałby dwa arkusze TEGO SAMEGO tygodnia
    i mógłby zaimportować oba, a worklogi są create-only i nieusuwalne narzędziem (ADR 0034).
    """
    week = {
        "week_start": date(2026, 7, 13),
        "week_end": date(2026, 7, 20),
        "week_label": "2026-W29",
    }
    first = build_timesheet(_PERSON, [_entry(15, "WT-12", 60)], **week)
    second = build_timesheet(_PERSON, [_entry(15, "WT-12", 60)], **week)

    assert sheet_filename(first) == sheet_filename(second)


def test_filename_falls_back_to_week_start_when_label_is_missing() -> None:
    """Bez etykiety nazwa bierze datę początku okna — nadal NIESIE tydzień (anty-duplikat).

    ``week_label`` bywa puste (źródło go nie poda); nazwa nie może wtedy zgubić tygodnia, bo to on
    pozwala człowiekowi rozpoznać „ten sam arkusz drugi raz". Gałąź ``or week_start.isoformat()``
    była dotąd nietknięta — wszystkie testy nazwy podawały etykietę.
    """
    timesheet = build_timesheet(
        _PERSON, [], week_start=date(2026, 7, 13), week_end=date(2026, 7, 20)
    )
    assert sheet_filename(timesheet) == "worklog_mikolaj-anonimowicz_emp-042_2026-07-13.xlsx"


def test_filename_slugs_a_filesystem_hostile_week_label() -> None:
    """Ukośnik/spacja w etykiecie tygodnia nie może wprowadzić separatora ścieżki do nazwy.

    Etykieta też przechodzi przez ``_slug``; ``2026 / W29`` musi spłaszczyć się do ``2026-w29``,
    inaczej ``/`` rozbiłby nazwę na podkatalog (arkusz wylądowałby nie tam, gdzie trzeba).
    """
    timesheet = build_timesheet(
        _PERSON,
        [],
        week_start=date(2026, 7, 13),
        week_end=date(2026, 7, 20),
        week_label="2026 / W29",
    )
    name = sheet_filename(timesheet)
    assert "/" not in name and " " not in name
    assert name == "worklog_mikolaj-anonimowicz_emp-042_2026-w29.xlsx"
