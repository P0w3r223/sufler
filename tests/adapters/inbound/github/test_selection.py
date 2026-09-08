"""Testy czystej selekcji zdarzeń GitHub (mapowanie, self-skip, watermark) — bez sieci."""

from __future__ import annotations

import pytest

from workmate.adapters.inbound.github import selection


def _bez_echa(external_id: str, echo_kind: str) -> bool:
    """Predykat „magazyn nic o tym nie wie" — wołający bez magazynu podaje go JAWNIE.

    ``select_events`` wymaga tego argumentu bez wartości domyślnej (ADR 0071 decyzja 6): pominięty
    przez przeoczenie wyłączałby strażnika pętli w ciszy. Widoczna nazwa w wywołaniu mówi wprost,
    że TA sonda o echo nie pyta — a sonda, która pyta, podaje własny predykat.
    """
    return False


def _issue(number: int, *, login: str = "alice", **kw) -> dict:
    raw = {
        "number": number,
        "title": f"Issue {number}",
        "body": "opis",
        "html_url": f"http://gh/{number}",
        "user": {"login": login},
        "created_at": "2026-07-15T10:00:00Z",
        "updated_at": "2026-07-15T10:00:00Z",
    }
    raw.update(kw)
    return raw


def _comment(comment_id: int, *, login: str = "bob", issue: int = 5, **kw) -> dict:
    raw = {
        "id": comment_id,
        "body": "komentarz",
        "html_url": f"http://gh/c/{comment_id}",
        "user": {"login": login},
        "issue_url": f"http://api/repos/o/r/issues/{issue}",
        "created_at": "2026-07-15T11:00:00Z",
        "updated_at": "2026-07-15T11:00:00Z",
    }
    raw.update(kw)
    return raw


def _review(
    review_id: int, *, state: str = "APPROVED", login: str = "alice", pr: int = 12, **kw
) -> dict:
    raw = {
        "id": review_id,
        "state": state,
        "body": "recenzja",
        "html_url": f"https://github.com/o/r/pull/{pr}#pullrequestreview-{review_id}",
        "user": {"login": login},
        "submitted_at": "2026-07-15T12:00:00Z",
    }
    raw.update(kw)
    return raw


def _run(
    run_id: int,
    *,
    conclusion: str = "success",
    attempt: int = 1,
    name: str = "CI",
    updated: str = "2026-07-15T13:00:00Z",
    **kw,
) -> dict:
    raw = {
        "id": run_id,
        "name": name,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "html_url": f"https://github.com/o/r/actions/runs/{run_id}",
        "pull_requests": [],
        "repository": {"html_url": "https://github.com/o/r"},
        "updated_at": updated,
    }
    raw.update(kw)
    return raw


def test_map_issue_basic_fields():
    ev = selection.map_issue(_issue(7, login="carol"))
    assert ev is not None
    assert ev.source == "github"
    assert ev.kind == "issue_opened"
    assert ev.external_id == "7"
    assert ev.actor == "carol"
    assert ev.url == "http://gh/7"


def test_map_issue_skips_pull_requests():
    assert selection.map_issue(_issue(7, pull_request={"url": "http://pr"})) is None


def test_map_issue_none_without_number_or_date():
    assert selection.map_issue({"title": "brak numeru"}) is None
    assert selection.map_issue({"number": 1}) is None  # brak created_at


def test_map_comment_derives_issue_number_in_title():
    ev = selection.map_comment(_comment(101, issue=42))
    assert ev is not None
    assert ev.kind == "issue_comment"
    assert ev.external_id == "101"
    assert "#42" in ev.title


def test_map_issue_clips_long_body():
    ev = selection.map_issue(_issue(7, body="x" * 900))
    assert ev is not None
    assert ev.summary.endswith("[…]")
    assert len(ev.summary) < 900


# --- Zamknięcia zgłoszeń (ADR 0071 etap 1) --------------------------------------------------


def _zamkniete(number: int, *, closed_at: str = "2026-09-01T09:30:00Z", **kw) -> dict:
    return _issue(number, state="closed", closed_at=closed_at, **kw)


def test_map_issue_closed_emituje_fakt_zamkniecia():
    """Warstwa była dziennikiem SAMYCH OTWARĆ i strukturalnie nie mogła powiedzieć, co jest
    otwarte — incydent 2026-09-04 (ADR 0071)."""
    ev = selection.map_issue_closed(_zamkniete(7))

    assert ev is not None
    assert ev.kind == "issue_closed"
    assert ev.title == "Issue #7 zamknięte"
    assert ev.summary == "Issue 7"  # tytuł issue jako treść, nie ciało
    assert ev.occurred_at.isoformat() == "2026-09-01T09:30:00+00:00"


def test_klucz_dedupu_niesie_znacznik_DOSLOWNIE_z_payloadu():
    """Najważniejsza sonda tego etapu — i jedyna, której naruszenia nie da się cofnąć.

    ``events.db`` jest append-only. Gdyby klucz powstawał przez sparsowanie i PONOWNE
    sformatowanie ``closed_at``, każda przyszła zmiana formatowania (strefa, mikrosekundy, ``Z``
    kontra ``+00:00``) utworzyłaby NOWY klucz dla tego samego faktu i to samo zamknięcie
    zdublowałoby się na zawsze. Dlatego podajemy znacznik w kształcie, którego żaden rozsądny
    formatter by nie odtworzył: jeśli w kluczu wyląduje cokolwiek innego niż wejście, to znaczy,
    że ktoś po drodze parsuje.
    """
    dziwny = "2026-09-01T09:30:00.000000+02:00"

    ev = selection.map_issue_closed(_zamkniete(7, closed_at=dziwny))

    assert ev is not None
    assert ev.external_id == f"7#closed@{dziwny}"


def test_klucz_zamkniecia_jest_FAKTEM_a_nie_stanem():
    """``{n}#closed`` kodowałoby STAN („zostało kiedyś zamknięte"), a magazyn append-only stanu
    nie unosi: po cyklu zamknięcie → otwarcie → zamknięcie dedup połknąłby drugie zamknięcie
    i nie dałoby się go odzyskać (ADR 0071 decyzja 3). Znacznik w kluczu czyni z tego fakt
    „zamknięte o T" i pozwala dołożyć ponowne otwarcia BEZ migracji."""
    pierwsze = selection.map_issue_closed(_zamkniete(7, closed_at="2026-09-01T09:30:00Z"))
    drugie = selection.map_issue_closed(_zamkniete(7, closed_at="2026-09-03T11:00:00Z"))

    assert pierwsze is not None and drugie is not None
    assert pierwsze.external_id != drugie.external_id


def test_map_issue_closed_nie_zmysla_znacznika_gdy_go_brak(caplog: pytest.LogCaptureFixture):
    """BEZ zapasu na ``updated_at``, inaczej niż ``map_pull_state`` (ADR 0071 decyzja 4).

    Zapas kupowałby odporność na przypadek, który nie występuje (GitHub zawsze podaje
    ``closed_at`` dla zamkniętego issue), a płacił WYMYŚLONYM znacznikiem w magazynie, którego
    nie da się cofnąć — i to znacznikiem, z którego zbudowałby się klucz dedupu.
    """
    raw = _issue(7, state="closed", updated_at="2026-09-02T08:00:00Z")  # bez closed_at

    with caplog.at_level("WARNING"):
        ev = selection.map_issue_closed(raw)

    assert ev is None
    assert "closed_at" in caplog.text  # cisza bez śladu byłaby gorsza niż brak zdarzenia


def test_map_issue_closed_milczy_dla_otwartych_i_dla_PR():
    assert selection.map_issue_closed(_issue(7, state="open")) is None
    assert selection.map_issue_closed(_issue(7)) is None  # brak pola state
    assert selection.map_issue_closed(_zamkniete(7, pull_request={"url": "http://pr"})) is None


def test_zamkniecie_ma_PUSTY_actor_i_to_jest_wybor():
    """``user`` z payloadu to ten, kto issue ZAŁOŻYŁ — na zamknięciu nazwałby autora zamykającym,
    czyli podstawiłby nowy fałsz w miejsce starego (ADR 0071 decyzja 2).

    ``closed_by`` jest dostępne i świadomie go nie bierzemy, dopóki self-skip filtruje po KONCIE:
    w tym repozytorium zamykającym jest zawsze konto PAT, więc ``actor`` z ``closed_by`` kazałby
    strażnikowi zjeść wszystkie zamknięcia — incydent odtworzony wewnątrz własnej naprawy. Ta
    sonda ma paść, gdy ktoś „tylko doda zamykającego" przed etapem 2.
    """
    ev = selection.map_issue_closed(_zamkniete(7, login="autor", closed_by={"login": "zamykacz"}))

    assert ev is not None
    assert ev.actor == ""


def test_select_events_daje_OBA_fakty_dla_zamknietego_zgloszenia():
    """Otwarcie i zamknięcie to dwa różne fakty o tym samym zgłoszeniu; dedup magazynu połyka
    powtórki otwarcia, więc emitowanie obu jest tanie i poprawne."""
    events = selection.select_events([_zamkniete(7, login="alice")], [], echo_seen=_bez_echa)

    assert [(e.kind, e.external_id) for e in events] == [
        ("issue_opened", "7"),
        ("issue_closed", "7#closed@2026-09-01T09:30:00Z"),
    ]


def test_zamkniecie_przechodzi_nawet_gdy_OTWARCIE_ma_echo_naszych_drzwi():
    """Interakcja, którą ADR nakazuje trzymać pod sondą (decyzje 2 + 6), po etapie 2.

    Zgłoszenie założone NASZYMI drzwiami ma echo, więc jego otwarcie strażnik pomija — słusznie,
    bo ten fakt jest już w magazynie pod ``source='teams'``. Zamknięcie musi przejść mimo to:
    echo mówi „to my je założyliśmy", a nie „to my je zamknęliśmy", i nie ma rodzaju echa, który
    by o zamknięciu mówił. Gdyby strażnik zjadał oba, warstwa nie wiedziałaby o zamknięciu żadnego
    ze zgłoszeń założonych z Teamsów.
    """
    events = selection.select_events(
        [_zamkniete(7, login="alice")],
        [],
        echo_seen=lambda eid, kind: (eid, kind) == ("7", "github_issue_created"),
    )

    assert [e.kind for e in events] == ["issue_closed"]


def test_zamkniecia_nie_ma_gdy_drzwi_nie_obserwuja_zgloszen():
    """``watch_kinds`` bez „issues" wyłącza CAŁĄ ścieżkę zgłoszeń, także zamknięcia — inaczej
    konfiguracja „tylko PR-y" zaczęłaby po cichu zapisywać zdarzenia zgłoszeń."""
    events = selection.select_events(
        [_zamkniete(7, login="alice")], [], echo_seen=_bez_echa, watch_kinds=("pulls",)
    )

    assert events == []


def test_select_events_pomija_to_co_ma_ECHO_naszych_drzwi():
    """ODWRÓCONE 2026-09-07 (ADR 0071 decyzja 6): pytamy o ECHO, nie o konto autora.

    Do tego dnia sonda nazywała się „skips_self" i pomijała zdarzenia autorstwa konta PAT.
    Przesłanka „nasze konto ⇒ nasze narzędzie" była zmierzona jako fałszywa: sześć z ośmiu
    zgłoszeń założonych kontem bota powstało poza narzędziem (`gh` CLI, WWW) i nie miało echa,
    więc filtr po koncie wycinał je z warstwy, a nic ich nie zapisywało z drugiej strony.
    """
    echa = {("1", "github_issue_created"), ("9", "github_comment_created")}
    issues = [_issue(1, login="alice"), _issue(2, login="alice")]
    comments = [_comment(9, login="alice"), _comment(10, login="alice")]

    events = selection.select_events(
        issues, comments, echo_seen=lambda eid, kind: (eid, kind) in echa
    )

    # Autor nie ma tu znaczenia — wszystkie cztery zdarzenia są cudze. Pominięte zostają te,
    # dla których w magazynie leży ślad NASZYCH drzwi zapisu.
    assert [(e.kind, e.external_id) for e in events] == [
        ("issue_opened", "2"),
        ("issue_comment", "10"),
    ]


def test_select_events_nie_pomija_juz_niczego_po_autorze():
    """Konto autora przestało być kryterium — także dla zdarzeń konta PAT bez echa.

    To jest cała zmiana widziana od strony skutku: sześć zgłoszeń, które dotąd nie istniały
    w warstwie, zaczyna istnieć.
    """
    events = selection.select_events(
        [_issue(1, login="me")], [_comment(9, login="me")], echo_seen=_bez_echa
    )

    assert [(e.kind, e.external_id) for e in events] == [
        ("issue_opened", "1"),
        ("issue_comment", "9"),
    ]


def test_next_since_advances_to_newest_updated():
    raws = [
        {"updated_at": "2026-07-15T10:00:00Z"},
        {"updated_at": "2026-07-15T12:00:00Z"},
        {"updated_at": "2026-07-15T11:00:00Z"},
    ]
    assert selection.next_since(raws, "2026-07-15T09:00:00Z") == "2026-07-15T12:00:00Z"
    assert selection.next_since([], "2026-07-15T09:00:00Z") == "2026-07-15T09:00:00Z"


def test_next_since_uses_submitted_at_field_for_reviews():
    raws = [
        _review(1, submitted_at="2026-07-15T10:00:00Z"),
        _review(2, submitted_at="2026-07-15T14:00:00Z"),
    ]
    # Recenzje nie mają ``updated_at`` — watermark liczymy z ``submitted_at``.
    assert selection.next_since(raws, "", field="submitted_at") == "2026-07-15T14:00:00Z"


# --- map_pull (pr_opened) ---------------------------------------------------


def test_map_pull_basic_fields():
    ev = selection.map_pull(_issue(8, login="carol", pull_request={"url": "http://pr"}))
    assert ev is not None
    assert ev.kind == "pr_opened"
    assert ev.external_id == "8"
    assert ev.actor == "carol"
    assert ev.url == "http://gh/8"


def test_map_pull_none_for_plain_issue():
    assert selection.map_pull(_issue(8)) is None  # brak klucza pull_request


def test_map_pull_none_without_number_or_date():
    assert selection.map_pull({"pull_request": {}, "title": "brak numeru"}) is None
    assert selection.map_pull({"pull_request": {}, "number": 1}) is None  # brak created_at


# --- map_comment: pr_comment vs issue_comment -------------------------------


def test_map_comment_pr_when_html_url_has_pull():
    ev = selection.map_comment(_comment(101, html_url="https://github.com/o/r/pull/9#c-101"))
    assert ev is not None
    assert ev.kind == "pr_comment"
    assert "PR" in ev.title


def test_map_comment_issue_when_html_url_lacks_pull():
    ev = selection.map_comment(_comment(101, html_url="https://github.com/o/r/issues/9#c-101"))
    assert ev is not None
    assert ev.kind == "issue_comment"


# --- map_review (pr_review) -------------------------------------------------


def test_map_review_approved_extracts_pr_number():
    ev = selection.map_review(_review(55, state="APPROVED", pr=12, login="carol"))
    assert ev is not None
    assert ev.kind == "pr_review"
    assert ev.external_id == "55"
    assert ev.actor == "carol"
    assert "#12" in ev.title


def test_map_review_changes_requested_is_event():
    ev = selection.map_review(_review(55, state="CHANGES_REQUESTED"))
    assert ev is not None
    assert ev.kind == "pr_review"


@pytest.mark.parametrize("state", ["COMMENTED", "PENDING", "DISMISSED", ""])
def test_map_review_none_for_noise_states(state):
    assert selection.map_review(_review(55, state=state)) is None


def test_map_review_none_without_id_or_date():
    assert selection.map_review({"state": "APPROVED", "submitted_at": "2026-07-15T12:00Z"}) is None
    assert selection.map_review({"id": 1, "state": "APPROVED"}) is None  # brak submitted_at


# --- map_ci_run (ci_success / ci_failure) -----------------------------------


@pytest.mark.parametrize(
    "conclusion,kind",
    [
        ("success", "ci_success"),
        ("failure", "ci_failure"),
        ("timed_out", "ci_failure"),
        ("startup_failure", "ci_failure"),
    ],
)
def test_map_ci_run_maps_decisive_conclusions(conclusion, kind):
    ev = selection.map_ci_run(_run(1, conclusion=conclusion))
    assert ev is not None
    assert ev.kind == kind


@pytest.mark.parametrize("conclusion", ["cancelled", "skipped", "neutral", "action_required"])
def test_map_ci_run_none_for_noise_conclusions(conclusion):
    assert selection.map_ci_run(_run(1, conclusion=conclusion)) is None


def test_map_ci_run_none_without_conclusion_or_date():
    assert selection.map_ci_run(_run(1, conclusion="")) is None
    assert selection.map_ci_run(_run(1, updated_at="")) is None
    assert selection.map_ci_run({"id": 1, "conclusion": "success"}) is None  # brak updated_at


def test_map_ci_run_external_id_distinguishes_reruns():
    # Ponowienie (re-run) dzieli ``id`` — ``run_attempt`` musi rozróżnić dwa zdarzenia,
    # inaczej druga porażka zniknęłaby w dedupie magazynu.
    first = selection.map_ci_run(_run(100, conclusion="failure", attempt=1))
    second = selection.map_ci_run(_run(100, conclusion="failure", attempt=2))
    assert first is not None and second is not None
    assert first.external_id == "100#1"
    assert second.external_id == "100#2"
    assert first.external_id != second.external_id


def test_map_ci_run_canonizes_url_to_pr_page():
    ev = selection.map_ci_run(
        _run(
            7,
            pull_requests=[{"number": 12}],
            repository={"html_url": "https://github.com/o/r"},
        )
    )
    assert ev is not None
    assert ev.url == "https://github.com/o/r/pull/12"
    # Link do samego przebiegu ląduje w summary (nie gubimy go), a #12 jest w tytule.
    assert "https://github.com/o/r/actions/runs/7" in ev.summary
    assert "#12" in ev.title


def test_map_ci_run_without_pr_uses_run_url_and_empty_summary():
    ev = selection.map_ci_run(_run(7, pull_requests=[]))
    assert ev is not None
    assert ev.url == "https://github.com/o/r/actions/runs/7"
    assert ev.summary == ""


def test_map_ci_run_ignores_extra_and_sensitive_fields():
    # Biała lista pól (bezpieczeństwo, ADR 0024): nadmiarowe/wrażliwe klucze NIE mogą
    # przeciekać do żadnego pola zdarzenia.
    ev = selection.map_ci_run(
        _run(
            7,
            conclusion="failure",
            logs_url="https://leak/logs",
            jobs=[{"secret": "TOP-SECRET-JOB"}],
            head_commit={"message": "leaky-commit-msg"},
            secret_token="s3cr3t-token",
        )
    )
    assert ev is not None
    blob = f"{ev.title}\n{ev.summary}\n{ev.url}\n{ev.actor}\n{ev.external_id}"
    for leaked in ("leak/logs", "TOP-SECRET-JOB", "leaky-commit-msg", "s3cr3t-token"):
        assert leaked not in blob
    assert ev.actor == ""  # CI nie ma autora-człowieka


# --- select_events + watch_kinds -------------------------------------------


def test_select_events_issues_only_skips_pull_requests():
    pr = _issue(8, login="alice", pull_request={"url": "http://pr"})
    events = selection.select_events([pr], [], echo_seen=_bez_echa, watch_kinds=("issues",))
    assert events == []  # PR pominięty, bo nasłuchujemy tylko "issues"


def test_select_events_pulls_only_emits_pr_skips_issue():
    pr = _issue(8, login="alice", pull_request={"url": "http://pr"})
    events = selection.select_events(
        [pr, _issue(9, login="alice")], [], echo_seen=_bez_echa, watch_kinds=("pulls",)
    )
    assert [(e.kind, e.external_id) for e in events] == [("pr_opened", "8")]


def test_select_events_ci_watermark_filters_old_runs():
    old = _run(1, updated="2026-07-15T09:00:00Z")
    fresh = _run(2, updated="2026-07-15T15:00:00Z")
    events = selection.select_events(
        [],
        [],
        raw_runs=[old, fresh],
        echo_seen=_bez_echa,
        watch_kinds=("ci",),
        runs_since="2026-07-15T10:00:00Z",
    )
    assert [e.external_id for e in events] == ["2#1"]  # <= watermark pominięty


def test_select_events_review_watermark_filters_old_reviews():
    old = _review(1, submitted_at="2026-07-15T09:00:00Z")
    fresh = _review(2, submitted_at="2026-07-15T15:00:00Z")
    events = selection.select_events(
        [],
        [],
        raw_reviews=[old, fresh],
        echo_seen=_bez_echa,
        watch_kinds=("reviews",),
        reviews_since="2026-07-15T10:00:00Z",
    )
    assert [e.external_id for e in events] == ["2"]


def test_recenzje_traca_self_skip_BEZ_zamiennika():
    """ODWRÓCONE 2026-09-07 (ADR 0071 decyzja 6). Recenzji nie filtrujemy już wcale.

    Drzwi zapisu są create-only na zgłoszeniach i komentarzach (reguła 7, ADR 0021), więc nie
    istnieje droga, którą bylibyśmy autorem recenzji. Filtrowanie ich po koncie było czystą
    stratą — tą samą, co na zgłoszeniach, tylko mniej widoczną, bo recenzji jest mało.
    """
    events = selection.select_events(
        [], [], raw_reviews=[_review(1, login="me")], echo_seen=_bez_echa, watch_kinds=("reviews",)
    )

    assert [e.kind for e in events] == ["pr_review"]


def test_select_events_default_watch_kinds_backward_compatible():
    # Domyślne ("issues","comments") mapuje issue i komentarze jak przed ADR 0024.
    events = selection.select_events([_issue(1, login="alice")], [_comment(9)], echo_seen=_bez_echa)
    assert {(e.kind, e.external_id) for e in events} == {
        ("issue_opened", "1"),
        ("issue_comment", "9"),
    }
