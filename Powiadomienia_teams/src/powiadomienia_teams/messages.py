"""Treść powiadomienia 1:1 (czysta logika) + minimalny render do HTML dla Graph."""
from __future__ import annotations

from collections.abc import Iterable
from html import escape
from typing import Any
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Member, WeekSchedule

_DNI = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
_DNI_SKROT = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]

DECLINED_TEXT = (
    "OK, nie wprowadzam żadnych zmian w Twoim grafiku na ten tydzień i kończę przypominanie. "
    "Odezwę się ponownie przy kolejnym grafiku."
)
APPLIED_TEXT = "Gotowe ✅ Zapisałem Twoje zmiany na przyszły tydzień. Dzięki!"
WRITE_FAILED_TEXT = (
    "Nie udało mi się zapisać wszystkiego 😕 Zajrzyj proszę do zakładki »Zmiany« w Teams i "
    "sprawdź, czego brakuje — część mogła się już zapisać. Uzupełnij tylko brakujące dni."
)
UNCLEAR_TEXT = (
    "Nie do końca zrozumiałem 🙂 Napisz proszę np. „pon–pt 8–16” "
    "albo „w piątek 10–20, reszta bez zmian”."
)
EXPIRED_TEXT = (
    "Nie dostałem odpowiedzi, więc na razie nic nie zapisuję. Kiedy będziesz gotowy/gotowa, "
    "napisz, kiedy pracujesz — wrócę do tego przy kolejnym przypomnieniu."
)


def build_nudge_text(member: Member, proposal: WeekSchedule, week_label: str, tz: ZoneInfo) -> str:
    """Zbuduj tekst przypomnienia (czysto). Godziny propozycji renderowane w strefie `tz`."""
    parts = member.display_name.split()
    first_name = parts[0] if parts else member.display_name
    lines = [
        f"Cześć {first_name}! 👋",
        f"Nie masz jeszcze uzupełnionych zmian na przyszły tydzień ({week_label}).",
    ]
    if proposal.is_empty:
        lines.append(
            "Nie znalazłem Twojego grafiku z zeszłego tygodnia — napisz proszę, kiedy pracujesz "
            "(np. „pon–pt 8–16”)."
        )
    else:
        lines.append("W zeszłym tygodniu Twój grafik wyglądał tak:")
        for sh in proposal.shifts:
            start = sh.start.astimezone(tz)
            end = sh.end.astimezone(tz)
            lines.append(f"• {_DNI[start.weekday()]} {start:%H:%M}–{end:%H:%M}")
        lines.append(
            "Odpisz „ok”, żeby powtórzyć to samo, albo napisz, co zmienić "
            "(np. „w piątek 10–20, reszta bez zmian” lub „w piątek mnie nie będzie”)."
        )
    return "\n".join(lines)


def describe_schedule(schedule: WeekSchedule, tz: ZoneInfo) -> str:
    """Opis grafiku do potwierdzenia z trybem pracy jako emotka: 🟢 stacjonarnie, 🔵 zdalnie.

    Kolor emotki idzie za kolorem Shifts (`theme`): green/None → 🟢 (stacjonarnie), blue → 🔵
    (zdalnie), np. „pon 08:00–16:00 🟢”.
    """
    parts = []
    for sh in schedule.shifts:
        start = sh.start.astimezone(tz)
        end = sh.end.astimezone(tz)
        mode = "🔵" if sh.theme == "blue" else "🟢"  # blue = zdalnie, green/None = stacjonarnie
        parts.append(f"{_DNI_SKROT[start.weekday()]} {start:%H:%M}–{end:%H:%M} {mode}")
    return ", ".join(parts)


def describe_time_off(entries: Iterable[dict[str, Any]]) -> str:
    """Opis dni wolnych do potwierdzenia, np. „pt: Urlop, wt: Zwolnienie lekarskie”.

    Używa FAKTYCZNEJ nazwy powodu z rozstrzygniętego wpisu (``reason_name``), więc pracownik
    widzi dokładnie to, co zostanie zapisane.
    """
    parts = []
    for item in entries:
        weekday = int(item["weekday"])
        name = str(item.get("reason_name") or "Nieobecność")
        parts.append(f"{_DNI_SKROT[weekday]}: {name}")
    return ", ".join(parts)


def build_confirm_text(
    schedule: WeekSchedule, time_off: Iterable[dict[str, Any]], tz: ZoneInfo
) -> str:
    """Prośba o potwierdzenie przed zapisem (spirit ADR 0006 — zapis tylko po »tak«)."""
    time_off = list(time_off)
    segments = []
    if not schedule.is_empty:
        segments.append(f"grafik: {describe_schedule(schedule, tz)}")
    if time_off:
        segments.append(f"czas wolny: {describe_time_off(time_off)}")
    return (
        f"Zapiszę {'; '.join(segments)}. "
        "Potwierdź „tak”, żeby zapisać, albo napisz poprawkę."
    )


def to_html(text: str) -> str:
    """Zamień tekst z podziałami linii na bezpieczny HTML dla wiadomości Teams."""
    return "<br>".join(escape(line) for line in text.split("\n"))
