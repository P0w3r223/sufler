"""Testy scoped narzędzia odpowiedzi w wątku (build_thread_reply_catalog, ADR 0024, Faza 3b).

Sedno kierunku WĄTEK→GitHub: narzędzie ``reply_on_thread`` niesie PRE-ZWIĄZANY numer celu (z
zaufanego mapowania, nie od modelu). Agent podaje TYLKO ``body`` — numer jest zaszyty w closurze,
nie w sygnaturze. Opis niesie numer celu i regułę miękkiego potwierdzenia („WPROST"). Koperta błędów
jak w innych narzędziach zapisu (``WriteError`` → ``{"error": ...}``). Testujemy na realnym
``GithubWriteService`` nad atrapą portu ``GithubWritePort`` (jak w test_github_write_service.py).
"""

from __future__ import annotations

import inspect

from workmate.core.application.github import GithubWriteService
from workmate.core.application.tools import build_thread_reply_catalog
from workmate.core.errors import WriteError


class _RecordingWriter:
    """Atrapa ``GithubWritePort`` — notuje ``issue_number`` przekazany do create_comment."""

    def __init__(self) -> None:
        self.comments: list[tuple[str, str, int, str]] = []

    def create_issue(self, owner, repo, title, body, labels):
        return {"number": 1, "html_url": "http://gh/1", "created_at": "2026-07-15T10:00:00Z"}

    def create_comment(self, owner, repo, issue_number, body):
        self.comments.append((owner, repo, issue_number, body))
        return {"id": 5, "html_url": "http://gh/c/5", "created_at": "2026-07-15T10:00:00Z"}


class _FailingWriter:
    def create_issue(self, *a, **k):
        raise WriteError("nie udało się utworzyć issue (HTTP 422).")

    def create_comment(self, *a, **k):
        raise WriteError("nie udało się dodać komentarza (HTTP 403).")


def _service(writer) -> GithubWriteService:
    return GithubWriteService(writer, owner="o", repo="r")


def test_catalog_exposes_single_reply_on_thread_tool():
    catalog = build_thread_reply_catalog(_service(_RecordingWriter()), "pr", "12")
    assert [spec.name for spec in catalog] == ["reply_on_thread"]


def test_reply_comments_on_prebound_number_not_supplied_by_model():
    writer = _RecordingWriter()
    catalog = build_thread_reply_catalog(_service(writer), "pr", "12")

    result = catalog[0].fn(body="dzięki")

    # Numer 12 jest PRE-ZWIĄZANY z mapowania — agent go nie podał, a komentarz trafił na #12.
    assert result["created"] is True
    assert result["url"] == "http://gh/c/5"
    assert writer.comments == [("o", "r", 12, "dzięki")]


def test_reply_signature_takes_only_body_number_is_hidden():
    """Sygnatura narzędzia ma WYŁĄCZNIE ``body`` — numer celu nie jest parametrem modelu."""
    catalog = build_thread_reply_catalog(_service(_RecordingWriter()), "issue", "7")

    params = list(inspect.signature(catalog[0].fn).parameters)
    assert params == ["body"]


def test_target_number_string_is_converted_to_int_for_comment():
    """``target_number`` przychodzi jako string; narzędzie komentuje właściwy numer (int)."""
    writer = _RecordingWriter()
    catalog = build_thread_reply_catalog(_service(writer), "issue", "42")

    catalog[0].fn(body="ok")

    assert writer.comments[0][2] == 42


def test_description_carries_pr_noun_and_target_and_explicit_rule():
    spec = build_thread_reply_catalog(_service(_RecordingWriter()), "pr", "12")[0]

    assert "#12" in spec.description  # numer celu widoczny dla modelu
    assert "PR" in spec.description  # rodzaj celu: PR
    assert "WPROST" in spec.description  # reguła miękkiego potwierdzenia (tylko na jawną prośbę)


def test_description_uses_issue_noun_for_issue_target():
    spec = build_thread_reply_catalog(_service(_RecordingWriter()), "issue", "7")[0]
    description = spec.description

    assert "issue #7" in description


def test_reply_envelopes_write_error_instead_of_raising():
    catalog = build_thread_reply_catalog(_service(_FailingWriter()), "issue", "9")

    result = catalog[0].fn(body="cześć")

    # Jak inne narzędzia zapisu: WriteError NIE wywala fn — wraca jako {"error": ...}.
    assert "error" in result
    assert "403" in result["error"]
