"""Testy narzędzia odpowiedzi plikiem w wątku (build_file_reply_catalog, ADR 0026, A′2).

Sedno: cel dostawy (team/channel/root) jest PRE-ZWIĄZANY z fabryki, model podaje tylko treść,
format i nazwę; renderowanie → upload → odpowiedź z załącznikiem. Awarie przewidywalne (zły format,
pusta treść, za duży plik, usunięty root) degradują do ``{"error": ...}`` (model odpowie tekstem),
nie wywracają pollera. Testy na strukturalnych atrapach portów — bez httpx/Graph.
"""

from __future__ import annotations

import json

import pytest

from workmate.core.application.tools import build_file_reply_catalog
from workmate.core.errors import ThreadRootGone
from workmate.core.ports.document import RenderedDocument
from workmate.core.ports.file_output import UploadedFile

_GUID = "2318B4D5-1111-2222-3333-444455556666"


class _FakeRenderer:
    """Atrapa ``DocumentRenderer``: oddaje ustalone bajty i zapamiętuje wołania."""

    def __init__(self, content: bytes = b"RENDERED") -> None:
        self._content = content
        self.calls: list[tuple[str, str]] = []

    def render(self, body: str, fmt: str) -> RenderedDocument:
        self.calls.append((body, fmt))
        return RenderedDocument(self._content, f"application/{fmt}")


class _FakeSender:
    """Atrapa ``TeamsFileSender`` w pamięci — zapamiętuje upload i odpowiedź; opcjonalnie rzuca."""

    def __init__(
        self, *, upload_error: Exception | None = None, reply_error: Exception | None = None
    ) -> None:
        self.uploaded: list[tuple[str, str, str, bytes, str]] = []
        self.replies: list[tuple[str, str, str, str, UploadedFile]] = []
        self._upload_error = upload_error
        self._reply_error = reply_error

    def upload_channel_file(
        self, team_id: str, channel_id: str, filename: str, content: bytes, content_type: str
    ) -> UploadedFile:
        if self._upload_error is not None:
            raise self._upload_error
        self.uploaded.append((team_id, channel_id, filename, content, content_type))
        return UploadedFile(
            item_id=f"item-{filename}",
            name=filename,
            web_url=f"https://sp/{filename}",
            attachment_id=_GUID,
        )

    def post_reply_with_attachment(
        self, team_id: str, channel_id: str, root_id: str, html: str, attachment: UploadedFile
    ) -> None:
        if self._reply_error is not None:
            raise self._reply_error
        self.replies.append((team_id, channel_id, root_id, html, attachment))


def _tool(sender: _FakeSender, renderer: _FakeRenderer, *, max_bytes: int = 4096):
    catalog = build_file_reply_catalog(
        sender, renderer, "team-1", "chan-1", "root-9", max_bytes=max_bytes
    )
    assert [spec.name for spec in catalog] == ["reply_with_file"]
    return catalog[0].fn


def test_renders_uploads_then_replies_with_attachment():
    sender, renderer = _FakeSender(), _FakeRenderer(b"PDFBYTES")
    result = _tool(sender, renderer)(content="Treść raportu", file_format="pdf", filename="Raport")

    # Renderowanie dostaje treść i format od modelu.
    assert renderer.calls == [("Treść raportu", "pdf")]
    # Upload idzie na PRE-ZWIĄZANY team/channel z zrenderowanymi bajtami i typem MIME renderera.
    team_u, chan_u, name_u, bytes_u, ctype_u = sender.uploaded[0]
    assert team_u == "team-1" and chan_u == "chan-1"
    assert bytes_u == b"PDFBYTES" and ctype_u == "application/pdf"
    assert name_u.startswith("raport-") and name_u.endswith(".pdf")
    # Odpowiedź trafia do PRE-ZWIĄZANEGO roota z tym samym załącznikiem.
    team, chan, root, html, attachment = sender.replies[0]
    assert (team, chan, root) == ("team-1", "chan-1", "root-9")
    assert attachment.attachment_id == _GUID
    assert result == {"replied": True, "file": name_u, "format": "pdf"}


def test_filename_is_content_addressed_for_idempotency_and_integrity():
    """Ta sama treść → ta sama nazwa (idempotencja retry); różna treść → różna (bez nadpisania)."""
    same_a = _FakeSender()
    _tool(same_a, _FakeRenderer(b"IDENTYCZNE"))(content="x", file_format="md", filename="raport")
    same_b = _FakeSender()
    _tool(same_b, _FakeRenderer(b"IDENTYCZNE"))(content="y", file_format="md", filename="raport")
    diff = _FakeSender()
    _tool(diff, _FakeRenderer(b"INNE"))(content="x", file_format="md", filename="raport")

    # Ta sama treść → identyczna nazwa (idempotentny nadpis, bez duplikatu).
    assert same_a.uploaded[0][2] == same_b.uploaded[0][2]
    # Inna treść → inna nazwa (nie nadpisze cudzej odpowiedzi w kanale).
    assert diff.uploaded[0][2] != same_a.uploaded[0][2]


def test_reply_html_is_trusted_and_escapes_filename():
    sender, renderer = _FakeSender(), _FakeRenderer()
    # Nazwa slugowana do ASCII, ale sam podpis HTML i tak MUSI być escapowany (obrona w głąb).
    _tool(sender, renderer)(content="x", file_format="txt", filename="a<b>c")
    html = sender.replies[0][3]
    assert "<script" not in html and "&lt;" not in html  # slug wyciął znaki, podpis czysty
    assert sender.uploaded[0][2].startswith("a-b-c-") and sender.uploaded[0][2].endswith(".txt")


def test_model_cannot_choose_thread_target():
    """Cel wątku pochodzi z fabryki, nie od modelu — sygnatura narzędzia nie ma pól team/channel."""
    fn = _tool(_FakeSender(), _FakeRenderer())
    assert set(fn.__code__.co_varnames[: fn.__code__.co_argcount]) == {
        "content",
        "file_format",
        "filename",
    }


def test_polish_filename_is_ascii_slugged():
    sender = _FakeSender()
    _tool(sender, _FakeRenderer())(content="x", file_format="docx", filename="Raport Wydań 2026!")
    name = sender.uploaded[0][2]
    assert name.startswith("raport-wyda-2026-") and name.endswith(".docx")


@pytest.mark.parametrize("bad", ["rtf", "exe", "html", ""])
def test_unsupported_format_degrades_to_error(bad: str):
    sender = _FakeSender()
    out = _tool(sender, _FakeRenderer())(content="x", file_format=bad, filename="f")
    assert "error" in out and not sender.uploaded  # nic nie wysłano


def test_format_is_normalized_case_and_whitespace():
    """Model bywa niechlujny: ``" PDF "`` = ``pdf``. Normalizujemy zamiast odrzucać."""
    sender = _FakeSender()
    out = _tool(sender, _FakeRenderer())(content="x", file_format=" PDF ", filename="f")
    assert out["format"] == "pdf" and sender.uploaded[0][2].endswith(".pdf")


def test_empty_content_degrades_to_error():
    sender = _FakeSender()
    out = _tool(sender, _FakeRenderer())(content="   ", file_format="md", filename="f")
    assert "error" in out and not sender.uploaded


def test_oversize_rendered_file_degrades_to_error():
    sender, renderer = _FakeSender(), _FakeRenderer(b"x" * 5000)
    out = _tool(sender, renderer, max_bytes=1024)(content="duzo", file_format="pdf", filename="f")
    assert "error" in out and "limit" in out["error"]
    assert not sender.uploaded  # guard PRZED uploadem


def test_thread_root_gone_degrades_to_error():
    """Usunięty root wątku → ThreadRootGone → {"error"} (model odpowie tekstem), nie wyjątek."""
    sender = _FakeSender(reply_error=ThreadRootGone("root znikł"))
    out = _tool(sender, _FakeRenderer())(content="x", file_format="txt", filename="f")
    assert "error" in out


def test_result_is_json_serializable():
    sender = _FakeSender()
    out = _tool(sender, _FakeRenderer())(content="x", file_format="md", filename="f")
    json.dumps(out)  # runtime serializuje wynik narzędzia — nie może rzucić
