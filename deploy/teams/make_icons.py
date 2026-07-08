"""Generator placeholderów ikon Teams — czysty Python + zlib, bez zależności.

Manifest Teams wymaga dwóch ikon o ustalonych wymiarach; treść może być
placeholderem, ale wymiary muszą się zgadzać:

- ``color.png``   192×192, pełny kolor akcentu (#2A6FF3),
- ``outline.png`` 32×32, w pełni przezroczysta (placeholder konturu).

Uruchom: ``uv run --no-sync python deploy/teams/make_icons.py``. Podmień oba pliki
na docelowe grafiki przed publikacją — to tylko wypełniacze, by pakiet się walidował.
"""
from __future__ import annotations

import struct
import zlib
from pathlib import Path

# Kolor akcentu z manifestu (RGBA, w pełni krycia).
_ACCENT = (0x2A, 0x6F, 0xF3, 0xFF)


def _png(width: int, height: int, rgba: tuple[int, int, int, int]) -> bytes:
    """Zwróć bajty PNG (8-bit RGBA) wypełnionego jednolitym kolorem ``rgba``."""
    row = bytes(rgba) * width
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # bajt filtra scanline: None
        raw.extend(row)
    return _assemble(width, height, bytes(raw))


def _assemble(width: int, height: int, raw: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        crc = zlib.crc32(body) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + body + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit, kolor typ 6 (RGBA)
    idat = zlib.compress(raw, 9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", idat)
        + chunk(b"IEND", b"")
    )


def main() -> None:
    here = Path(__file__).parent
    (here / "color.png").write_bytes(_png(192, 192, _ACCENT))
    (here / "outline.png").write_bytes(_png(32, 32, (0, 0, 0, 0)))
    print("zapisano deploy/teams/color.png (192x192) i outline.png (32x32)")


if __name__ == "__main__":
    main()
