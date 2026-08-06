"""Testy przechwycenia „zapisz to" (``ThreadNoteRouter``, F2, ADR 0048).

Lustro ``test_meeting_command`` dla innego WYZWALACZA: nie komenda ``/notatka`` z argami, lecz
@WZMIANKA bota (``mentions_bot``) niosąca dyrektywę ``zapisz to | <projekt>``. Router testujemy na
ATRAPIE ``ThreadNoteService`` — bez Graph i bez Claude. Klucz: wyzwalacz wymaga wzmianki (sama
fraza nie wystarcza), TRUSTED projekt z JAWNEGO argumentu (nie z treści wątku), data z Graph
``source_timestamp`` (deterministyczna), autoryzacja PRZED pracą, degradacja błędów do czytelnego
tekstu, async ACK + wynik do wątku.
"""

from __future__ import annotations

from datetime import date

from workmate.adapters.inbound.thread_note_command import ThreadNoteContext, ThreadNoteRouter
from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
from workmate.core.application.thread_notes import ThreadNoteOutcome
from workmate.core.domain.identity import Person
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import LLMError, WriteError

_TS = "2026-07-28T10:00:00Z"  # Graph ``created`` wzmianki → data notatki 2026-07-28.


class _FakeThreadService:
    """Atrapa ``ThreadNoteService`` — notuje wejście, zwraca ``ThreadNoteOutcome`` albo rzuca.

    ``already_filed=True`` symuluje idempotencję (ADR 0048): notatka wzmianki już istniała,
    ``note=None`` (pobór wątku i Claude pominięte).
    """

    def __init__(self, *, raises: Exception | None = None, already_filed: bool = False) -> None:
        self._raises = raises
        self._already_filed = already_filed
        self.calls: list[tuple[str, str, str, date]] = []

    def note_from_thread(
        self, external_id: str, source_message_id: str, *, project: str, on: date
    ) -> ThreadNoteOutcome:
        self.calls.append((external_id, source_message_id, project, on))
        if self._raises is not None:
            raise self._raises
        note_id = f"mpwik/{project}/{on}-thr-abc123"
        if self._already_filed:
            return ThreadNoteOutcome(note_id=note_id, created=False, note=None)
        note = Note(
            id=note_id,
            metadata=NoteMetadata(
                title="Przeglad",
                project=project,
                date=on,
                participants=["Anna Kowalska"],
                decisions=["Zamrozic v1"],
                action_items=["Anna: draft"],
                open_questions=[],
                tags=["api", "src:teams-thread"],
            ),
            body="Streszczenie.\n\nŹródło: wątek Teams (team/chan/root)",
        )
        return ThreadNoteOutcome(note_id=note.id, created=True, note=note)


def _ctx(
    *,
    sender_id: str = "",
    mentions_bot: bool = True,
    source_timestamp: str = _TS,
    external_id: str = "team/chan/root",
    source_message_id: str = "msg-1",
) -> ThreadNoteContext:
    return ThreadNoteContext(
        external_id=external_id,
        source_message_id=source_message_id,
        source_timestamp=source_timestamp,
        sender_id=sender_id,
        mentions_bot=mentions_bot,
    )


# --- wyzwalacz: wzmianka + dyrektywa ----------------------------------------


def test_no_mention_returns_none_even_with_directive_text():
    # Bez @wzmianki bota to zwykła wiadomość — nawet z frazą „zapisz to" w treści (bot i tak
    # odpowie normalną turą). Wyzwalacz zapisu wymaga wzmianki (ADR 0048).
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    assert router.dispatch("zapisz to | scada-integration", _ctx(mentions_bot=False)) is None
    assert service.calls == []


def test_mention_without_directive_returns_none():
    # Wzmianka bez „zapisz to" → normalna tura agenta (None), nie wyzwalacz zapisu.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    assert router.dispatch("@WorkMate co słychać w projekcie?", _ctx()) is None
    assert service.calls == []


def test_directive_without_project_returns_usage():
    # „zapisz to" bez „| projekt" → podpowiedź składni, bez dotykania serwisu.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to", _ctx())

    assert reply is not None and "podaj projekt" in reply
    assert service.calls == []


def test_happy_path_uses_trusted_project_and_graph_timestamp_date():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    # Projekt z JAWNEGO argumentu wzmianki (zaufany), data z Graph timestampu (deterministyczna).
    assert service.calls == [("team/chan/root", "msg-1", "scada-integration", date(2026, 7, 28))]
    assert reply is not None
    assert "Notatka z wątku zapisana" in reply
    assert "mpwik/scada-integration/2026-07-28-thr-abc123" in reply


# --- data z Graph timestampu ------------------------------------------------


def test_empty_timestamp_returns_bad_timestamp_message():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx(source_timestamp=""))

    assert reply is not None and "Nie udało się ustalić daty wątku" in reply
    assert service.calls == []  # bez daty nie ruszamy serwisu


def test_bad_timestamp_returns_bad_timestamp_message():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch(
        "@WorkMate zapisz to | scada-integration", _ctx(source_timestamp="wczoraj")
    )

    assert reply is not None and "Nie udało się ustalić daty wątku" in reply
    assert service.calls == []


# --- idempotencja + degradacja błędów ---------------------------------------


def test_already_filed_reports_idempotent_skip():
    # Idempotencja (ADR 0048): notatka wzmianki już istniała (note=None) → komunikat „był już
    # zapisany", z deterministycznym id.
    service = _FakeThreadService(already_filed=True)
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    assert reply is not None
    assert "był już zapisany" in reply
    assert "mpwik/scada-integration/2026-07-28-thr-abc123" in reply


def test_write_error_degrades_to_message():
    service = _FakeThreadService(raises=WriteError("nieznany projekt"))
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to | zly-projekt", _ctx())

    assert reply is not None and "Nie udało się zapisać notatki z wątku" in reply


def test_llm_error_degrades_to_message():
    service = _FakeThreadService(raises=LLMError("Claude padł"))
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    assert reply is not None and "Nie udało się zapisać notatki z wątku" in reply


def test_thread_fetch_value_error_degrades_to_message():
    # Zły external_id / brak wątku z Graph (ValueError) → czytelny komunikat, nie wyjątek.
    service = _FakeThreadService(raises=ValueError("zły external_id wątku"))
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    assert reply is not None and "Nie udało się pobrać treści wątku" in reply


# --- autoryzacja nadawcy (B2 / ADR 0042) ------------------------------------


class _FakeLookup:
    """Atrapa ``AadIdentityLookup`` — zna wskazane AAD id, resztę zwraca ``None``."""

    def __init__(self, people: dict[str, Person]) -> None:
        self._people = people

    def resolve_by_aad_user_id(self, aad_user_id: str) -> Person | None:
        return self._people.get(aad_user_id)


_ANNA = Person(
    source_id="EMP-1", aad_user_id="aad-anna", jira_user="anna@example.org", display_name="Anna"
)
_AUTHZ = MeetingNoteAuthorizer(_FakeLookup({"aad-anna": _ANNA}))


def test_authorized_member_saves_note():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, authorizer=_AUTHZ)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx(sender_id="aad-anna"))

    assert reply is not None and "Notatka z wątku zapisana" in reply
    assert service.calls  # rozpoznany członek → serwis wywołany


def test_unknown_sender_refused_before_work():
    # Autoryzacja PRZED poborem wątku (fail-closed): nieznany nadawca → odmowa, serwis NIE ruszony.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, authorizer=_AUTHZ)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx(sender_id="aad-obcy"))

    assert reply is not None and "Brak uprawnień" in reply
    assert service.calls == []


def test_no_authorizer_skips_gate():
    # authorizer=None (operatorskie ścieżki) → brak bramki członkostwa.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    assert service.calls


# --- async fire-and-forget (B3 / ADR 0043) ----------------------------------


class _InlineScheduler:
    """Atrapa schedulera — wykonuje thunk NATYCHMIAST (deterministyczny test bez wątków)."""

    def __init__(self) -> None:
        self.submitted = 0

    def __call__(self, thunk: object) -> None:
        self.submitted += 1
        thunk()  # type: ignore[operator]


class _RecordingCallback:
    """Atrapa callbacku — notuje (cel wątku, tekst) zamiast realnej wysyłki do Teams."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, str]] = []

    def __call__(self, target: str, text: str) -> None:
        self.posts.append((target, text))


def test_async_returns_ack_and_posts_result_to_thread():
    service = _FakeThreadService()
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = ThreadNoteRouter(service, scheduler=scheduler, callback=callback)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    # Natychmiastowa odpowiedź to ACK, nie treść notatki.
    assert reply is not None and "Przyjąłem" in reply
    assert scheduler.submitted == 1
    # Łańcuch policzony w tle; wynik trafił do WŁAŚCIWEGO wątku (external_id).
    assert service.calls == [("team/chan/root", "msg-1", "scada-integration", date(2026, 7, 28))]
    assert len(callback.posts) == 1
    target, text = callback.posts[0]
    assert target == "team/chan/root"
    assert "Notatka z wątku zapisana" in text


def test_async_posts_error_text_on_failure():
    # Błąd łańcucha w tle → do wątku trafia CZYTELNY komunikat, nie cisza (nie ACK bez wyniku).
    service = _FakeThreadService(raises=LLMError("Claude padł"))
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = ThreadNoteRouter(service, scheduler=scheduler, callback=callback)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx())

    assert reply is not None and "Przyjąłem" in reply
    assert callback.posts[0][1].startswith("Nie udało się zapisać notatki z wątku")


def test_async_refusal_is_sync_and_schedules_no_background():
    # Odmowa autoryzacji jest NATYCHMIASTOWA (sync) — nie zleca zadania w tle (ADR 0042/0043).
    service = _FakeThreadService()
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = ThreadNoteRouter(service, authorizer=_AUTHZ, scheduler=scheduler, callback=callback)

    reply = router.dispatch("@WorkMate zapisz to | scada-integration", _ctx(sender_id="aad-obcy"))

    assert reply is not None and "Brak uprawnień" in reply
    assert scheduler.submitted == 0
    assert service.calls == []
    assert callback.posts == []
