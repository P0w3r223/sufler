"""Testy komendy ZAPISU ``/notatka`` (``MeetingNoteRouter``, produkcyjne M3, ADR 0009/0041, B1).

Router testujemy na ATRAPIE ``MeetingNoteService`` — bez Graph i bez Claude. Klucz: rozpoznanie
komendy, TRUSTED args (projekt/data/ref z komendy, nie z transkryptu), degradacja błędów do
czytelnego tekstu (komenda nie wywraca tury).
"""

from __future__ import annotations

from datetime import date

from workmate.adapters.inbound.commands import CommandContext
from workmate.adapters.inbound.meeting_command import MeetingNoteRouter
from workmate.core.application.meeting_authz import MeetingNoteAuthorizer
from workmate.core.application.meeting_notes import MeetingNoteOutcome
from workmate.core.domain.identity import Person
from workmate.core.domain.models import Note, NoteMetadata
from workmate.core.errors import LLMError, WriteError


class _FakeMeetingService:
    """Atrapa ``MeetingNoteService`` — notuje wejście, zwraca ``MeetingNoteOutcome`` albo rzuca.

    ``already_filed=True`` symuluje idempotencję (ADR 0043): notatka już istniała, ``note=None``.
    """

    def __init__(self, *, raises: Exception | None = None, already_filed: bool = False) -> None:
        self._raises = raises
        self._already_filed = already_filed
        self.calls: list[tuple[str, str, date]] = []

    def note_from_meeting(
        self, meeting_ref: str, *, project: str, meeting_date: date
    ) -> MeetingNoteOutcome:
        self.calls.append((meeting_ref, project, meeting_date))
        if self._raises is not None:
            raise self._raises
        note_id = f"mpwik/{project}/{meeting_date}-mtg-abc123"
        if self._already_filed:
            return MeetingNoteOutcome(note_id=note_id, created=False, note=None)
        note = Note(
            id=note_id,
            metadata=NoteMetadata(
                title="Przeglad",
                project=project,
                date=meeting_date,
                participants=["Anna Kowalska"],
                decisions=["Zamrozic v1"],
                action_items=["Anna: draft"],
                open_questions=[],
                tags=["api"],
            ),
            body="Streszczenie.",
        )
        return MeetingNoteOutcome(note_id=note.id, created=True, note=note)


_CTX = CommandContext("teams_graph", "team/chan/root")


def test_non_command_returns_none():
    router = MeetingNoteRouter(_FakeMeetingService())
    assert router.dispatch("zwykła wiadomość do agenta", _CTX) is None


def test_other_slash_command_returns_none():
    # ``/szukaj`` należy do read-only routera — ten router go NIE porywa.
    router = MeetingNoteRouter(_FakeMeetingService())
    assert router.dispatch("/szukaj integracja", _CTX) is None


def test_missing_args_returns_usage():
    router = MeetingNoteRouter(_FakeMeetingService())
    reply = router.dispatch("/notatka", _CTX)
    assert reply is not None and "Użycie:" in reply


def test_wrong_field_count_returns_usage():
    router = MeetingNoteRouter(_FakeMeetingService())
    reply = router.dispatch("/notatka https://join | scada-integration", _CTX)  # brak daty
    assert reply is not None and "Użycie:" in reply


def test_bad_date_returns_error():
    service = _FakeMeetingService()
    router = MeetingNoteRouter(service)
    reply = router.dispatch("/notatka https://join | scada-integration | 28-07-2026", _CTX)
    assert reply is not None and "RRRR-MM-DD" in reply
    assert service.calls == []  # zła data → nie dotykamy serwisu


def test_happy_path_uses_trusted_args_from_command():
    service = _FakeMeetingService()
    router = MeetingNoteRouter(service)

    reply = router.dispatch(
        "/notatka https://teams.microsoft.com/join/abc | scada-integration | 2026-07-28", _CTX
    )

    # Projekt i data pochodzą z KOMENDY (zaufane), nie z transkryptu (ADR 0009 §3).
    assert service.calls == [
        ("https://teams.microsoft.com/join/abc", "scada-integration", date(2026, 7, 28))
    ]
    assert reply is not None
    assert "Notatka ze spotkania zapisana" in reply
    assert "mpwik/scada-integration/2026-07-28-mtg-abc123" in reply


def test_accepts_bare_meeting_id_as_ref():
    service = _FakeMeetingService()
    router = MeetingNoteRouter(service)

    router.dispatch("/notatka MSoxMg-id | scada-integration | 2026-07-28", _CTX)

    assert service.calls[0][0] == "MSoxMg-id"


def test_write_error_degrades_to_message():
    service = _FakeMeetingService(raises=WriteError("nieznany projekt"))
    router = MeetingNoteRouter(service)

    reply = router.dispatch("/notatka ref | zly-projekt | 2026-07-28", _CTX)

    assert reply is not None and "Nie udało się złożyć notatki" in reply


def test_llm_error_degrades_to_message():
    service = _FakeMeetingService(raises=LLMError("Claude padł"))
    router = MeetingNoteRouter(service)

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", _CTX)

    assert reply is not None and "Nie udało się złożyć notatki" in reply


def test_transcript_error_degrades_to_message():
    service = _FakeMeetingService(raises=ValueError("spotkanie nie ma transkryptu"))
    router = MeetingNoteRouter(service)

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", _CTX)

    assert reply is not None and "Nie udało się pobrać transkryptu" in reply


def test_already_filed_reports_idempotent_skip():
    # Idempotencja (ADR 0043): notatka spotkania już istniała → komunikat o pominięciu, z id.
    service = _FakeMeetingService(already_filed=True)
    router = MeetingNoteRouter(service)

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", _CTX)

    assert reply is not None
    assert "była już złożona" in reply
    assert "mpwik/scada-integration/2026-07-28-mtg-abc123" in reply


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


def test_authorized_member_files_note():
    service = _FakeMeetingService()
    router = MeetingNoteRouter(service, authorizer=_AUTHZ)
    ctx = CommandContext("teams_graph", "team/chan/root", "aad-anna")

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", ctx)

    assert reply is not None and "Notatka ze spotkania zapisana" in reply
    assert service.calls  # rozpoznany członek → serwis wywołany


def test_unknown_sender_refused_before_fetch():
    service = _FakeMeetingService()
    router = MeetingNoteRouter(service, authorizer=_AUTHZ)
    ctx = CommandContext("teams_graph", "team/chan/root", "aad-obcy")

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", ctx)

    assert reply is not None and "Brak uprawnień" in reply
    assert service.calls == []  # odmowa PRZED pobraniem transkryptu (fail-closed)


def test_no_authorizer_skips_gate():
    # authorizer=None (operatorskie CLI, jeden zaufany user) → brak bramki członkostwa.
    service = _FakeMeetingService()
    router = MeetingNoteRouter(service)

    router.dispatch("/notatka ref | scada-integration | 2026-07-28", _CTX)

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
    service = _FakeMeetingService()
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = MeetingNoteRouter(service, scheduler=scheduler, callback=callback)
    ctx = CommandContext("teams_graph", "team/chan/root", "aad-anna")

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", ctx)

    # Natychmiastowa odpowiedź to ACK, nie treść notatki.
    assert reply is not None and "Przyjąłem" in reply
    assert scheduler.submitted == 1
    # Łańcuch policzony w tle; wynik trafił do WŁAŚCIWEGO wątku (external_id, nie od modelu).
    assert service.calls == [("ref", "scada-integration", date(2026, 7, 28))]
    assert len(callback.posts) == 1
    target, text = callback.posts[0]
    assert target == "team/chan/root"
    assert "Notatka ze spotkania zapisana" in text


def test_async_posts_error_text_on_failure():
    # Błąd łańcucha w tle → do wątku trafia CZYTELNY komunikat, nie cisza (nie ACK bez wyniku).
    service = _FakeMeetingService(raises=LLMError("Claude padł"))
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = MeetingNoteRouter(service, scheduler=scheduler, callback=callback)
    ctx = CommandContext("teams_graph", "team/chan/root", "aad-anna")

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", ctx)

    assert reply is not None and "Przyjąłem" in reply
    assert callback.posts[0][1].startswith("Nie udało się złożyć notatki")


def test_async_refusal_is_sync_and_schedules_no_background():
    # Odmowa autoryzacji jest NATYCHMIASTOWA (sync) — nie zleca zadania w tle (ADR 0042/0043).
    service = _FakeMeetingService()
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = MeetingNoteRouter(service, authorizer=_AUTHZ, scheduler=scheduler, callback=callback)
    ctx = CommandContext("teams_graph", "team/chan/root", "aad-obcy")

    reply = router.dispatch("/notatka ref | scada-integration | 2026-07-28", ctx)

    assert reply is not None and "Brak uprawnień" in reply
    assert scheduler.submitted == 0
    assert service.calls == []
    assert callback.posts == []
