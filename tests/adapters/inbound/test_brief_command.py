"""Testy routera one-pagera „ogarnij mnie na <projekt>" (``BriefRouter``, F4, ADR 0051).

Bez sieci/LLM: atrapa ``ProjectBriefService`` zwraca stub z ``to_text``, a dostawę PDF podmieniamy
przechwytującym zamknięciem. Kluczowe niezmienniki: wyzwalacz TYLKO przy @wzmiance bota; projekt z
JAWNEGO argumentu (nie z treści wątku); ``| pdf`` dostarcza plikiem, a brak/awaria dostawy degraduje
do TEKSTU; nieznany projekt → czytelna odmowa; case-insensitive dyrektywa.
"""

from __future__ import annotations

from workmate.adapters.inbound.brief_command import BriefContext, BriefRouter

_EID = "team-1/chan-1/root-1"


class _FakeBrief:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_text(self) -> str:
        return self._text


class _FakeService:
    """Atrapa ``ProjectBriefService``: mapa projekt→tekst; brak klucza → ``None`` (nieznany)."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping
        self.asked: list[str] = []

    def brief(self, project: str):  # noqa: ANN201 - strukturalna atrapa portu
        self.asked.append(project)
        text = self._mapping.get(project)
        return _FakeBrief(text) if text is not None else None


def _router(mapping: dict[str, str], *, deliver=None) -> tuple[BriefRouter, _FakeService]:
    service = _FakeService(mapping)
    return BriefRouter(service, deliver_pdf=deliver), service  # type: ignore[arg-type]


def _ctx(*, mentions_bot: bool = True) -> BriefContext:
    return BriefContext(external_id=_EID, mentions_bot=mentions_bot)


def test_without_mention_returns_none():
    router, service = _router({"workmate": "BRIEF"})

    assert router.dispatch("ogarnij mnie na workmate", _ctx(mentions_bot=False)) is None
    assert service.asked == []  # bez wzmianki nawet nie pytamy serwisu


def test_mention_without_directive_returns_none():
    router, _ = _router({"workmate": "BRIEF"})

    assert router.dispatch("@WorkMate co u ciebie?", _ctx()) is None


def test_directive_with_project_returns_brief_text():
    router, service = _router({"workmate": "TREŚĆ ONE-PAGERA"})

    reply = router.dispatch("@WorkMate ogarnij mnie na workmate", _ctx())

    assert reply == "TREŚĆ ONE-PAGERA"
    assert service.asked == ["workmate"]


def test_directive_without_project_returns_usage():
    router, service = _router({"workmate": "BRIEF"})

    reply = router.dispatch("@WorkMate ogarnij mnie na", _ctx())

    assert reply is not None and "ogarnij mnie na <projekt>" in reply
    assert service.asked == []  # brak projektu → nie pytamy serwisu


def test_unknown_project_degrades_to_readable_refusal():
    router, _ = _router({"workmate": "BRIEF"})

    reply = router.dispatch("@WorkMate ogarnij mnie na widmo", _ctx())

    assert reply is not None and "Nie znam projektu 'widmo'" in reply


def test_project_comes_from_argument_not_thread_content():
    # Klucz to token PO frazie; dodatkowe słowa po nim (treść wątku) są ignorowane — brief nie
    # przekieruje odczytu na inny projekt (ADR 0009 §3).
    router, service = _router({"workmate": "BRIEF"})

    reply = router.dispatch(
        "@WorkMate ogarnij mnie na workmate a przy okazji scada-integration", _ctx()
    )

    assert reply == "BRIEF"
    assert service.asked == ["workmate"]


def test_directive_is_case_insensitive():
    router, service = _router({"workmate": "BRIEF"})

    assert router.dispatch("@WorkMate Ogarnij Mnie Na workmate", _ctx()) == "BRIEF"
    assert service.asked == ["workmate"]


def test_pdf_flag_delivers_file_and_confirms():
    calls: list[tuple[str, str, str]] = []
    router, _ = _router(
        {"workmate": "TREŚĆ"}, deliver=lambda ext, name, content: calls.append((ext, name, content))
    )

    reply = router.dispatch("@WorkMate ogarnij mnie na workmate | pdf", _ctx())

    assert calls == [(_EID, "brief-workmate.pdf", "TREŚĆ")]
    assert reply is not None and "PDF w tym wątku" in reply


def test_pdf_flag_without_delivery_degrades_to_text():
    router, _ = _router({"workmate": "TREŚĆ"}, deliver=None)

    reply = router.dispatch("@WorkMate ogarnij mnie na workmate | pdf", _ctx())

    assert reply is not None
    assert reply.startswith("TREŚĆ")  # treść dostarczona mimo braku PDF
    assert "PDF niedostępny" in reply


def test_pdf_delivery_failure_degrades_to_text():
    def _boom(ext: str, name: str, content: str) -> None:
        raise RuntimeError("Graph 500")

    router, _ = _router({"workmate": "TREŚĆ"}, deliver=_boom)

    reply = router.dispatch("@WorkMate ogarnij mnie na workmate | pdf", _ctx())

    assert reply is not None
    assert reply.startswith("TREŚĆ")
    assert "Nie udało się wysłać PDF" in reply
