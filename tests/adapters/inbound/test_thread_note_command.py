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

from sufler.adapters.inbound.thread_note_command import ThreadNoteContext, ThreadNoteRouter
from sufler.core.application.meeting_authz import MeetingNoteAuthorizer
from sufler.core.application.thread_notes import ThreadNoteOutcome
from sufler.core.domain.identity import Person
from sufler.core.domain.models import Note, NoteMetadata, Project
from sufler.core.errors import LLMError, WriteError

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
    mention_texts: tuple[str, ...] = (),
) -> ThreadNoteContext:
    return ThreadNoteContext(
        external_id=external_id,
        source_message_id=source_message_id,
        source_timestamp=source_timestamp,
        sender_id=sender_id,
        mentions_bot=mentions_bot,
        mention_texts=mention_texts,
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

    assert router.dispatch("@Sufler co słychać w projekcie?", _ctx()) is None
    assert service.calls == []


def test_directive_without_project_returns_usage():
    # „zapisz to" bez „| projekt" → podpowiedź składni, bez dotykania serwisu.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to", _ctx())

    assert reply is not None and "podaj projekt" in reply
    assert service.calls == []


def test_happy_path_uses_trusted_project_and_graph_timestamp_date():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

    # Projekt z JAWNEGO argumentu wzmianki (zaufany), data z Graph timestampu (deterministyczna).
    assert service.calls == [("team/chan/root", "msg-1", "scada-integration", date(2026, 7, 28))]
    assert reply is not None
    assert "Notatka z wątku zapisana" in reply
    assert "mpwik/scada-integration/2026-07-28-thr-abc123" in reply


# --- data z Graph timestampu ------------------------------------------------


def test_data_notatki_liczy_sie_w_STREFIE_DRZWI_nie_w_utc_znacznika():
    """Regresja: „zapisz to" o 23:30 czasu warszawskiego zakładało notatkę pod POPRZEDNIM dniem.

    Graph podaje czas w UTC, a data wchodzi do ``build_note_id`` — notatka dostawała więc zarówno
    inny dzień w treści, jak i inny identyfikator. Notatki `-thr-` są niezmienne, więc korekta
    wymaga założenia nowej. Bliźniacze drzwi digestu konwertują strefę od początku.
    """
    from zoneinfo import ZoneInfo

    service = _FakeThreadService()
    router = ThreadNoteRouter(service, tz=ZoneInfo("Europe/Warsaw"))

    # 21:30 UTC = 23:30 w Warszawie → nadal 28 lipca.
    router.dispatch(
        "@Sufler zapisz to | scada-integration",
        _ctx(source_timestamp="2026-07-28T21:30:00Z"),
    )
    # 22:30 UTC = 00:30 następnego dnia w Warszawie → już 29 lipca.
    router.dispatch(
        "@Sufler zapisz to | scada-integration",
        _ctx(source_timestamp="2026-07-28T22:30:00Z"),
    )

    assert [wywolanie[3] for wywolanie in service.calls] == [
        date(2026, 7, 28),
        date(2026, 7, 29),
    ]


def test_bez_strefy_data_zostaje_w_utc_jak_dotad():
    """Kontrast: ścieżki operatorskie i testy bez strefy zachowują dawne zachowanie."""
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    router.dispatch(
        "@Sufler zapisz to | scada-integration",
        _ctx(source_timestamp="2026-07-28T22:30:00Z"),
    )

    assert service.calls[0][3] == date(2026, 7, 28)


def test_empty_timestamp_returns_bad_timestamp_message():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx(source_timestamp=""))

    assert reply is not None and "Nie udało się ustalić daty wątku" in reply
    assert service.calls == []  # bez daty nie ruszamy serwisu


def test_bad_timestamp_returns_bad_timestamp_message():
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    reply = router.dispatch(
        "@Sufler zapisz to | scada-integration", _ctx(source_timestamp="wczoraj")
    )

    assert reply is not None and "Nie udało się ustalić daty wątku" in reply
    assert service.calls == []


# --- idempotencja + degradacja błędów ---------------------------------------


def test_already_filed_reports_idempotent_skip():
    # Idempotencja (ADR 0048): notatka wzmianki już istniała (note=None) → komunikat „był już
    # zapisany", z deterministycznym id.
    service = _FakeThreadService(already_filed=True)
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

    assert reply is not None
    assert "był już zapisany" in reply
    assert "mpwik/scada-integration/2026-07-28-thr-abc123" in reply


def test_write_error_degrades_to_message():
    service = _FakeThreadService(raises=WriteError("nieznany projekt"))
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to | zly-projekt", _ctx())

    assert reply is not None and "Nie udało się zapisać notatki z wątku" in reply


def test_llm_error_degrades_to_message():
    service = _FakeThreadService(raises=LLMError("Claude padł"))
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

    assert reply is not None and "Nie udało się zapisać notatki z wątku" in reply


def test_thread_fetch_value_error_degrades_to_message():
    # Zły external_id / brak wątku z Graph (ValueError) → czytelny komunikat, nie wyjątek.
    service = _FakeThreadService(raises=ValueError("zły external_id wątku"))
    router = ThreadNoteRouter(service)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

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

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx(sender_id="aad-anna"))

    assert reply is not None and "Notatka z wątku zapisana" in reply
    assert service.calls  # rozpoznany członek → serwis wywołany


def test_unknown_sender_refused_before_work():
    # Autoryzacja PRZED poborem wątku (fail-closed): nieznany nadawca → odmowa, serwis NIE ruszony.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, authorizer=_AUTHZ)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx(sender_id="aad-obcy"))

    assert reply is not None and "Brak uprawnień" in reply
    assert service.calls == []


def test_no_authorizer_skips_gate():
    # authorizer=None (operatorskie ścieżki) → brak bramki członkostwa.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service)

    router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

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

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

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

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

    assert reply is not None and "Przyjąłem" in reply
    assert callback.posts[0][1].startswith("Nie udało się zapisać notatki z wątku")


def test_async_refusal_is_sync_and_schedules_no_background():
    # Odmowa autoryzacji jest NATYCHMIASTOWA (sync) — nie zleca zadania w tle (ADR 0042/0043).
    service = _FakeThreadService()
    scheduler = _InlineScheduler()
    callback = _RecordingCallback()
    router = ThreadNoteRouter(service, authorizer=_AUTHZ, scheduler=scheduler, callback=callback)

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx(sender_id="aad-obcy"))

    assert reply is not None and "Brak uprawnień" in reply
    assert scheduler.submitted == 0
    assert service.calls == []
    assert callback.posts == []


# --- podpowiedź z REALNEGO rejestru i forma naturalna (demo 2026-08-21) -----


class _FakeProjects:
    """Atrapa ``ProjectsRepository`` — tylko ``all()``; ``raises`` symuluje zepsuty rejestr."""

    def __init__(self, klucze: tuple[str, ...], *, raises: Exception | None = None) -> None:
        self._klucze = klucze
        self._raises = raises
        self.odczyty = 0

    def all(self) -> list[Project]:
        self.odczyty += 1
        if self._raises is not None:
            raise self._raises
        return [Project(key=k, company="biap", name=k, description="d") for k in self._klucze]

    def get(self, key: str) -> Project | None:  # pragma: no cover - router tego nie woła
        raise AssertionError("router nie rozstrzyga istnienia projektu — robi to serwis")

    def status_record(self, key: str) -> None:  # pragma: no cover - router tego nie woła
        raise AssertionError("router nie czyta statusów")


def test_usage_lists_real_registry_keys():
    # Podpowiedź wypisuje klucze Z REJESTRU. Wersja z wymyślonym „np. scada-integration"
    # dawała odmowę każdemu, kto skopiował przykład (demo 2026-08-21): rejestr niósł jeden
    # klucz `workmate`, a `scada` nie występował ani tam, ani w notatkach.
    projects = _FakeProjects(("workmate", "biap-www"))
    router = ThreadNoteRouter(_FakeThreadService(), projects=projects)

    reply = router.dispatch("@Sufler zapisz to", _ctx())

    assert reply is not None
    assert "biap-www, workmate" in reply  # posortowane, realne
    assert "scada-integration" not in reply


def test_usage_without_registry_falls_back_to_syntax_only():
    # Bez repozytorium (ścieżki operatorskie) podpowiedź nadal działa — bez listy kluczy
    # i BEZ wymyślonego przykładu.
    router = ThreadNoteRouter(_FakeThreadService())

    reply = router.dispatch("@Sufler zapisz to", _ctx())

    assert reply is not None and "podaj projekt" in reply
    assert "scada-integration" not in reply


def test_broken_registry_degrades_usage_instead_of_breaking_the_trigger():
    # Rejestr jest tu wygodą, nie bramką: jego awaria ma zdegradować podpowiedź, a wyzwalacz
    # ze składnią z kreską ma działać dalej.
    service = _FakeThreadService()
    projects = _FakeProjects((), raises=RuntimeError("YAML padł"))
    router = ThreadNoteRouter(service, projects=projects)

    assert router.dispatch("@Sufler zapisz to", _ctx()) is not None
    reply = router.dispatch("@Sufler zapisz to | workmate", _ctx())

    assert reply is not None and reply.startswith("✓")
    assert service.calls[0][2] == "workmate"


def test_natural_phrasing_resolves_the_project_from_the_mention_text():
    # Forma, którą człowiek napisał na demo. Klucz nadal pochodzi z tekstu WZMIANKI (ADR 0009
    # §3) — luźniejszy parser tej gwarancji nie rusza, bo źródłem jest ta sama wiadomość.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("@Sufler zapisz to jako notatkę projektu workmate", _ctx())

    assert reply is not None and reply.startswith("✓")
    assert service.calls[0][2] == "workmate"


def test_unknown_word_after_directive_gives_usage_instead_of_a_guess():
    # Bez kreski klucz musi BYĆ kluczem rejestru. Słowo spoza rejestru nie ma się stać
    # miejscem zapisu — zgadywanie jest gorsze niż pytanie.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("@Sufler zapisz to jako notatkę projektu klienta", _ctx())

    assert reply is not None and "podaj projekt" in reply
    assert service.calls == []


def test_two_registry_keys_in_one_mention_give_usage():
    # Dwa klucze w jednym zdaniu → nie zgadujemy, który; podpowiedź i decyzja człowieka.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate", "biap-www")))

    reply = router.dispatch("@Sufler zapisz to do workmate albo biap-www", _ctx())

    assert reply is not None and "podaj projekt" in reply
    assert service.calls == []


def test_pipe_syntax_keeps_working_for_keys_outside_the_registry():
    # Kreska zostaje drogą DOSŁOWNĄ: bierze wszystko po ``|`` niezależnie od rejestru, a o tym,
    # czy projekt istnieje, rozstrzyga serwis (``require_project``). Rozluźnienie nie przeniosło
    # tej decyzji do routera.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("@Sufler zapisz to | scada-integration", _ctx())

    assert reply is not None and reply.startswith("✓")
    assert service.calls[0][2] == "scada-integration"


def test_mention_without_directive_never_touches_the_registry():
    # Router konsultuje KAŻDĄ wzmiankę bota, także zwykłe pytanie, które przepuszcza dalej.
    # Rejestr ma czytać wyłącznie ta garstka, która niesie „zapisz to" — inaczej każda wzmianka
    # statuje plik rejestru, a przy zepsutym pliku logujemy ostrzeżenie w każdej takiej turze.
    projects = _FakeProjects(("workmate",))
    router = ThreadNoteRouter(_FakeThreadService(), projects=projects)

    assert router.dispatch("@Sufler co słychać w projekcie?", _ctx()) is None
    assert projects.odczyty == 0


def test_directive_reads_the_registry_once():
    # Jeden odczyt na turę: podpowiedź i rozpoznanie klucza dzielą ten sam wynik.
    projects = _FakeProjects(("workmate", "biap-www"))
    router = ThreadNoteRouter(_FakeThreadService(), projects=projects)

    assert router.dispatch("@Sufler zapisz to", _ctx()) is not None
    assert projects.odczyty == 1


# --- przegląd kodu 2026-08-21: wzmianka nie jest argumentem -----------------


_BOT = ("Virtual Sufler",)


def test_bot_mention_after_the_directive_is_not_a_project_key():
    """Nazwa bota zawiera klucz rejestru — i po ``_strip_html`` wygląda jak słowo człowieka.

    „Zapisz to, @Virtual Sufler" zapisywało wątek pod projekt `workmate` PO CICHU: w trybie
    async ACK nie nazywa projektu, a notatki `-thr-` są NIEZMIENNE, więc pomyłki nie dało się
    ani zauważyć, ani cofnąć. Wzmianka jest adresatem, nie argumentem.
    """
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("Zapisz to, Virtual Sufler", _ctx(mention_texts=_BOT))

    assert reply is not None and "podaj projekt" in reply
    assert service.calls == []


def test_mention_before_the_directive_still_lets_the_sentence_name_the_project():
    """Wycinamy wzmiankę POZYCYJNIE, nie po wartości tokenu.

    Gdyby wykluczać token `workmate` dlatego, że występuje w nazwie bota, zginęłaby forma,
    dla której cała ta ścieżka powstała.
    """
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch(
        "Virtual Sufler zapisz to jako notatkę projektu workmate", _ctx(mention_texts=_BOT)
    )

    assert reply is not None and reply.startswith("✓")
    assert service.calls[0][2] == "workmate"


def test_a_pasted_url_whose_path_matches_a_key_is_not_a_project_key():
    # Segment ścieżki bywa równy kluczowi projektu; wklejony link nie jest poleceniem zapisu.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch(
        "zapisz to https://github.com/BIAP/workmate/pull/78", _ctx(mention_texts=_BOT)
    )

    assert reply is not None and "podaj projekt" in reply
    assert service.calls == []


def test_pipe_in_prose_falls_back_to_the_key_named_in_the_sentence():
    """Kreska wygrywała nad formą naturalną przy KAŻDYM ``|`` w wiadomości.

    Podpowiedź reklamuje teraz formę naturalną, a wiadomości Teams rutynowo niosą ``|``.
    Tekst po kresce nie jest kluczem, zdanie niesie dokładnie jeden — wygrywa zdanie.
    """
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("zapisz to jako notatkę projektu workmate | dzięki", _ctx())

    assert reply is not None and reply.startswith("✓")
    assert service.calls[0][2] == "workmate"


def test_pipe_still_wins_literally_when_it_names_a_registry_key():
    # Kontrakt kreski zostaje: tekst po ``|`` jest argumentem, gdy jest kluczem rejestru.
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate", "biap-www")))

    router.dispatch("zapisz to do workmate | biap-www", _ctx())

    assert service.calls[0][2] == "biap-www"


def test_unknown_key_after_the_pipe_is_quoted_back_by_the_service():
    # Gdy ani kreska, ani zdanie nie dają klucza, do serwisu idzie to, co człowiek NAPISAŁ —
    # komunikat „nieznany projekt" ma cytować jego tekst, nie milczeć.
    service = _FakeThreadService(raises=WriteError("nieznany projekt: literowka"))
    router = ThreadNoteRouter(service, projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("zapisz to | literowka", _ctx())

    assert reply is not None and "literowka" in reply
    assert service.calls[0][2] == "literowka"


def test_registry_key_with_capitals_is_matched_and_returned_canonically():
    """``require_project`` porównuje bez względu na wielkość liter — parser musi też.

    Inaczej klucz z wersalikiem daje się WYPISAĆ w podpowiedzi, ale nie daje się użyć:
    człowiek kopiuje go z komunikatu bota i dostaje ten sam komunikat.
    """
    service = _FakeThreadService()
    router = ThreadNoteRouter(service, projects=_FakeProjects(("Sufler",)))

    router.dispatch("zapisz to jako notatkę projektu sufler", _ctx())

    assert service.calls[0][2] == "Sufler"


def test_usage_shows_no_copyable_mention_token():
    """Wyzwalacz stoi na ``mentions[]`` z Graph, nie na tekście — `@Nazwa` jest nie do skopiowania.

    Podpowiedź drukowała `@Sufler`, gdy bot na produkcji nazywa się `Virtual Sufler`. Sama
    podmiana nazwy zostawiłaby przykład NIEKOPIOWALNY: wklejone `@cokolwiek` to zwykłe słowo
    i wzmianki nie tworzy. Zdanie mówi teraz, co zrobić, zamiast pokazywać znaki do przepisania.
    """
    router = ThreadNoteRouter(_FakeThreadService(), projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("@Virtual Sufler zapisz to", _ctx(mention_texts=("Virtual Sufler",)))

    assert reply is not None
    assert "@" not in reply
    assert "podpowiedzi Teams" in reply


def test_usage_gives_the_reason_it_refuses():
    """Odmowa bez powodu wygląda jak awaria — sonda 2026-08-21 po migracji 1.13.0.

    Człowiek napisał „Zapisz to, @Virtual Sufler", dostał podpowiedź i odczytał ją jako
    „bot nie zrozumiał". Router zachował się poprawnie (bez tego zapisałby wątek pod `workmate`,
    bo nazwa bota niesie klucz rejestru), ale nie powiedział, DLACZEGO pyta.
    """
    router = ThreadNoteRouter(_FakeThreadService(), projects=_FakeProjects(("workmate",)))

    reply = router.dispatch("Zapisz to, Virtual Sufler", _ctx(mention_texts=("Virtual Sufler",)))

    assert reply is not None
    assert "moja nazwa" in reply and "treść wątku" in reply
    assert "Klucze z rejestru: workmate." in reply
