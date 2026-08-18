"""Testy narzędzia wysyłki OBRAZU 1:1 do rozmówcy (build_user_image_push_catalog, ADR 0027, A′3).

Sedno: odbiorca (``target_user_id``) jest PRE-ZWIĄZANY z fabryki (nadawca bieżącej wiadomości), NIE
od modelu — model podaje jedynie bajty obrazu (base64) i format. Poprawne base64+format → wywołanie
sendera z pre-związanym celem i poprawnym typem MIME. Awarie przewidywalne (zły format, złe base64,
pusty lub za duży obraz) degradują do ``{"error": ...}`` (model odpowie tekstem), nie wywracają
pollera. Testy na STRUKTURALNEJ atrapie portu w pamięci — bez httpx/Graph.
"""

from __future__ import annotations

import base64
import inspect
import json

import pytest

from workmate.core.application.tools import build_user_image_push_catalog
from workmate.core.ports.user_push import IMAGE_CONTENT_TYPES, sniff_image_format

_TARGET = "u-anna-aad-id"

# Magic bytes (sygnatury) formatów z allowlisty — narzędzie WERYFIKUJE, że bajty zgadzają się z
# deklarowanym formatem (boundary validation), więc happy-path MUSI używać realnych sygnatur.
_PNG_SIG = b"\x89PNG\r\n\x1a\n"  # dokładnie 8 B — użyteczne przy teście granicy rozmiaru
_JPG_SIG = b"\xff\xd8\xff"
_GIF_SIG = b"GIF89a"
# Poprawne bajty per deklarowany format modelu (jpeg współdzieli sygnaturę z jpg — kanon 'jpg').
_VALID_BYTES: dict[str, bytes] = {
    "png": _PNG_SIG + b"pixels",
    "jpg": _JPG_SIG + b"pixels",
    "jpeg": _JPG_SIG + b"pixels",
    "gif": _GIF_SIG + b"pixels",
}


class _FakeSender:
    """Atrapa ``UserImageSender`` w pamięci — zapamiętuje wołania; opcjonalnie rzuca."""

    def __init__(self, *, error: Exception | None = None) -> None:
        self.sent: list[tuple[str, bytes, str]] = []
        self._error = error

    def send_image_to_user(self, target_user_id: str, content: bytes, content_type: str) -> None:
        if self._error is not None:
            raise self._error
        self.sent.append((target_user_id, content, content_type))


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _tool(sender: _FakeSender, *, max_bytes: int = 4096, target: str = _TARGET):
    catalog = build_user_image_push_catalog(sender, target, max_bytes=max_bytes)
    assert [spec.name for spec in catalog] == ["SendImage"]
    return catalog[0].fn


def test_valid_image_is_sent_to_prebound_target():
    sender = _FakeSender()
    png = _VALID_BYTES["png"]
    result = _tool(sender)(image_base64=_b64(png), image_format="png")

    # Sender dostaje PRE-ZWIĄZANY cel (nie od modelu), zdekodowane bajty i typ MIME z mapy.
    target, content, ctype = sender.sent[0]
    assert target == _TARGET
    assert content == png
    assert ctype == "image/png"
    # Wynik niesie potwierdzenie + metadane (rozmiar surowych bajtów, nie base64).
    assert result == {"sent": True, "format": "png", "bytes": len(png)}


@pytest.mark.parametrize(
    ("fmt", "expected_ctype"),
    [
        ("png", "image/png"),
        ("jpg", "image/jpeg"),
        ("jpeg", "image/jpeg"),
        ("gif", "image/gif"),
    ],
)
def test_each_supported_format_maps_to_its_content_type(fmt: str, expected_ctype: str):
    """Każdy format → właściwy MIME z ``IMAGE_CONTENT_TYPES`` (bajty z sygnaturą formatu)."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(_VALID_BYTES[fmt]), image_format=fmt)

    assert out["sent"] is True
    assert sender.sent[0][2] == expected_ctype == IMAGE_CONTENT_TYPES[fmt]


def test_format_is_normalized_case_and_whitespace():
    """Model bywa niechlujny: ``" PNG "`` = ``png``. Normalizujemy zamiast odrzucać."""
    sender = _FakeSender()
    png = _VALID_BYTES["png"]
    out = _tool(sender)(image_base64=_b64(png), image_format=" PNG ")

    assert out == {"sent": True, "format": "png", "bytes": len(png)}
    assert sender.sent[0][2] == "image/png"


@pytest.mark.parametrize("bad", ["svg", "bmp", "webp", "exe", ""])
def test_unsupported_format_degrades_to_error(bad: str):
    """Wąska allowlista: format spoza mapy → koperta błędu, sender NIE wołany."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(b"DATA"), image_format=bad)

    assert "error" in out and not sender.sent


def test_bad_base64_degrades_to_error():
    """Niepoprawne base64 → koperta błędu (degradacja do tekstu), sender NIE wołany."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64="!!! to nie base64 !!!", image_format="png")

    assert "error" in out and not sender.sent


def test_empty_image_degrades_to_error():
    """Pusty obraz (base64 pustych bajtów) → koperta błędu, sender NIE wołany."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(b""), image_format="png")

    assert "error" in out and not sender.sent


def test_oversize_image_degrades_to_error_before_send():
    """Obraz ponad ``max_bytes`` → koperta z limitem; guard PRZED wysyłką (sender NIE wołany)."""
    sender = _FakeSender()
    out = _tool(sender, max_bytes=8)(image_base64=_b64(b"x" * 64), image_format="png")

    assert "error" in out and "limit" in out["error"]
    assert not sender.sent


def test_image_at_the_size_limit_is_sent():
    """Granica jest inkluzywna: obraz DOKŁADNIE na limicie przechodzi (odcinamy tylko powyżej).

    Sygnatura PNG ma dokładnie 8 B, więc sama w sobie wypełnia ``max_bytes=8`` i przechodzi też
    weryfikację magic bytes — jeden dobór bajtów pokrywa oba niezmienniki (limit + sygnatura).
    """
    sender = _FakeSender()
    assert len(_PNG_SIG) == 8
    out = _tool(sender, max_bytes=8)(image_base64=_b64(_PNG_SIG), image_format="png")

    assert out == {"sent": True, "format": "png", "bytes": 8}
    assert len(sender.sent) == 1


def test_declared_png_but_bytes_have_no_signature_degrades_to_error():
    """Boundary validation: ``png`` + bajty bez sygnatury → koperta błędu, sender NIE wołany."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(b"to nie jest zaden obraz"), image_format="png")

    assert "error" in out and not sender.sent


def test_declared_png_but_bytes_are_jpeg_degrades_to_error():
    """Deklaracja ``png``, ale bajty niosą sygnaturę JPEG → niezgodność → błąd, bez wysyłki."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(_JPG_SIG + b"pixels"), image_format="png")

    assert "error" in out and not sender.sent


def test_signature_mismatch_error_names_the_declared_format():
    """Komunikat wskazuje deklarowany format — model wie, że to bajty nie pasują, nie sam format."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(b"XXXXXXXX"), image_format="gif")

    assert "gif" in out["error"] and not sender.sent


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (_PNG_SIG + b"rest", "png"),
        (_JPG_SIG + b"rest", "jpg"),
        (b"GIF87a...", "gif"),
        (b"GIF89a...", "gif"),
        (b"not an image", None),
        (b"", None),
        (_PNG_SIG[:-1], None),  # obcięta sygnatura PNG nie liczy się jako png
    ],
)
def test_sniff_image_format_recognizes_signatures(raw: bytes, expected):
    """Pure-helper rozpoznaje kanoniczny format po magic bytes (png/jpg/gif) albo ``None``."""
    assert sniff_image_format(raw) == expected


def test_model_cannot_choose_recipient():
    """Cel wiąże fabryka, nie model — sygnatura narzędzia nie ma pola odbiorcy/target_user_id.

    Sondujemy przez ``inspect.signature``, nie przez ``__code__.co_varnames[:co_argcount]``:
    ``co_argcount`` NIE liczy parametrów keyword-only, więc dołożenie ``*, target_user_id``
    przeszłoby tamtą asercję bez śladu — model wybierałby odbiorcę, a sonda bezpieczeństwa
    dalej byłaby zielona.
    """
    fn = _tool(_FakeSender())

    assert set(inspect.signature(fn).parameters) == {"image_base64", "image_format"}


def test_result_is_json_serializable_round_trip():
    """Runtime serializuje wynik narzędzia — musi przejść tam i z powrotem bez straty."""
    sender = _FakeSender()
    out = _tool(sender)(image_base64=_b64(_VALID_BYTES["gif"]), image_format="gif")

    assert json.loads(json.dumps(out)) == out
