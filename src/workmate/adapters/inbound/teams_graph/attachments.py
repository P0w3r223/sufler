"""Materializacja załączników Teams: referencja → bajty → ``Attachment`` (base64/tekst).

Tu żyje I/O (pobranie z Graph przez wstrzyknięty port) + kodowanie base64 + ekstrakcja
tekstu z ``.docx`` (Claude API nie przyjmuje docx natywnie) + walidacja limitów. ``python-docx``
importowany LENIWIE (extra ``teams-graph``). Błąd/limit/nieobsługiwany typ NIE kładzie pollera
— zamiast bajtów wstawiamy krótką notkę tekstową (agent poinformuje użytkownika). Treść
załącznika to DANE, nie polecenia — nie interpretujemy jej tutaj.
"""
from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.ports.llm import Attachment

if TYPE_CHECKING:
    from workmate.adapters.inbound.teams_graph.poller import GraphChannelClient
    from workmate.adapters.inbound.teams_graph.selection import (
        AttachmentRef,
        ChannelMessage,
    )

logger = logging.getLogger(__name__)

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


@dataclass(frozen=True)
class AttachmentLimits:
    """Granice materializacji: rozmiar pliku, liczba i ŁĄCZNY budżet bajtów na wiadomość."""

    max_bytes: int  # pojedynczy plik
    max_count: int  # liczba na wiadomość
    max_total_bytes: int  # łączny budżet surowych bajtów na wiadomość (sufit 32 MB API)


class AttachmentMaterializer:
    """Zamienia ``AttachmentRef`` na ``Attachment``: pobiera bajty i koduje/ekstrahuje.

    Klient Graph (port ``GraphChannelClient`` z metodami binarnymi) jest wstrzykiwany —
    pełną materializację testujemy atrapą portu bez sieci.
    """

    def __init__(
        self, client: GraphChannelClient, *, limits: AttachmentLimits
    ) -> None:
        self._client = client
        self._limits = limits

    async def materialize(
        self, team_id: str, channel_id: str, msg: ChannelMessage
    ) -> tuple[Attachment, ...]:
        """Zmaterializuj referencje wiadomości; ponad limit → notka, reszta pobrana.

        Egzekwuje trzy granice: liczbę (max_count), rozmiar pliku (max_bytes) i ŁĄCZNY
        budżet bajtów (max_total_bytes) — bez tego kilka plików mogłoby przekroczyć sufit
        32 MB żądania API (bloki są odtwarzane co turę).
        """
        refs = msg.attachment_refs
        out: list[Attachment] = []
        kept = refs[: self._limits.max_count]
        if len(refs) > len(kept):
            dropped = len(refs) - len(kept)
            out.append(_note(f"Pominięto {dropped} załącznik(ów) — limit na wiadomość."))
        total = 0
        for ref in kept:
            remaining = self._limits.max_total_bytes - total
            att, size = await self._materialize_one(
                team_id, channel_id, msg.id, ref, budget=remaining
            )
            out.append(att)
            total += size
        return tuple(out)

    async def _materialize_one(
        self,
        team_id: str,
        channel_id: str,
        message_id: str,
        ref: AttachmentRef,
        *,
        budget: int,
    ) -> tuple[Attachment, int]:
        """Pobierz i zbuduj załącznik; zwróć (Attachment, liczba_zaliczonych_bajtów).

        Pobranie ORAZ budowanie (w tym ekstrakcja .docx, która rzuca na uszkodzonym pliku)
        są w JEDNYM ``try`` — każdy błąd degraduje do notki, nigdy nie zapętla pollera.
        Notka/limit → 0 bajtów zaliczonych (nie zjada budżetu).
        """
        try:
            if ref.kind == "hosted":
                data = await self._client.get_hosted_content(
                    team_id, channel_id, message_id, ref.hosted_id
                )
            else:
                data = await self._client.download_shared_url(ref.url)
            if len(data) > self._limits.max_bytes:
                return _note(f"Pominięto załącznik {ref.name}: przekracza limit rozmiaru."), 0
            if len(data) > budget:
                return (
                    _note(f"Pominięto załącznik {ref.name}: przekroczony łączny limit wiadomości."),
                    0,
                )
            built = _build(ref, data)
        except Exception:
            logger.exception("Nie udało się przetworzyć załącznika %s", ref.name)
            return _note(f"Nie udało się przetworzyć załącznika: {ref.name}."), 0
        if built is None:
            return _note(f"Pominięto załącznik {ref.name}: nieobsługiwany typ."), 0
        return built, len(data)


def _build(ref: AttachmentRef, data: bytes) -> Attachment | None:
    """Zbuduj ``Attachment`` z bajtów; ``None`` gdy typ nieobsługiwany."""
    if ref.kind == "hosted":
        media_type = _image_media_type(data)
        if media_type is None:
            return None
        return Attachment("image", media_type, ref.name, data_base64=_b64(data))
    ext = ref.name.rsplit(".", 1)[-1].lower() if "." in ref.name else ""
    if ext == "pdf":
        return Attachment("document", "application/pdf", ref.name, data_base64=_b64(data))
    if ext in ("png", "jpg", "jpeg"):
        media_type = "image/png" if ext == "png" else "image/jpeg"
        return Attachment("image", media_type, ref.name, data_base64=_b64(data))
    if ext == "docx":
        return Attachment("text", "text/plain", ref.name, text=_extract_docx(data))
    return None


def _image_media_type(data: bytes) -> str | None:
    """Rozpoznaj typ obrazu po magicznych bajtach (obrazy inline nie mają rozszerzenia)."""
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _extract_docx(data: bytes) -> str:
    """Wyciągnij tekst z .docx: akapity + komórki tabel (``python-docx``, import leniwy)."""
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts).strip()


def _b64(data: bytes) -> str:
    """Base64 bez znaków nowej linii (wymóg bloków ``image``/``document`` API)."""
    return base64.standard_b64encode(data).decode("ascii")


def _note(message: str) -> Attachment:
    """Notka tekstowa zamiast bajtów (limit/błąd/nieobsługiwany typ) — widzi ją agent."""
    return Attachment("text", "text/plain", "uwaga systemowa", text=f"[Uwaga systemowa] {message}")
