"""Testy czystej selekcji zdarzeń Jiry (mapowanie, self-skip, watermark, JQL) — bez sieci."""

from __future__ import annotations

from workmate.adapters.inbound.jira import selection

_BASE = "https://jira.example.com"


def _issue(
    key: str = "WM-1",
    *,
    creator: str = "alice",
    summary: str = "Awaria logowania",
    created: str = "2026-07-15T10:00:00.000+0200",
    updated: str = "2026-07-15T10:00:00.000+0200",
    project_key: str | None = None,
    histories: list | None = None,
    comments: list | None = None,
) -> dict:
    fields: dict = {
        "summary": summary,
        "description": "opis zgłoszenia",
        "created": created,
        "updated": updated,
        "creator": {"name": creator},
        "project": {"key": project_key or key.split("-", 1)[0]},
    }
    if comments is not None:
        fields["comment"] = {"comments": comments}
    raw: dict = {"key": key, "fields": fields}
    if histories is not None:
        raw["changelog"] = {"histories": histories}
    return raw


def _history(
    history_id: str,
    *,
    author: str = "bob",
    frm: str = "Open",
    to: str = "In Progress",
    field: str = "status",
    created: str = "2026-07-15T11:00:00.000+0200",
) -> dict:
    return {
        "id": history_id,
        "author": {"name": author},
        "created": created,
        "items": [{"field": field, "fromString": frm, "toString": to}],
    }


def _comment(
    comment_id: str,
    *,
    author: str = "carol",
    body: str = "działam nad tym",
    created: str = "2026-07-15T12:00:00.000+0200",
) -> dict:
    return {"id": comment_id, "author": {"name": author}, "body": body, "created": created}


# --- map_issue_created ------------------------------------------------------


def test_map_issue_created_basic_fields():
    ev = selection.map_issue_created(_issue("WM-7", creator="carol"), base_url=_BASE, project="WM")
    assert ev is not None
    assert ev.source == "jira"
    assert ev.kind == "jira_issue_created"
    assert ev.external_id == "WM-7"
    assert ev.actor == "carol"
    assert ev.project == "WM"
    assert ev.url == f"{_BASE}/browse/WM-7"
    assert "WM-7" in ev.title and "Awaria" in ev.title


def test_map_issue_created_none_without_key_or_date():
    no_key = {"fields": {"created": "2026-07-15T10:00:00.000+0200"}}
    assert selection.map_issue_created(no_key) is None
    assert selection.map_issue_created({"key": "WM-1", "fields": {}}) is None  # brak created


def test_map_issue_created_falls_back_to_reporter():
    raw = _issue("WM-3")
    del raw["fields"]["creator"]
    raw["fields"]["reporter"] = {"name": "reporter-user"}
    ev = selection.map_issue_created(raw)
    assert ev is not None
    assert ev.actor == "reporter-user"


def test_map_issue_created_clips_long_description():
    raw = _issue("WM-9")
    raw["fields"]["description"] = "x" * 900
    ev = selection.map_issue_created(raw)
    assert ev is not None
    assert ev.summary.endswith("[…]")
    assert len(ev.summary) < 900


def test_map_issue_created_non_string_description_is_empty():
    raw = _issue("WM-9")
    raw["fields"]["description"] = {"type": "doc"}  # Cloud ADF — nie tekst
    ev = selection.map_issue_created(raw)
    assert ev is not None
    assert ev.summary == ""


# --- map_transitions --------------------------------------------------------


def test_map_transitions_emits_status_change_with_history_id():
    raw = _issue("WM-5", histories=[_history("10001", frm="Open", to="In Progress")])
    events = selection.map_transitions(raw, base_url=_BASE, project="WM")
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "jira_transition"
    assert ev.external_id == "10001"  # dedup po id wpisu changelogu (stabilny)
    assert ev.actor == "bob"
    assert ev.title == "WM-5: In Progress"
    assert ev.summary == "Open → In Progress"
    assert ev.url == f"{_BASE}/browse/WM-5"


def test_map_transitions_skips_non_status_changes():
    raw = _issue("WM-5", histories=[_history("1", field="assignee", frm="a", to="b")])
    assert selection.map_transitions(raw) == []


def test_map_transitions_multiple_histories_each_emit():
    raw = _issue(
        "WM-5",
        histories=[
            _history("1", to="In Progress"),
            _history("2", frm="In Progress", to="Done"),
        ],
    )
    events = selection.map_transitions(raw)
    assert [e.external_id for e in events] == ["1", "2"]


def test_map_transitions_no_changelog_is_empty():
    assert selection.map_transitions(_issue("WM-5")) == []


def test_map_transitions_summary_without_from():
    raw = _issue("WM-5", histories=[_history("1", frm="", to="Done")])
    events = selection.map_transitions(raw)
    assert events[0].summary == "Done"  # brak stanu wyjściowego → sam docelowy


# --- map_comments -----------------------------------------------------------


def test_map_comments_emits_with_comment_id_and_focused_url():
    raw = _issue("WM-8", comments=[_comment("30001", author="dave", body="komentarz")])
    events = selection.map_comments(raw, base_url=_BASE, project="WM")
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "jira_comment"
    assert ev.external_id == "30001"
    assert ev.actor == "dave"
    assert "WM-8" in ev.title
    assert ev.url == f"{_BASE}/browse/WM-8?focusedCommentId=30001"


def test_map_comments_no_comment_field_is_empty():
    assert selection.map_comments(_issue("WM-8")) == []


def test_map_comments_none_without_id_or_date():
    raw = _issue("WM-8", comments=[{"body": "brak id", "created": "2026-07-15T12:00:00.000+0200"}])
    assert selection.map_comments(raw) == []


# --- select_events ----------------------------------------------------------


def test_select_events_expands_issue_into_three_kinds():
    raw = _issue(
        "WM-1",
        histories=[_history("h1", to="In Progress")],
        comments=[_comment("c1")],
    )
    events = selection.select_events([raw], self_account="")
    kinds = {e.kind for e in events}
    assert kinds == {"jira_issue_created", "jira_transition", "jira_comment"}


def test_select_events_skips_self_authored_per_event():
    # Issue utworzone przez konto bota (svc), ale komentarz od innej osoby — komentarz zostaje.
    raw = _issue("WM-1", creator="svc", comments=[_comment("c1", author="alice")])
    events = selection.select_events([raw], self_account="svc")
    kinds = [e.kind for e in events]
    assert "jira_issue_created" not in kinds  # utworzenie autorstwa bota pominięte
    assert "jira_comment" in kinds  # komentarz innej osoby zostaje


def test_select_events_empty_self_account_keeps_all():
    raw = _issue("WM-1", creator="svc")
    events = selection.select_events([raw], self_account="")
    assert any(e.kind == "jira_issue_created" for e in events)


def test_select_events_stamps_project_per_issue_from_map():
    a = _issue("WM-1", project_key="WM")
    b = _issue("OPS-2", project_key="OPS")
    project_map = {"WM": "workmate", "OPS": "operations"}
    events = selection.select_events([a, b], self_account="", project_map=project_map)
    by_id = {e.external_id: e.project for e in events}
    assert by_id["WM-1"] == "workmate"
    assert by_id["OPS-2"] == "operations"


def test_select_events_unknown_project_key_is_empty_project():
    raw = _issue("ZZ-1", project_key="ZZ")
    events = selection.select_events([raw], self_account="", project_map={"WM": "workmate"})
    assert events[0].project == ""


def test_select_events_sorts_by_occurred_at():
    raw = _issue(
        "WM-1",
        created="2026-07-15T10:00:00.000+0200",
        histories=[_history("h1", created="2026-07-15T13:00:00.000+0200")],
        comments=[_comment("c1", created="2026-07-15T11:00:00.000+0200")],
    )
    events = selection.select_events([raw], self_account="")
    kinds = [e.kind for e in events]
    assert kinds == ["jira_issue_created", "jira_comment", "jira_transition"]


# --- build_jql / next_since / to_jql_datetime -------------------------------


def test_build_jql_without_since():
    assert selection.build_jql(("WM", "OPS")) == "project in (WM, OPS) ORDER BY updated ASC"


def test_build_jql_with_since_adds_inclusive_clause():
    jql = selection.build_jql(("WM",), since="2026-07-15 10:00")
    assert jql == 'project in (WM) AND updated >= "2026-07-15 10:00" ORDER BY updated ASC'


def test_next_since_advances_to_newest_updated_as_iso_instant():
    raws = [
        _issue("WM-1", updated="2026-07-15T10:00:00.000+0200"),
        _issue("WM-2", updated="2026-07-15T12:30:00.000+0200"),
        _issue("WM-3", updated="2026-07-15T11:00:00.000+0200"),
    ]
    # Pełny aware ISO (nie minutowy JQL) — to zarazem granica filtra świeżości.
    assert selection.next_since(raws, "") == "2026-07-15T12:30:00+02:00"


def test_next_since_keeps_current_when_empty_batch():
    assert selection.next_since([], "2026-07-15T09:00:00+02:00") == "2026-07-15T09:00:00+02:00"


def test_next_since_ignores_unparsable_updated():
    raws = [_issue("WM-1", updated="not-a-date")]
    assert selection.next_since(raws, "2026-07-15T09:00:00+02:00") == "2026-07-15T09:00:00+02:00"


# --- filtr świeżości (occurred_at >= granica) -------------------------------


def test_select_events_freshness_suppresses_old_created_keeps_new_transition():
    # Stary tiket (utworzony przed granicą) dotknięty po starcie: JQL go wciąga z historią, ale
    # utworzenie i stary komentarz są PRZED granicą → pominięte; świeża tranzycja → przepuszczona.
    raw = _issue(
        "WM-1",
        created="2026-07-15T08:00:00.000+0200",
        histories=[_history("h1", created="2026-07-15T12:00:00.000+0200")],
        comments=[_comment("c-old", created="2026-07-15T08:30:00.000+0200")],
    )
    events = selection.select_events([raw], self_account="", since="2026-07-15T10:00:00+02:00")
    assert [(e.kind, e.external_id) for e in events] == [("jira_transition", "h1")]


def test_select_events_freshness_inclusive_on_boundary():
    # Zdarzenie DOKŁADNIE na granicy przechodzi (inkluzywnie) — dedup domknie ewentualny dubel.
    raw = _issue("WM-1", created="2026-07-15T10:00:00.000+0200")
    events = selection.select_events([raw], self_account="", since="2026-07-15T10:00:00+02:00")
    assert any(e.kind == "jira_issue_created" for e in events)


def test_select_events_empty_since_disables_freshness_filter():
    raw = _issue("WM-1", created="2020-01-01T10:00:00.000+0200")
    events = selection.select_events([raw], self_account="", since="")
    assert any(e.kind == "jira_issue_created" for e in events)  # brak granicy → wszystko przechodzi


def test_select_events_naive_since_disables_freshness_filter():
    # Naiwny watermark (legacy/ręczny, bez strefy) NIE może wywrócić rundy TypeError — filtr off.
    raw = _issue("WM-1", created="2020-01-01T10:00:00.000+0200")
    events = selection.select_events([raw], self_account="", since="2026-07-15 10:00")
    assert any(e.kind == "jira_issue_created" for e in events)


def test_to_jql_datetime_uses_own_wall_clock():
    ev = selection.map_issue_created(_issue("WM-1", created="2026-07-15T10:23:45.000+0200"))
    assert ev is not None
    # Zegar ścienny znacznika (10:23 w +0200) formatowany wprost — bez konwersji strefy.
    assert selection.to_jql_datetime(ev.occurred_at) == "2026-07-15 10:23"
