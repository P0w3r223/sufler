"""Testy narzędzia wysyłki DOKUMENTU 1:1 do rozmówcy (build_user_doc_push_catalog, ADR 0027, plik).

Sedno: odbiorca (``target_user_id``) jest PRE-ZWIĄZANY z fabryki (nadawca bieżącej wiadomości), NIE
od modelu — model podaje jedynie treść, format i bazę nazwy. Poprawne wejście → renderowanie treści
przez ``DocumentRenderer`` i wywołanie sendera z pre-związanym celem, unikalną-po-treści nazwą i
bajtami. Awarie przewidywalne (zły format, pusta lub za duża treść) degradują do ``{"error": ...}``
(model odpowie tekstem), nie wywracają pollera. Testy na STRUKTURALNYCH atrapach — bez httpx/Graph.
"""

from __future__ import annotations

import json

import pytest

from workmate.core.application.tools import build_user_doc_push_catalog
from workmate.core.ports.document import FILE_REPLY_FORMATS, RenderedDocument
from workmate.core.ports.user_doc_push import UserDocSender

_TARGET = "u-anna-aad-id"


class _FakeRenderer:
    """Atrapa ``DocumentRenderer`` — bajty = UTF-8 treści, typ MIME z jednoźródłowej mapy."""

    def render(self, body: str, fmt: str) -> RenderedDocument:
        return RenderedDocument(content=body.encode("utf-8"), content_type=FILE_REPLY_FORMATS[fmt])


class _FakeSender:
    """Atrapa ``UserDocSender`` w pamięci — zapamiętuje wołania; opcjonalnie rzuca."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, str, bytes, str]] = []
        self._error = error

    def send_document_to_user(
        self, target_user_id: str, filename: str, content: bytes, content_type: str
    ) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append((target_user_id, filename, content, content_type))


def _tool(sender: _FakeSender, *, max_bytes: int = 4096, target: str = _TARGET):
    catalog = build_user_doc_push_catalog(sender, _FakeRenderer(), target, max_bytes=max_bytes)
    assert [spec.name for spec in catalog] == ["send_document_to_user"]
    return catalog[0].fn


def test_fake_satisfies_the_port():
    """Atrapa MUSI pasować strukturalnie do portu (mypy) — dowód testowalności bez Graph."""
    sender: UserDocSender = _FakeSender()
    sender.send_document_to_user("u1", "f.md", b"bytes", "text/markdown; charset=utf-8")


def test_valid_document_is_rendered_and_sent_to_prebound_target():
    sender = _FakeSender()
    out = _tool(sender)(content="# Raport\ntreść", file_format="md", filename="raport")

    target, name, content, ctype = sender.sent[0]
    # Sender dostaje PRE-ZWIĄZANY cel (nie od modelu) i zrenderowane bajty treści.
    assert target == _TARGET
    assert content == "# Raport\ntreść".encode()
    assert ctype == FILE_REPLY_FORMATS["md"]
    # Nazwa jest UNIKALNA-PO-TREŚCI: slug bazy + skrót treści + rozszerzenie formatu.
    assert name.startswith("raport-") and name.endswith(".md")
    assert out == {"sent": True, "file": name, "format": "md"}


@pytest.mark.parametrize("fmt", ["md", "txt", "pdf", "docx"])
def test_each_supported_format_passes_through(fmt: str):
    sender = _FakeSender()
    out = _tool(sender)(content="treść", file_format=fmt, filename="dok")

    assert out["sent"] is True and out["format"] == fmt
    assert sender.sent[0][3] == FILE_REPLY_FORMATS[fmt]
    assert sender.sent[0][1].endswith(f".{fmt}")


def test_format_is_normalized_case_and_whitespace():
    """Model bywa niechlujny: ``" PDF "`` = ``pdf``. Normalizujemy zamiast odrzucać."""
    sender = _FakeSender()
    out = _tool(sender)(content="x", file_format=" PDF ")

    assert out["sent"] is True and out["format"] == "pdf"


@pytest.mark.parametrize("bad", ["xlsx", "html", "exe", ""])
def test_unsupported_format_degrades_to_error(bad: str):
    """Wąska allowlista: format spoza mapy → koperta błędu, sender NIE wołany."""
    sender = _FakeSender()
    out = _tool(sender)(content="x", file_format=bad)

    assert "error" in out and not sender.sent


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_empty_content_degrades_to_error(blank: str):
    """Pusta/białoznakowa treść → koperta błędu (nie ma czego renderować), sender NIE wołany."""
    sender = _FakeSender()
    out = _tool(sender)(content=blank, file_format="md")

    assert "error" in out and not sender.sent


def test_oversize_document_degrades_to_error_before_send():
    """Zrenderowany plik ponad ``max_bytes`` → koperta z limitem; guard PRZED wysyłką."""
    sender = _FakeSender()
    out = _tool(sender, max_bytes=8)(content="x" * 64, file_format="txt")

    assert "error" in out and "limit" in out["error"]
    assert not sender.sent


def test_document_at_the_size_limit_is_sent():
    """Granica jest inkluzywna: plik DOKŁADNIE na limicie przechodzi (odcinamy tylko powyżej)."""
    sender = _FakeSender()
    out = _tool(sender, max_bytes=8)(content="12345678", file_format="txt")

    assert out["sent"] is True
    assert len(sender.sent) == 1


def test_hard_sender_failure_propagates_not_enveloped():
    """Twarda awaria Graph (nie WorkMateError/ValidationError) WYPŁYWA wyżej — świadomie.

    Koperta łapie tylko przewidywalne błędy dziedziny; twardą awarię infrastruktury ma zobaczyć
    ``SafeResponder`` (zaloguje i zdegraduje), nie połknąć narzędzie (jak obraz/plik w wątku)."""
    sender = _FakeSender(error=RuntimeError("Graph 500"))
    with pytest.raises(RuntimeError, match="Graph 500"):
        _tool(sender)(content="x", file_format="md")


def test_same_content_gives_same_name_different_content_differs():
    """Nazwa adresowana treścią: ta sama treść → ta sama nazwa (idempotencja), różna → różna."""
    sender = _FakeSender()
    tool = _tool(sender)
    tool(content="alpha", file_format="md", filename="dok")
    tool(content="alpha", file_format="md", filename="dok")
    tool(content="beta", file_format="md", filename="dok")

    names = [row[1] for row in sender.sent]
    assert names[0] == names[1]  # ta sama treść → ta sama nazwa
    assert names[2] != names[0]  # różna treść → różna nazwa


def test_model_cannot_choose_recipient():
    """Cel wiąże fabryka, nie model — sygnatura narzędzia nie ma pola odbiorcy/target_user_id."""
    fn = _tool(_FakeSender())
    assert set(fn.__code__.co_varnames[: fn.__code__.co_argcount]) == {
        "content",
        "file_format",
        "filename",
    }


def test_result_is_json_serializable():
    sender = _FakeSender()
    out = _tool(sender)(content="x", file_format="txt")

    json.dumps(out)  # runtime serializuje wynik narzędzia — nie może rzucić
