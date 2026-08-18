"""Deklaracja prywatności z README/ADR 0003 sprawdzona na WSZYSTKICH trzech drogach wyjścia.

Raport opuszcza narzędzie trzema drogami: JSON (kontrakt dla agenta), Markdown (digest dla
człowieka) i zapytanie do Claude API. Żadna z nich nie może nieść adresu e-mail, ścieżki z nazwą
użytkownika ani pełnego identyfikatora sesji.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from claude_summary.adapters.anthropic_summarizer import summarize_day
from claude_summary.core.models import Commit, DaySummary, Prompt, SummaryReport
from claude_summary.core.render import to_json, to_markdown

WARSAW = ZoneInfo("Europe/Warsaw")
EMAIL = "jan.kowalski@firma.pl"
SESSION = "3534249a-6d2c-480a-8f8b-a0c746180c71"
# Konto właściciela raportu i nazwa użytkownika w ścieżce to CELOWO dwie różne osoby.
# Etykieta osoby („Jan Kowalski") wychodzi na zewnątrz legalnie — wyprowadza ją ``person_label``
# z części lokalnej adresu. Gdyby ścieżka niosła to samo nazwisko, jedyną asercją, jaka by
# została, byłoby „nie ma tu ``Jan Kowalski\\projekt``" — czyli sprawdzenie SEPARATORA, nie
# redakcji: implementacja, która zamieni ``\`` na ``/`` i zostawi nazwisko, przeszłaby ją.
KATALOG_UZYTKOWNIKA = "Anna Nowak"
REPO = f"C:\\Users\\{KATALOG_UZYTKOWNIKA}\\projekt"


class _RecordingLlm:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.payloads.append(user)
        return "opis"


def _report() -> SummaryReport:
    day = DaySummary(
        day=date(2026, 7, 17),
        prompts=(
            Prompt(
                timestamp=datetime(2026, 7, 17, 7, tzinfo=timezone.utc),
                text="zrób X",
                session_id=SESSION,
                cwd="",
                project="C--Users-[UŻYTKOWNIK]-projekt",
            ),
        ),
        commits=(
            Commit(
                sha="abcdef1234567",
                timestamp=datetime(2026, 7, 17, 8, tzinfo=timezone.utc),
                author=EMAIL,
                message="feat: x",
            ),
        ),
    )
    return SummaryReport(
        person=EMAIL, since=date(2026, 7, 17), until=date(2026, 7, 17), repo=REPO, days=(day,)
    )


def test_no_output_path_carries_email_path_or_session_id() -> None:
    report = _report()
    llm = _RecordingLlm()
    prose = summarize_day(report.days[0], person=report.person, llm=llm)

    outputs = {
        "json": to_json(report, tz=WARSAW),
        "markdown": to_markdown(report, tz=WARSAW),
        "llm": llm.payloads[0],
    }
    for name, payload in outputs.items():
        assert EMAIL not in payload, f"adres e-mail wyciekł drogą: {name}"
        assert KATALOG_UZYTKOWNIKA not in payload, f"nazwa użytkownika wyciekła drogą: {name}"
        assert SESSION not in payload, f"identyfikator sesji wyciekł drogą: {name}"
    assert prose == "opis"  # warstwa LLM nadal działa na zredagowanych danych


def test_the_report_still_names_the_person_it_is_about() -> None:
    """Przeciwwaga: redakcja usuwa NOŚNIKI tożsamości, nie sam raport.

    Bez tej sondy „naprawa" polegająca na wyczyszczeniu wszystkich pól przechodziłaby jako
    spełniony kontrakt prywatności — a raport bez adresata jest bezużyteczny.
    """
    report = _report()

    markdown = to_markdown(report, tz=WARSAW)

    assert "Jan Kowalski" in markdown  # etykieta z części lokalnej adresu, nie adres
    assert "abcdef1" in markdown  # skrót commitu przeżywa redakcję
