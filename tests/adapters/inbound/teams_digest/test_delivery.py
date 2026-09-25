"""Testy orkiestracji przebiegu digestu (``deliver_weekly_digest``, F6, ADR 0053).

Bez sieci/zegara/asyncio: atrapa ``ChangeDigestService`` zwraca ustalony digest, a callbacki
``send``/``mark`` przechwytują. Niezmienniki: wysyłka do wszystkich NIEobsłużonych + oznaczenie;
odbiorca z ``already`` pomijany; pusty tydzień bez wysyłki (ale oznaczony); awaria jednej wysyłki
nie kładzie przebiegu (odbiorca w ``failed``, NIEoznaczony → ponowienie); nagłówek doklejony.
"""

from __future__ import annotations

from datetime import date

from sufler.adapters.inbound.teams_digest.delivery import deliver_weekly_digest

_SINCE = date(2026, 7, 20)
_LABEL = "2026-W30"


class _FakeDigest:
    def __init__(self, total: int, text: str = "TREŚĆ DIGESTU") -> None:
        self.total = total
        self._text = text

    def to_text(self) -> str:
        return self._text


class _FakeService:
    """Atrapa ``ChangeDigestService``: zapamiętuje datę i zwraca ustalony digest."""

    def __init__(self, digest: _FakeDigest) -> None:
        self._digest = digest
        self.since_arg: date | None = None

    def since(self, day: date):  # noqa: ANN201 - strukturalna atrapa
        self.since_arg = day
        return self._digest


def _run(service, *, recipients, already=None, failing=()):
    sent: list[tuple[str, str]] = []
    marked: list[str] = []

    def send(recipient: str, text: str) -> None:
        if recipient in failing:
            raise RuntimeError("Graph 500")
        sent.append((recipient, text))

    report = deliver_weekly_digest(
        service,  # type: ignore[arg-type]
        since=_SINCE,
        week_label=_LABEL,
        recipients=recipients,
        already=set(already or ()),
        send=send,
        mark=marked.append,
    )
    return report, sent, marked


def test_sends_to_all_recipients_and_marks_them():
    service = _FakeService(_FakeDigest(total=5))

    report, sent, marked = _run(service, recipients=["u1", "u2"])

    assert [r for r, _ in sent] == ["u1", "u2"]
    assert marked == ["u1", "u2"]
    assert report.sent == ("u1", "u2")
    assert report.total_events == 5
    assert service.since_arg == _SINCE  # okno liczone od podanej daty


def test_header_is_prepended_to_digest_text():
    service = _FakeService(_FakeDigest(total=1, text="RDZEŃ"))

    _, sent, _ = _run(service, recipients=["u1"])

    _, text = sent[0]
    assert text.endswith("RDZEŃ")
    assert text.split("\n")[0].startswith("Cześć")  # nagłówek nasz, nie z treści zdarzeń


def test_already_delivered_recipient_is_skipped():
    service = _FakeService(_FakeDigest(total=3))

    report, sent, marked = _run(service, recipients=["u1", "u2"], already=["u1"])

    assert [r for r, _ in sent] == ["u2"]  # u1 pominięty
    assert marked == ["u2"]
    assert "u1" in report.already


def test_empty_week_is_not_sent_but_marked():
    service = _FakeService(_FakeDigest(total=0))

    report, sent, marked = _run(service, recipients=["u1", "u2"])

    assert sent == []  # brak zmian → brak DM (bez spamu)
    assert report.skipped_empty is True
    assert marked == ["u1", "u2"]  # ale tydzień oznaczony (spójna detekcja nadrabiania)


def test_duplicate_recipient_is_sent_only_once():
    # Powtórzony AAD id na liście (błąd env) NIE może dać dwóch DM-ów tej samej osobie.
    service = _FakeService(_FakeDigest(total=2))

    report, sent, marked = _run(service, recipients=["u1", "u1", "u2"])

    assert [r for r, _ in sent] == ["u1", "u2"]
    assert marked == ["u1", "u2"]
    assert report.sent == ("u1", "u2")


def test_failed_send_is_not_marked_and_reported():
    service = _FakeService(_FakeDigest(total=4))

    report, sent, marked = _run(service, recipients=["u1", "u2", "u3"], failing={"u2"})

    assert [r for r, _ in sent] == ["u1", "u3"]  # u2 padł, reszta poszła
    assert marked == ["u1", "u3"]  # u2 NIEoznaczony → ponowienie w kolejnym przebiegu
    assert report.failed == ("u2",)
    assert report.sent == ("u1", "u3")
