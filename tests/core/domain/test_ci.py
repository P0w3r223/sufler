"""Testy czystej domeny CI (ADR 0024, Faza 2) — wyłuskanie numeru PR i deterministyczny render."""
from __future__ import annotations

from workmate.core.domain.ci import pr_number_from_url, render_ci_failure_comment


def test_pr_number_from_pull_url():
    assert pr_number_from_url("https://github.com/o/r/pull/12") == 12


def test_pr_number_from_pull_url_with_fragment():
    # Kanoniczny url auto-komentarza bywa z fragmentem (#issuecomment) — nadal wyłuskujemy numer.
    assert pr_number_from_url("https://github.com/o/r/pull/7#issuecomment-1") == 7


def test_pr_number_from_run_url_is_none():
    # Porażka CI niezwiązana z PR ma url przebiegu (bez ``/pull/``) — nie ma czego komentować.
    assert pr_number_from_url("https://github.com/o/r/actions/runs/99") is None


def test_pr_number_from_empty_is_none():
    assert pr_number_from_url("") is None


def test_pr_number_from_none_is_none():
    # Sygnatura deklaruje ``str``, ale funkcja jawnie chroni się przed ``None`` (``url or ""``).
    assert pr_number_from_url(None) is None  # type: ignore[arg-type]


def test_render_composes_title_and_summary():
    body = render_ci_failure_comment("„CI” na PR #12", "Przebieg: https://gh/run/1")
    assert "„CI” na PR #12" in body
    assert "Przebieg: https://gh/run/1" in body


def test_render_marks_message_as_automatic_non_llm():
    # Transparentność (ADR 0024): stopka jawnie oznacza wiadomość jako bota bez modelu językowego.
    body = render_ci_failure_comment("tytuł", "podsumowanie")
    assert "bez modelu" in body
    assert "automatyczn" in body  # nagłówek: „powiadomienie automatyczne"


def test_render_handles_missing_title_and_summary():
    # Brak pól nie wywraca renderu — zostaje sam nagłówek + stopka (nie ma pustych linii-śmieci).
    body = render_ci_failure_comment("", "")
    assert "bez modelu" in body
    assert "\n\n\n" not in body  # brak pustych sekcji po pominiętych polach


def test_render_is_deterministic_for_same_inputs():
    # Ta sama para wejść → identyczny string (idempotencja treści — brak zegara/losowości/modelu).
    a = render_ci_failure_comment("CI na PR #5", "Przebieg: https://gh/run/9")
    b = render_ci_failure_comment("CI na PR #5", "Przebieg: https://gh/run/9")
    assert a == b


def test_render_differs_when_inputs_differ():
    assert render_ci_failure_comment("A", "x") != render_ci_failure_comment("B", "x")
