"""Regresje szwu ``ConversationalResponder``: perymetr bramki odczytu i kolejność skazy.

Bez LLM i bez sieci: atrapa runtime notuje katalog narzędzi tury, routery dyrektyw są atrapami
notującymi kontekst, magazyn rozmów to prawdziwy SQLite w pamięci. Sedno: co responder WIE
o nadawcy w chwili, w której buduje katalog i konsultuje routery — bo obie te chwile decydowały
o obejściu bramki (ADR 0062) i o ślepocie sędziego mutacji (ADR 0066).
"""

from __future__ import annotations

import asyncio
from typing import Any

from workmate.adapters.inbound.responder import ConversationalResponder, InboundMessage
from workmate.adapters.outbound.sqlite_conversations import SqliteConversationStore
from workmate.core.application.conversations import ConversationService
from workmate.core.domain.pricing import TokenUsage
from workmate.core.ports.llm import AgentResult, AssistantTurn, Attachment, UserText


class _FakeRuntime:
    """Atrapa runtime: notuje ``extra_tools`` i ``trust`` tury, oddaje stałą odpowiedź."""

    def __init__(self) -> None:
        self.extra_tools: list[Any] = []
        self.trust: str = ""

    def run_turn(
        self,
        query: str,
        *,
        attachments: object = (),
        history: object = (),
        extra_tools: object = (),
        session_header: str = "",
        audit: object = None,
        attachment_queue: object = None,
        trust_nonce: str = "",
        trust: str = "T1",
    ) -> AgentResult:
        self.extra_tools = list(extra_tools)  # type: ignore[arg-type]
        self.trust = trust
        entries = (UserText(query), AssistantTurn("ok", (), (), usage=TokenUsage()))
        return AgentResult(reply="ok", entries=entries, stop_reason="end_turn", usage=TokenUsage())


class _RecordingRouter:
    """Atrapa routera dyrektyw: notuje kontekst i zwraca stałą odpowiedź (albo ``None``)."""

    def __init__(self, reply: str | None = "odpowiedź dyrektywy") -> None:
        self._reply = reply
        self.contexts: list[Any] = []

    def dispatch(self, text: str, ctx: Any) -> str | None:
        self.contexts.append(ctx)
        return self._reply


def _service() -> ConversationService:
    return ConversationService(SqliteConversationStore(":memory:"), max_context_tokens=100_000)


def _responder(**kwargs: Any) -> tuple[ConversationalResponder, _FakeRuntime]:
    runtime = _FakeRuntime()
    return (
        ConversationalResponder(runtime, _service(), channel="teams_graph", **kwargs),  # type: ignore[arg-type]
        runtime,
    )


def _reply(responder: ConversationalResponder, message: InboundMessage) -> str:
    return asyncio.run(responder.respond(message))


# --- perymetr bramki odczytu (ADR 0062) ----------------------------------------


def test_brief_router_receives_the_sender_identity():
    """Regresja: ``BriefContext`` nie niósł nadawcy, choć responder go MIAŁ.

    Router briefu odpalał się przed jakąkolwiek autoryzacją, więc bramka odczytu nie miała
    czego sprawdzić — nie z powodu decyzji, tylko z powodu braku pola w kontekście.
    """
    brief = _RecordingRouter()
    responder, _ = _responder(project_brief=brief)

    _reply(
        responder,
        InboundMessage(
            text="@WorkMate ogarnij mnie na workmate",
            conversation_id="team/chan/root",
            sender_id="aad-123",
            mentions_bot=True,
        ),
    )

    assert [c.sender_id for c in brief.contexts] == ["aad-123"]


def test_change_digest_router_receives_the_sender_identity():
    digest = _RecordingRouter()
    responder, _ = _responder(change_digest=digest)

    _reply(
        responder,
        InboundMessage(
            text="@WorkMate co się zmieniło od 2026-07-01",
            conversation_id="team/chan/root",
            sender_id="aad-123",
            mentions_bot=True,
        ),
    )

    assert [c.sender_id for c in digest.contexts] == ["aad-123"]


# --- skaza rozmowy przed budową katalogów (ADR 0066 / 0064) --------------------


def _file_factory_spy(widziane: list[bool]):
    """Fabryka ``File`` notująca skazę WIDZIANĄ w chwili budowy katalogu.

    Rdzeń przyjmuje ``bool`` albo ``Callable[[], bool]`` (odczyt w chwili mutacji), więc sonda
    rozwija jedno i drugie — mierzymy STAN rozmowy, nie sposób jego przekazania.
    """

    def factory(_scope, _queue, _sender_id, _trust, tainted):
        widziane.append(tainted() if callable(tainted) else tainted)
        return []

    return factory


def test_attachment_taint_is_lit_before_the_tool_catalogs_are_built():
    """Regresja: skaza zapalała się PO budowie katalogów, więc ``File(edit)`` w turze
    z zatrutym załącznikiem widział „rozmowa czysta" — dokładnie w turze, w której sędzia
    mutacji (ADR 0065) najbardziej potrzebuje wiedzy o pochodzeniu.
    """
    widziane: list[bool] = []
    responder, _ = _responder(
        file_catalog_factory=_file_factory_spy(widziane), attachment_budget_bytes=1024
    )

    _reply(
        responder,
        InboundMessage(
            text="popraw notatkę",
            conversation_id="team/chan/root",
            sender_id="aad-123",
            attachments=(
                Attachment(kind="text", media_type="text/plain", name="a.txt", text="tresc"),
            ),
        ),
    )

    assert widziane == [True]


def test_guest_taint_is_also_visible_to_the_catalog_factory():
    """Sama klasa T2 (gość) skaża rozmowę i musi być widoczna przy budowie katalogu."""
    widziane: list[bool] = []
    responder, _ = _responder(
        file_catalog_factory=_file_factory_spy(widziane),
        attachment_budget_bytes=1024,
        sender_trust=lambda _sender: "T2",
    )

    _reply(
        responder,
        InboundMessage(text="cześć", conversation_id="team/chan/root", sender_id="aad-gosc"),
    )

    assert widziane == [True]


def test_guest_with_an_attachment_lights_both_sources_but_the_store_keeps_only_the_first():
    """Regresja ``elif``: gość Z załącznikiem nie zgłaszał w ogóle źródła „guest".

    Sonda ma DWA piętra, bo szew i skutek rozchodzą się tu na trwałe:

    * szew — obie przyczyny zostają zgłoszone (``elif`` gubił drugą). Szpieg na prywatnej
      metodzie jest tu jedyną drogą: druga przyczyna nie zostawia po sobie żadnego śladu, który
      dałoby się zobaczyć od zewnątrz;
    * skutek — ``mark_tainted`` jest ``UPDATE … WHERE tainted=0``, więc do rozmowy trafia
      WYŁĄCZNIE pierwsza przyczyna. Asercja na stanie przypina tę granicę wprost, żeby sonda nie
      obiecywała rozróżnienia, którego system nie dostarcza.

    DEFEKT PRODUKCYJNY (poza zakresem tej roli): komentarz przy dwóch ``if`` w ``responder`` mówi,
    że gość z załącznikiem ma „DWA źródła skazy i oba są faktem", a w audycie odróżnienie „treść
    przyszła plikiem" od „pisze ktoś spoza pionu" jest tym, czego się szuka. Tymczasem jedynym
    konsumentem jest kolumna ``taint_source`` z zapisem pierwszego zapłonu — „guest" ginie
    dokładnie w tej turze, w której miał znaczyć najwięcej. Rozróżnienie wymaga zapisu obu
    przyczyn (osobna kolumna albo zbiór), a to zmiana schematu, nie testu.
    """
    service = _service()
    responder = ConversationalResponder(
        _FakeRuntime(),  # type: ignore[arg-type]
        service,
        channel="teams_graph",
        sender_trust=lambda _sender: "T2",
    )
    zrodla: list[str] = []
    oryginal = responder._mark_taint

    def _spy(conversation_id: str, source: str) -> None:
        zrodla.append(source)
        oryginal(conversation_id, source)

    responder._mark_taint = _spy  # type: ignore[method-assign]

    _reply(
        responder,
        InboundMessage(
            text="patrz",
            conversation_id="team/chan/root",
            sender_id="aad-gosc",
            attachments=(Attachment(kind="text", media_type="text/plain", name="a.txt", text="x"),),
        ),
    )

    assert zrodla == ["attachment", "guest"]
    rozmowa = service.active_conversation("teams_graph", "team/chan/root")
    assert rozmowa is not None
    assert rozmowa.tainted is True
    assert rozmowa.taint_source == "attachment", (
        "magazyn zapisuje pierwszy zapłon; gdyby zaczął zapisywać oba, ta asercja ma zapytać "
        "o aktualizację kontraktu, a nie po cichu przestać cokolwiek znaczyć"
    )
