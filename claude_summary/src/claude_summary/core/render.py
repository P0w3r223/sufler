"""Deterministyczny render raportu do dicta (JSON) i do Markdown.

JSON to stabilny kontrakt dla większego agenta (Jira); Markdown to czytelny digest dla człowieka.
Oba są czyste i tzależne: godziny wyświetlamy w strefie ``tz`` (znaczniki w modelach są w strefie
źródła), więc render jest jedynym miejscem prezentacji czasu obok grupowania po dniu.
"""

from __future__ import annotations

import json
from typing import Any
from zoneinfo import ZoneInfo

from claude_summary.core.models import DaySummary, Prompt, SummaryReport

_WEEKDAY_PL = (
    "poniedziałek",
    "wtorek",
    "środa",
    "czwartek",
    "piątek",
    "sobota",
    "niedziela",
)


def _plural_pl(count: int, one: str, few: str, many: str) -> str:
    """Polska odmiana rzeczownika po liczebniku (1 prompt, 2 prompty, 5 promptów)."""
    if count == 1:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def _oneline(text: str, width: int = 200) -> str:
    """Zwiń wielolinijkowy prompt do jednej linii i przytnij do podglądu."""
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= width else collapsed[:width] + " […]"


def _day_to_dict(day: DaySummary, *, tz: ZoneInfo) -> dict[str, Any]:
    return {
        "date": day.day.isoformat(),
        "weekday": _WEEKDAY_PL[day.day.weekday()],
        "prompt_count": day.prompt_count,
        "commit_count": day.commit_count,
        "llm_prose": day.llm_prose,
        "prompts": [
            {
                "time": prompt.timestamp.astimezone(tz).strftime("%H:%M"),
                "timestamp": prompt.timestamp.astimezone(tz).isoformat(),
                "project": prompt.project,
                "session_id": prompt.session_id,
                "text": prompt.text,
                "redactions": list(prompt.redactions),
            }
            for prompt in day.prompts
        ],
        "commits": [
            {
                "time": commit.timestamp.astimezone(tz).strftime("%H:%M"),
                "timestamp": commit.timestamp.astimezone(tz).isoformat(),
                "sha": commit.sha,
                "short_sha": commit.sha[:7],
                "author": commit.author,
                "message": commit.message,
            }
            for commit in day.commits
        ],
    }


def to_dict(report: SummaryReport, *, tz: ZoneInfo) -> dict[str, Any]:
    """Zbuduj JSON-owalny słownik raportu (stabilny kontrakt dla agenta Jira)."""
    return {
        "person": report.person,
        "since": report.since.isoformat(),
        "until": report.until.isoformat(),
        "repo": report.repo,
        "timezone": str(tz),
        "days": [_day_to_dict(day, tz=tz) for day in report.days],
    }


def to_json(report: SummaryReport, *, tz: ZoneInfo) -> str:
    return json.dumps(to_dict(report, tz=tz), ensure_ascii=False, indent=2)


def _prompt_line(prompt: Prompt, *, tz: ZoneInfo) -> str:
    time = prompt.timestamp.astimezone(tz).strftime("%H:%M")
    suffix = f"  _(zredagowano: {', '.join(prompt.redactions)})_" if prompt.redactions else ""
    return f"- {time} — {_oneline(prompt.text)}{suffix}"


def _day_heading(day: DaySummary) -> str:
    weekday = _WEEKDAY_PL[day.day.weekday()]
    prompts = f"{day.prompt_count} {_plural_pl(day.prompt_count, 'prompt', 'prompty', 'promptów')}"
    commits = f"{day.commit_count} {_plural_pl(day.commit_count, 'commit', 'commity', 'commitów')}"
    return f"## {day.day.isoformat()} ({weekday}) · {prompts} · {commits}"


def to_markdown(report: SummaryReport, *, tz: ZoneInfo) -> str:
    """Zbuduj czytelny dzienny digest w Markdown."""
    repo = report.repo or "—"
    lines: list[str] = [
        f"# Podsumowanie aktywności — {report.person}",
        "",
        f"Zakres: {report.since.isoformat()} – {report.until.isoformat()} · "
        f"Repo: {repo} · Strefa: {tz}",
        "",
    ]
    for day in report.days:
        lines.append(_day_heading(day))
        if day.is_empty:
            lines += ["", "_Brak zarejestrowanej aktywności._", ""]
            continue
        if day.llm_prose:
            lines += ["", day.llm_prose.strip()]
        if day.prompts:
            lines += ["", "**Prompty:**"]
            lines += [_prompt_line(prompt, tz=tz) for prompt in day.prompts]
        if day.commits:
            lines += ["", "**Commity:**"]
            lines += [
                f"- {commit.timestamp.astimezone(tz).strftime('%H:%M')} "
                f"`{commit.sha[:7]}` {_oneline(commit.message)}"
                for commit in day.commits
            ]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
