"""Testy routera digestu „co się zmieniło od <data>" (``ChangeDigestRouter``, F5, ADR 0052).

Bez sieci: atrapa ``ChangeDigestService`` zwraca stub z ``to_text``, dostawę PDF podmieniamy
przechwytującym zamknięciem. Niezmienniki: wyzwalacz TYLKO przy @wzmiance bota; data z JAWNEGO
argumentu (ISO), zła/brak → podpowiedź; ``| pdf`` dostarcza plikiem, brak/awaria → degradacja do
tekstu; case-insensitive dyrektywa.
"""

from __future__ import annotations

from datetime import date

from workmate.adapters.inbound.change_command import ChangeDigestContext, ChangeDigestRouter

_EID = "team-1/chan-1/root-1"


class _FakeDigest:
    def __init__(self, text: str) -> None:
        self._text = text

    def to_text(self) -> str:
        return self._text


class _FakeService:
    """Atrapa ``ChangeDigestService``: zapamiętuje pytaną datę, zwraca stały stub."""

    def __init__(self, text: str = "DIGEST") -> None:
        self._text = text
        self.asked: list[date] = []

    def since(self, day: date):  # noqa: ANN201 - strukturalna atrapa
        self.asked.append(day)
        return _FakeDigest(self._text)


def _router(*, deliver=None, text: str = "DIGEST") -> tuple[ChangeDigestRouter, _FakeService]:
    service = _FakeService(text)
    return ChangeDigestRouter(service, deliver_pdf=deliver), service  # type: ignore[arg-type]


def _ctx(*, mentions_bot: bool = True) -> ChangeDigestContext:
    return ChangeDigestContext(external_id=_EID, mentions_bot=mentions_bot)


def test_without_mention_returns_none():
    router, service = _router()

    assert router.dispatch("co się zmieniło od 2026-07-01", _ctx(mentions_bot=False)) is None
    assert service.asked == []


def test_mention_without_directive_returns_none():
    router, _ = _router()

    assert router.dispatch("@WorkMate jak leci?", _ctx()) is None


def test_directive_with_iso_date_returns_digest_text():
    router, service = _router(text="TREŚĆ DIGESTU")

    reply = router.dispatch("@WorkMate co się zmieniło od 2026-07-01", _ctx())

    assert reply == "TREŚĆ DIGESTU"
    assert service.asked == [date(2026, 7, 1)]


def test_directive_without_date_returns_usage():
    router, service = _router()

    reply = router.dispatch("@WorkMate co się zmieniło od", _ctx())

    assert reply is not None and "RRRR-MM-DD" in reply
    assert service.asked == []


def test_directive_with_bad_date_returns_usage():
    router, service = _router()

    reply = router.dispatch("@WorkMate co się zmieniło od wczoraj", _ctx())

    assert reply is not None and "RRRR-MM-DD" in reply
    assert service.asked == []  # zła data → nie liczymy digestu


def test_directive_is_case_insensitive():
    router, service = _router()

    assert router.dispatch("@WorkMate Co Się Zmieniło Od 2026-07-01", _ctx()) == "DIGEST"
    assert service.asked == [date(2026, 7, 1)]


def test_pdf_flag_delivers_file_and_confirms():
    calls: list[tuple[str, str, str]] = []
    router, _ = _router(
        deliver=lambda ext, name, content: calls.append((ext, name, content)), text="TREŚĆ"
    )

    reply = router.dispatch("@WorkMate co się zmieniło od 2026-07-01 | pdf", _ctx())

    # BAZA nazwy bez rozszerzenia — pipeline file-reply dokłada ``.pdf``.
    assert calls == [(_EID, "zmiany-od-2026-07-01", "TREŚĆ")]
    assert reply is not None and "PDF w tym wątku" in reply


def test_pdf_flag_without_delivery_degrades_to_text():
    router, _ = _router(deliver=None, text="TREŚĆ")

    reply = router.dispatch("@WorkMate co się zmieniło od 2026-07-01 | pdf", _ctx())

    assert reply is not None
    assert reply.startswith("TREŚĆ")
    assert "PDF niedostępny" in reply


def test_pdf_delivery_failure_degrades_to_text():
    def _boom(ext: str, name: str, content: str) -> None:
        raise RuntimeError("Graph 500")

    router, _ = _router(deliver=_boom, text="TREŚĆ")

    reply = router.dispatch("@WorkMate co się zmieniło od 2026-07-01 | pdf", _ctx())

    assert reply is not None
    assert reply.startswith("TREŚĆ")
    assert "Nie udało się wysłać PDF" in reply
