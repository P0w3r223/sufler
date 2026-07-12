"""Testy materializacji załączników Teams (ADR 0016) — referencja → bajty → ``Attachment``.

I/O portu ``GraphChannelClient`` jest wstrzykiwane, więc pełną materializację testujemy
ATRAPĄ portu — bez ``httpx``, bez MSAL, bez sieci. Sedno: sniff obrazu inline po magicznych
bajtach, mapowanie rozszerzeń plików (pdf/obrazy/docx), ekstrakcja tekstu z .docx oraz
łagodna degradacja (limit rozmiaru/liczby, nieobsługiwany typ, błąd pobrania → NOTKA, a nie
wyjątek na zewnątrz).
"""
from __future__ import annotations

import asyncio
import io
from typing import Any

from docx import Document

from workmate.adapters.inbound.teams_graph.attachments import (
    AttachmentLimits,
    AttachmentMaterializer,
)
from workmate.adapters.inbound.teams_graph.selection import AttachmentRef, ChannelMessage

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16

_TEAM = "team-1"
_CHAN = "chan-1"


class _FakeGraphClient:
    """Atrapa portu ``GraphChannelClient`` — oddaje zaskryptowane bajty per referencja.

    ``hosted`` mapuje ``hosted_id → bajty``; ``files`` mapuje ``url → bajty``. Podanie
    ``Exception`` zamiast bajtów sprawia, że pobranie rzuca (test degradacji).
    """

    def __init__(
        self,
        *,
        hosted: dict[str, bytes | Exception] | None = None,
        files: dict[str, bytes | Exception] | None = None,
    ) -> None:
        self._hosted = hosted or {}
        self._files = files or {}
        self.hosted_calls: list[tuple[str, str, str, str]] = []
        self.file_calls: list[str] = []

    async def get_hosted_content(
        self, team_id: str, channel_id: str, message_id: str, hosted_id: str
    ) -> bytes:
        self.hosted_calls.append((team_id, channel_id, message_id, hosted_id))
        return _resolve(self._hosted[hosted_id])

    async def download_shared_url(self, url: str) -> bytes:
        self.file_calls.append(url)
        return _resolve(self._files[url])


def _resolve(value: bytes | Exception) -> bytes:
    if isinstance(value, Exception):
        raise value
    return value


def _msg(refs: tuple[AttachmentRef, ...]) -> ChannelMessage:
    return ChannelMessage(
        id="m-1",
        thread_root_id="root-1",
        created="2024-01-01T10:00:00Z",
        sender_id="u-anna",
        sender_name="Anna",
        text="opis",
        attachment_refs=refs,
    )


def _materialize(
    client: Any,
    refs: tuple[AttachmentRef, ...],
    *,
    max_bytes: int = 1_000_000,
    max_count: int = 5,
    max_total: int = 1_000_000,
) -> tuple:
    materializer = AttachmentMaterializer(
        client,
        limits=AttachmentLimits(
            max_bytes=max_bytes, max_count=max_count, max_total_bytes=max_total
        ),
    )
    return asyncio.run(materializer.materialize(_TEAM, _CHAN, _msg(refs)))


def _docx_bytes(*, paragraph: str, table_cells: list[list[str]]) -> bytes:
    """Zbuduj mały .docx w pamięci: jeden akapit + tabela — do testu ekstrakcji tekstu."""
    doc = Document()
    doc.add_paragraph(paragraph)
    table = doc.add_table(rows=len(table_cells), cols=len(table_cells[0]))
    for r, row in enumerate(table_cells):
        for c, value in enumerate(row):
            table.cell(r, c).text = value
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


# --- hosted (obrazy inline): sniff po magicznych bajtach ---------------------


def test_hosted_png_sniffed_as_image_png():
    client = _FakeGraphClient(hosted={"h1": _PNG})

    (att,) = _materialize(client, (AttachmentRef(kind="hosted", name="obraz", hosted_id="h1"),))

    assert att.kind == "image"
    assert att.media_type == "image/png"
    assert att.data_base64  # bajty zakodowane base64
    assert client.hosted_calls == [(_TEAM, _CHAN, "m-1", "h1")]


def test_hosted_jpeg_sniffed_as_image_jpeg():
    client = _FakeGraphClient(hosted={"h1": _JPEG})

    (att,) = _materialize(client, (AttachmentRef(kind="hosted", name="obraz", hosted_id="h1"),))

    assert (att.kind, att.media_type) == ("image", "image/jpeg")


def test_hosted_unrecognized_magic_bytes_yields_note():
    """Bajty bez znanego magic (nie obraz) → notka, nie wyjątek."""
    client = _FakeGraphClient(hosted={"h1": b"not-an-image-blob"})

    (att,) = _materialize(client, (AttachmentRef(kind="hosted", name="obraz", hosted_id="h1"),))

    assert att.kind == "text"
    assert "nieobsługiwany typ" in att.text


# --- pliki: mapowanie po rozszerzeniu ----------------------------------------


def test_file_pdf_becomes_document():
    client = _FakeGraphClient(files={"u://umowa": b"%PDF-1.7 ..."})
    ref = AttachmentRef(kind="file", name="umowa.pdf", url="u://umowa")

    (att,) = _materialize(client, (ref,))

    assert att.kind == "document"
    assert att.media_type == "application/pdf"
    assert att.name == "umowa.pdf"
    assert att.data_base64


def test_file_png_extension_becomes_image_even_without_sniff():
    client = _FakeGraphClient(files={"u://foto": b"whatever-bytes"})
    # rozszerzenie rozpoznawane case-insensitive (.PNG == .png)
    ref = AttachmentRef(kind="file", name="foto.PNG", url="u://foto")

    (att,) = _materialize(client, (ref,))

    assert (att.kind, att.media_type) == ("image", "image/png")


def test_file_jpg_and_jpeg_map_to_image_jpeg():
    client = _FakeGraphClient(files={"u://a": b"x", "u://b": b"y"})
    refs = (
        AttachmentRef(kind="file", name="a.jpg", url="u://a"),
        AttachmentRef(kind="file", name="b.jpeg", url="u://b"),
    )

    a, b = _materialize(client, refs)

    assert a.media_type == "image/jpeg"
    assert b.media_type == "image/jpeg"


def test_file_docx_extracts_paragraphs_and_table_text():
    """.docx → kind=text z tekstem: akapit ORAZ komórki tabeli (Claude nie przyjmuje docx)."""
    data = _docx_bytes(
        paragraph="Ustalenia ze spotkania",
        table_cells=[["Zadanie", "Termin"], ["Wdrożenie SCADA", "2024-03"]],
    )
    client = _FakeGraphClient(files={"u://doc": data})
    ref = AttachmentRef(kind="file", name="notatka.docx", url="u://doc")

    (att,) = _materialize(client, (ref,))

    assert att.kind == "text"
    assert att.media_type == "text/plain"
    assert att.data_base64 == ""  # docx idzie jako tekst, nie base64
    assert "Ustalenia ze spotkania" in att.text
    # Sedno: tekst TABELI też jest wyekstrahowany (nie tylko akapity).
    assert "Wdrożenie SCADA" in att.text
    assert "Termin" in att.text


def test_file_unsupported_extension_yields_note():
    client = _FakeGraphClient(files={"u://z": b"zip-bytes"})
    ref = AttachmentRef(kind="file", name="archiwum.zip", url="u://z")

    (att,) = _materialize(client, (ref,))

    assert att.kind == "text"
    assert "nieobsługiwany typ" in att.text
    assert "archiwum.zip" in att.text


# --- łagodna degradacja: limity i błędy --------------------------------------


def test_oversized_attachment_yields_note_not_bytes():
    """Plik ponad limit rozmiaru → notka (nie wysyłamy megabajtów do API)."""
    client = _FakeGraphClient(files={"u://big": b"x" * 100})
    ref = AttachmentRef(kind="file", name="wielki.pdf", url="u://big")

    (att,) = _materialize(client, (ref,), max_bytes=10)

    assert att.kind == "text"
    assert "przekracza limit rozmiaru" in att.text
    assert "wielki.pdf" in att.text


def test_over_count_drops_extras_with_note_and_still_fetches_kept():
    """Ponad ``max_count`` → notka o pominiętych + POBRANE tylko dozwolone (reszta odcięta)."""
    client = _FakeGraphClient(files={"u://1": b"%PDF a", "u://2": b"%PDF b"})
    refs = (
        AttachmentRef(kind="file", name="a.pdf", url="u://1"),
        AttachmentRef(kind="file", name="b.pdf", url="u://2"),
        AttachmentRef(kind="file", name="c.pdf", url="u://3"),  # ta już nie pobrana
    )

    result = _materialize(client, refs, max_count=2)

    # Pierwszy element to notka o pominięciu, potem 2 pobrane dokumenty.
    assert result[0].kind == "text"
    assert "Pominięto 1" in result[0].text
    assert [a.kind for a in result[1:]] == ["document", "document"]
    # Trzeci URL nie był w ogóle pobierany (limit odciął przed I/O).
    assert client.file_calls == ["u://1", "u://2"]


def test_download_exception_yields_note_not_raised():
    """Wyjątek pobrania jednego załącznika NIE kładzie pollera — degraduje do notki."""
    client = _FakeGraphClient(files={"u://boom": RuntimeError("Graph 500")})
    ref = AttachmentRef(kind="file", name="feler.pdf", url="u://boom")

    (att,) = _materialize(client, (ref,))

    assert att.kind == "text"
    assert "Nie udało się przetworzyć" in att.text
    assert "feler.pdf" in att.text


def test_corrupt_docx_yields_note_not_raised():
    """REGRESJA: uszkodzony/podszyty .docx (ekstrakcja rzuca) degraduje do notki —

    inaczej wyjątek propagowałby przez pollera i zapętlał kanał (build był poza try).
    """
    client = _FakeGraphClient(files={"u://bad": b"not a valid zip/docx package"})
    ref = AttachmentRef(kind="file", name="uszkodzony.docx", url="u://bad")

    (att,) = _materialize(client, (ref,))

    assert att.kind == "text"
    assert "Nie udało się przetworzyć" in att.text
    assert "uszkodzony.docx" in att.text


def test_aggregate_budget_drops_overflow_with_note():
    """Pliki mieszczące się pojedynczo, ale przekraczające ŁĄCZNY budżet → nadmiar to notka."""
    client = _FakeGraphClient(files={"u://1": b"%PDF" + b"a" * 96, "u://2": b"%PDF" + b"b" * 96})
    refs = (
        AttachmentRef(kind="file", name="a.pdf", url="u://1"),  # 100 B — mieści się
        AttachmentRef(kind="file", name="b.pdf", url="u://2"),  # 100 B — już nie (budżet 150)
    )

    first, second = _materialize(client, refs, max_bytes=1000, max_total=150)

    assert first.kind == "document"
    assert second.kind == "text"
    assert "przekroczony łączny limit" in second.text
    assert "b.pdf" in second.text


def test_mixed_refs_materialize_independently():
    """Jedna zła referencja (błąd) nie psuje dobrych — każda materializowana osobno."""
    client = _FakeGraphClient(
        hosted={"h1": _PNG},
        files={"u://good": b"%PDF ok", "u://bad": RuntimeError("padło")},
    )
    refs = (
        AttachmentRef(kind="hosted", name="obraz", hosted_id="h1"),
        AttachmentRef(kind="file", name="dobry.pdf", url="u://good"),
        AttachmentRef(kind="file", name="zly.pdf", url="u://bad"),
    )

    img, good, bad = _materialize(client, refs)

    assert img.kind == "image"
    assert good.kind == "document"
    assert bad.kind == "text" and "Nie udało się przetworzyć" in bad.text
