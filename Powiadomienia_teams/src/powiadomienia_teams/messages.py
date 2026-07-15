"""Treść powiadomienia 1:1 (czysta logika) + minimalny render do HTML dla Graph."""
from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Member, WeekSchedule

_DNI = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"]
_DNI_SKROT = ["pon", "wt", "śr", "czw", "pt", "sob", "nd"]

DECLINED_TEXT = "OK, nie zapisuję. Jak zmienisz zdanie, po prostu napisz, kiedy pracujesz."
APPLIED_TEXT = "Gotowe ✅ Zapisałem Twoje zmiany na przyszły tydzień. Dzięki!"
WRITE_FAILED_TEXT = (
    "Nie udało mi się automatycznie zapisać zmian 😕 Uzupełnij je proszę ręcznie "
    "w zakładce »Zmiany« w Teams."
)
UNCLEAR_TEXT = (
    "Nie do końca zrozumiałem 🙂 Napisz proszę np. „pon–pt 8–16” "
    "albo „w piątek 10–20, reszta bez zmian”."
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
    """Opis grafiku do potwierdzenia z trybem pracy, np. „pon 08:00–16:00 (stacjonarnie)”."""
    parts = []
    for sh in schedule.shifts:
        start = sh.start.astimezone(tz)
        end = sh.end.astimezone(tz)
        mode = "zdalnie" if sh.theme == "blue" else "stacjonarnie"  # green/None = stacjonarnie
        parts.append(f"{_DNI_SKROT[start.weekday()]} {start:%H:%M}–{end:%H:%M} ({mode})")
    return ", ".join(parts)


def build_confirm_text(schedule: WeekSchedule, tz: ZoneInfo) -> str:
    """Prośba o potwierdzenie przed zapisem (spirit ADR 0006 — zapis tylko po »tak«)."""
    return (
        f"Zapiszę Twój grafik: {describe_schedule(schedule, tz)}. "
        "Potwierdź „tak”, żeby zapisać, albo napisz poprawkę."
    )


def to_html(text: str) -> str:
    """Zamień tekst z podziałami linii na bezpieczny HTML dla wiadomości Teams."""
    return "<br>".join(escape(line) for line in text.split("\n"))
