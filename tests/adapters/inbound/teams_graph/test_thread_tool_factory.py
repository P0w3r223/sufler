"""Testy fabryki ``reply_on_thread`` per turę (_make_thread_tool_factory, ADR 0024, Faza 3b).

Fabryka mapuje ``external_id`` drzwi teams_graph (konwencja ``team/channel/root``) na cel wątku z
``ThreadLinkStore`` i wstrzykuje scoped narzędzie z PRE-ZWIĄZANYM numerem. Sprawdzamy: powiązany
wątek → narzędzie komentujące właściwy numer; brak powiązania → pusta lista; źle uformowany
``external_id`` (nie 3 części) → pusta lista bez wyjątku. Na atrapach (link store + port zapisu).
"""

from __future__ import annotations

from workmate.adapters.inbound.teams_graph.app import (
    _build_bridge_catalog,
    _build_file_reply_factory,
    _build_user_doc_push_factory,
    _build_user_push_factory,
    _compose_thread_factories,
    _compose_user_push_factories,
    _make_thread_tool_factory,
)
from workmate.config import EventsSettings, GithubSettings, TeamsGraphSettings
from workmate.core.application.github import GithubWriteService


class _RecordingWriter:
    """Atrapa ``GithubWritePort`` — notuje ``issue_number`` z create_comment."""

    def __init__(self) -> None:
        self.comments: list[tuple[str, str, int, str]] = []

    def create_issue(self, owner, repo, title, body, labels):
        return {"number": 1, "html_url": "http://gh/1", "created_at": "2026-07-15T10:00:00Z"}

    def create_comment(self, owner, repo, issue_number, body):
        self.comments.append((owner, repo, issue_number, body))
        return {"id": 5, "html_url": "http://gh/c/5", "created_at": "2026-07-15T10:00:00Z"}


class _FakeThreadLinks:
    """Atrapa ``ThreadLinkStore`` — mapuje (team, channel, root) → cel (kind, number)."""

    def __init__(self, targets: dict[tuple[str, str, str], tuple[str, str]]) -> None:
        self._targets = targets

    def get_root(self, team_id, channel_id, target_kind, target_number):
        return None

    def get_target(self, team_id, channel_id, root_id):
        return self._targets.get((team_id, channel_id, root_id))

    def link(self, team_id, channel_id, target_kind, target_number, root_id):
        return None


def _write_service(writer) -> GithubWriteService:
    return GithubWriteService(writer, owner="o", repo="r")


def test_linked_thread_yields_reply_tool_that_comments_mapped_number():
    writer = _RecordingWriter()
    links = _FakeThreadLinks({("team-1", "chan-1", "root-9"): ("pr", "12")})
    factory = _make_thread_tool_factory(links, _write_service(writer))

    tools = factory("team-1/chan-1/root-9")

    assert [spec.name for spec in tools] == ["reply_on_thread"]
    tools[0].fn(body="odpowiedź z wątku")
    # Numer wzięty z zaufanego mapowania (12), nie od modelu.
    assert writer.comments == [("o", "r", 12, "odpowiedź z wątku")]


def test_unlinked_thread_yields_empty_list():
    links = _FakeThreadLinks({})  # get_target zwróci None
    factory = _make_thread_tool_factory(links, _write_service(_RecordingWriter()))

    assert factory("team-1/chan-1/root-9") == []


def test_malformed_external_id_without_three_parts_yields_empty_list():
    """Sam nadawca (``u1``) zamiast ``team/channel/root`` → pusta lista, bez wyjątku."""
    links = _FakeThreadLinks({})
    factory = _make_thread_tool_factory(links, _write_service(_RecordingWriter()))

    assert factory("u1") == []


def test_too_many_parts_external_id_yields_empty_list():
    links = _FakeThreadLinks({("a", "b", "c"): ("issue", "1")})
    factory = _make_thread_tool_factory(links, _write_service(_RecordingWriter()))

    # 4 części ≠ 3 → nie próbujemy mapować (obrona przed nietypowym id).
    assert factory("a/b/c/d") == []


def test_bridge_catalog_gate_off_yields_no_thread_factory():
    """Strukturalna gwarancja: zapis do GitHub OFF → fabryka wątkowa NIE powstaje (None).

    Bez włączonej bramki agent nie dostaje ani narzędzi zapisu, ani ``reply_on_thread`` —
    powierzchnia mutująca w ogóle się nie materializuje (jak Gate 4 / ADR 0021). Jira (ADR 0054)
    nie ma tu żadnej zdolności — most push/zapis usunięty, "moje zadania" wchodzi osobną fabryką.
    """
    catalog, factory = _build_bridge_catalog(
        EventsSettings(db_path=":memory:"),
        GithubSettings(enable_github_write=False, token="t", owner="o", repo="r"),
    )

    assert factory is None  # brak fabryki = brak reply_on_thread
    # Od kroku 5.2 (ADR 0009) to JEDNO narzędzie: odczyt zdarzeń, podsumowanie aktywności projektu
    # (ADR 0029) i propozycja czasu z commitów (ADR 0034) są jego akcjami. Zapis GitHub OFF, więc
    # akcje mutujące nie istnieją w schemacie — bramka siedzi w `Literal`, nie w ciele funkcji.
    assert [spec.name for spec in catalog] == ["GitHub"]
    akcje = str(catalog[0].fn.__annotations__["action"])
    assert "events" in akcje and "activity" in akcje and "worklog" in akcje
    assert "create_issue" not in akcje and "comment" not in akcje


# --- fabryka reply_with_file (ADR 0026, A′2) + kompozycja fabryk wątkowych --------


def test_file_reply_factory_off_by_default_is_none():
    """Strukturalna gwarancja: bramka OFF → fabryka pliku NIE powstaje (brak powierzchni zapisu)."""
    assert _build_file_reply_factory(TeamsGraphSettings(), lambda: "tok") is None


def test_file_reply_factory_on_yields_scoped_reply_with_file_tool():
    """Bramka ON → dla poprawnego wątku (team/channel/root) agent dostaje ``reply_with_file``."""
    settings = TeamsGraphSettings(enable_file_reply=True, max_file_reply_kb=256)
    factory = _build_file_reply_factory(settings, lambda: "tok")
    assert factory is not None
    assert [spec.name for spec in factory("team-1/chan-1/root-9")] == ["reply_with_file"]
    # Źle uformowany external_id (nie 3 części) → pusta lista, bez wyjątku (jak fabryka GitHub).
    assert factory("u1") == []


def test_compose_thread_factories_concatenates_and_ignores_none():
    combined = _compose_thread_factories(None, lambda eid: ["a"], None, lambda eid: ["b", "c"])
    assert combined is not None
    assert combined("team/chan/root") == ["a", "b", "c"]


def test_compose_thread_factories_all_none_is_none():
    assert _compose_thread_factories(None, None) is None


def test_compose_thread_factories_single_returns_it_directly():
    only = _make_thread_tool_factory(_FakeThreadLinks({}), _write_service(_RecordingWriter()))
    assert _compose_thread_factories(None, only) is only


# --- fabryka send_image_to_user (ADR 0027, A′3) — klucz = nadawca, nie wątek -----

# Zakresy czatu wymagane bramką push-u; dokładamy do domyślnych, by konfiguracja była spójna.
_CHAT_SCOPES = ("Chat.Create", "ChatMessage.Send")


def test_user_push_factory_off_by_default_is_none():
    """Strukturalna gwarancja: bramka OFF → fabryka push-u NIE powstaje (brak zapisu)."""
    assert _build_user_push_factory(TeamsGraphSettings(), lambda: "tok") is None


def test_user_push_factory_on_yields_scoped_send_image_tool():
    """Bramka ON → nadawca (sender_id) dostaje ``send_image_to_user``; pusty nadawca → []."""
    settings = TeamsGraphSettings(
        enable_user_file_push=True,
        max_user_image_kb=256,
        scopes=(*TeamsGraphSettings().scopes, *_CHAT_SCOPES),
    )
    factory = _build_user_push_factory(settings, lambda: "tok")
    assert factory is not None
    # Klucz to sender_id (AAD id nadawcy), NIE external_id wątku — narzędzie dla realnego nadawcy.
    assert [spec.name for spec in factory("u-anna-aad")] == ["send_image_to_user"]
    # Pusty sender_id (drzwi bez pojęcia nadawcy) → brak celu → pusta lista, bez wyjątku.
    assert factory("") == []


# --- fabryka send_document_to_user (ADR 0027, wariant plikowy) + kompozycja push-u --------

# Dokument wymaga zakresów czatu ORAZ zapisu (upload na OneDrive) — dokładamy oba do domyślnych.
_DOC_SCOPES = (*_CHAT_SCOPES, "Files.ReadWrite.All")


def test_user_doc_push_factory_off_by_default_is_none():
    """Strukturalna gwarancja: bramka OFF → fabryka dokumentu NIE powstaje (brak zapisu)."""
    assert _build_user_doc_push_factory(TeamsGraphSettings(), lambda: "tok") is None


def test_user_doc_push_factory_on_yields_scoped_send_document_tool():
    """Bramka ON → nadawca (sender_id) dostaje ``send_document_to_user``; pusty nadawca → []."""
    settings = TeamsGraphSettings(
        enable_user_doc_push=True,
        max_user_doc_kb=256,
        scopes=(*TeamsGraphSettings().scopes, *_DOC_SCOPES),
    )
    factory = _build_user_doc_push_factory(settings, lambda: "tok")
    assert factory is not None
    assert [spec.name for spec in factory("u-anna-aad")] == ["send_document_to_user"]
    assert factory("") == []  # pusty sender_id → brak celu → pusta lista


def test_compose_user_push_factories_concatenates_image_and_doc():
    """Obraz + dokument (obie kluczowane sender_id) łączą się w jedną fabrykę per turę."""
    combined = _compose_user_push_factories(
        lambda sid: ["send_image_to_user"], lambda sid: ["send_document_to_user"]
    )
    assert combined is not None
    assert combined("u-anna") == ["send_image_to_user", "send_document_to_user"]


def test_compose_user_push_factories_all_none_is_none():
    assert _compose_user_push_factories(None, None) is None


def test_compose_user_push_factories_single_returns_it_directly():
    only = _build_user_push_factory(
        TeamsGraphSettings(
            enable_user_file_push=True, scopes=(*TeamsGraphSettings().scopes, *_CHAT_SCOPES)
        ),
        lambda: "tok",
    )
    assert only is not None
    assert _compose_user_push_factories(None, only) is only
