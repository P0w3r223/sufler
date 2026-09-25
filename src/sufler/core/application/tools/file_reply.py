"""Dostawa pliku i obrazu do rozmówcy: ``ReplyWithFile``, ``SendImage``, ``SendDocument``."""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from html import escape
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

if TYPE_CHECKING:
    from sufler.core.ports.document import DocumentRenderer
    from sufler.core.ports.file_output import TeamsFileSender
    from sufler.core.ports.user_doc_push import UserDocSender
    from sufler.core.ports.user_push import UserImageSender

from sufler.core.application.tools.spec import ToolSpec, _envelope
from sufler.core.errors import InvalidRequestError, SuflerError
from sufler.core.ports.document import FILE_REPLY_FORMATS
from sufler.core.ports.user_push import IMAGE_CONTENT_TYPES, sniff_image_format


def build_file_reply_catalog(
    sender: TeamsFileSender,
    renderer: DocumentRenderer,
    team_id: str,
    channel_id: str,
    root_id: str,
    *,
    max_bytes: int,
) -> list[ToolSpec]:
    """SCOPED narzędzie odpowiedzi PLIKIEM w wątku Teams (ADR 0026, A′2, bramka enable_file_reply).

    Cel dostawy (``team/channel/root``) jest PRE-ZWIĄZANY z zaufanego ``external_id`` wątku, NIE od
    modelu — plik ląduje wyłącznie w wątku bieżącej rozmowy, nigdy w dowolnym czacie (kontrola
    kompensująca ryzyko eksfiltracji, ADR 0026 §Threat model). Model podaje jedynie treść, format i
    nazwę bazową. Wstrzykiwane PER TURĘ tylko przy włączonej bramce ``enable_file_reply``.

    Renderowanie i dostawa NIE mogą wywrócić pollera: przewidywalną awarię (usunięty root wątku →
    ``ThreadRootGone``, zły format lub za duży plik → ``InvalidRequestError``) łapiemy w kopercie i
    zwracamy ``{"error": ...}``, więc model degraduje do odpowiedzi TEKSTEM w tej samej turze (ADR
    0026: „an upload failure degrades to a text reply"). Twardą awarię infrastruktury Graph (brak
    zakresu, trwałe 5xx) świadomie PUSZCZAMY wyżej — ``SafeResponder`` ją zaloguje i zdegraduje, a
    operator ma ją zobaczyć, nie połknąć po cichu.
    """
    formats = "/".join(FILE_REPLY_FORMATS)

    def reply_with_file(
        content: str, file_format: str, filename: str = "odpowiedz"
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if not content.strip():
                raise InvalidRequestError("Pusta treść — nie ma czego renderować do pliku.")
            fmt = file_format.strip().lower()
            if fmt not in FILE_REPLY_FORMATS:
                raise InvalidRequestError(
                    f"Nieobsługiwany format {file_format!r}; dozwolone: {formats}."
                )
            rendered = renderer.render(content, fmt)
            if len(rendered.content) > max_bytes:
                raise InvalidRequestError(
                    f"Zrenderowany plik ({len(rendered.content)} B) przekracza limit {max_bytes} B."
                )
            name = _safe_doc_name(filename, fmt, rendered.content)
            uploaded = sender.upload_channel_file(
                team_id, channel_id, name, rendered.content, rendered.content_type
            )
            sender.post_reply_with_attachment(
                team_id, channel_id, root_id, _file_reply_html(uploaded.name), uploaded
            )
            return {"replied": True, "file": uploaded.name, "format": fmt}

        return _envelope(build, errors=(SuflerError, ValidationError))

    description = (
        f"Odpowiedz w TYM wątku Teams PLIKIEM ({formats}) — renderuje podaną treść do pliku i "
        "załącza go w wątku (ZAPIS — wysyła wiadomość z załącznikiem). Podajesz ``content`` (treść "
        f"do zapisania, Markdown/tekst), ``file_format`` (jeden z: {formats}) oraz opcjonalnie "
        "``filename`` (baza nazwy, bez rozszerzenia). Cel wątku jest ustalony z rozmowy — nie "
        "podajesz go. Użyj TYLKO gdy użytkownik WPROST prosi o plik albo dokument."
    )
    return [ToolSpec("ReplyWithFile", description, reply_with_file, taints=False)]


def _safe_doc_name(base: str, fmt: str, content: bytes) -> str:
    """Zbuduj bezpieczną, UNIKALNĄ-PO-TREŚCI nazwę ``<slug>-<hash>.<fmt>`` z bazy od modelu.

    Slug (alnum + łącznik, ASCII, przycięty) odcina separatory ścieżki i ``..`` niezależnie od
    kodowania po stronie adaptera — obrona w głąb; polskie znaki upraszczamy (sam plik trzyma pełną
    treść UTF-8, slug dotyczy tylko nazwy). Sufiks = 8 znaków skrótu TREŚCI: upload jest
    nadpisujący-po-ścieżce (ADR 0026), więc ta sama treść (np. ponowiona tura) daje TĘ SAMĄ nazwę
    (idempotencja, bez duplikatu), a RÓŻNA treść — różną nazwę, żeby dwie odpowiedzi w tym samym
    kanale o tej samej bazie nie nadpisały się nawzajem (integralność pliku wskazywanego z wątku).
    """
    slug = re.sub(r"[^0-9A-Za-z]+", "-", base).strip("-").lower()[:64] or "odpowiedz"
    digest = hashlib.sha256(content).hexdigest()[:8]
    return f"{slug}-{digest}.{fmt}"


def _file_reply_html(filename: str) -> str:
    """Zaufany, ESCAPOWANY HTML podpisu odpowiedzi z załącznikiem (składany w rdzeniu, ADR 0026)."""
    return f"<p>W załączniku: {escape(filename)}</p>"


def build_user_image_push_catalog(
    sender: UserImageSender, target_user_id: str, *, max_bytes: int
) -> list[ToolSpec]:
    """SCOPED narzędzie wysyłki OBRAZU do rozmówcy 1:1 na Teams (ADR 0027, A′3, push bramkowany).

    Odbiorca (``target_user_id``) jest PRE-ZWIĄZANY z nadawcy bieżącej wiadomości, NIE od modelu —
    obraz trafia wyłącznie do osoby, która właśnie napisała do agenta, nigdy do dowolnego AAD id
    (kontrola kompensująca ryzyko spamu/eksfiltracji do osoby, ADR 0027 §Threat model). Model podaje
    jedynie bajty obrazu (base64) i format. Wstrzykiwane PER TURĘ tylko przy włączonej bramce.

    Awaria nie może wywrócić pollera: przewidywalną (zły format/base64, pusty lub za duży obraz)
    łapiemy w kopercie i zwracamy ``{"error": ...}``, więc model degraduje do odpowiedzi TEKSTEM
    (jak ADR 0026). Twardą awarię Graph (brak zakresu, trwałe 5xx) świadomie PUSZCZAMY wyżej —
    ``SafeResponder`` ją zaloguje, a operator ma ją zobaczyć, nie połknąć.
    """
    formats = "/".join(sorted(IMAGE_CONTENT_TYPES))

    def send_image_to_user(image_base64: str, image_format: str) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            fmt = image_format.strip().lower()
            content_type = IMAGE_CONTENT_TYPES.get(fmt)
            if content_type is None:
                raise InvalidRequestError(
                    f"Nieobsługiwany format obrazu {image_format!r}; dozwolone: {formats}."
                )
            try:
                content = base64.b64decode(image_base64.strip(), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise InvalidRequestError("Niepoprawne base64 obrazu.") from exc
            if not content:
                raise InvalidRequestError("Pusty obraz — nie ma czego wysłać.")
            if len(content) > max_bytes:
                raise InvalidRequestError(
                    f"Obraz ({len(content)} B) przekracza limit {max_bytes} B."
                )
            # Boundary validation: bajty MUSZĄ zgadzać się z deklarowanym formatem (magic bytes),
            # by model nie wysłał dowolnej treści z ``contentType: image/png`` (jpeg = kanon jpg).
            canonical = "jpg" if fmt == "jpeg" else fmt
            if sniff_image_format(content) != canonical:
                raise InvalidRequestError(
                    f"Bajty nie są obrazem {fmt!r} (nierozpoznana lub niezgodna sygnatura)."
                )
            sender.send_image_to_user(target_user_id, content, content_type)
            return {"sent": True, "format": fmt, "bytes": len(content)}

        return _envelope(build, errors=(SuflerError, ValidationError))

    description = (
        f"Wyślij OBRAZ ({formats}) rozmówcy 1:1 na Teams — osobie, która pisze w TEJ rozmowie "
        "(ZAPIS — wysyła wiadomość z obrazem). Podajesz ``image_base64`` (bajty obrazu zakodowane "
        f"base64) oraz ``image_format`` (jeden z: {formats}). Odbiorca jest ustalony z rozmowy — "
        "nie podajesz go. Użyj TYLKO gdy użytkownik WPROST prosi o obraz."
    )
    return [ToolSpec("SendImage", description, send_image_to_user, taints=False)]


def build_user_doc_push_catalog(
    sender: UserDocSender,
    renderer: DocumentRenderer,
    target_user_id: str,
    *,
    max_bytes: int,
) -> list[ToolSpec]:
    """SCOPED narzędzie wysyłki DOKUMENTU do rozmówcy 1:1 (ADR 0027, wariant plikowy, bramka OFF).

    Lustro ``reply_with_file`` (A′2 — renderuje treść przez ``DocumentRenderer``), ale dostawa jak
    ``send_image_to_user``: odbiorca (``target_user_id``) jest PRE-ZWIĄZANY z nadawcy bieżącej
    wiadomości, NIE od modelu — plik trafia wyłącznie do osoby, która właśnie napisała do agenta,
    nigdy do dowolnego AAD id (kontrola kompensująca ryzyko spamu/eksfiltracji, ADR 0027 §Threat
    model). Model podaje jedynie treść, format i bazę nazwy. Wstrzykiwane PER TURĘ przy bramce ON.

    Awaria nie może wywrócić pollera: przewidywalną (zły format, pusta lub za duża treść) łapiemy w
    kopercie i zwracamy ``{"error": ...}``, więc model degraduje do odpowiedzi TEKSTEM w tej samej
    turze. Twardą awarię Graph (brak zakresu, trwałe 5xx) PUSZCZAMY wyżej — ``SafeResponder``
    ją zaloguje, a operator ma ją zobaczyć, nie połknąć po cichu.
    """
    formats = "/".join(FILE_REPLY_FORMATS)

    def send_document_to_user(
        content: str, file_format: str, filename: str = "dokument"
    ) -> dict[str, Any]:
        def build() -> dict[str, Any]:
            if not content.strip():
                raise InvalidRequestError("Pusta treść — nie ma czego renderować do pliku.")
            fmt = file_format.strip().lower()
            if fmt not in FILE_REPLY_FORMATS:
                raise InvalidRequestError(
                    f"Nieobsługiwany format {file_format!r}; dozwolone: {formats}."
                )
            rendered = renderer.render(content, fmt)
            if len(rendered.content) > max_bytes:
                raise InvalidRequestError(
                    f"Zrenderowany plik ({len(rendered.content)} B) przekracza limit {max_bytes} B."
                )
            name = _safe_doc_name(filename, fmt, rendered.content)
            sender.send_document_to_user(
                target_user_id, name, rendered.content, rendered.content_type
            )
            return {"sent": True, "file": name, "format": fmt}

        return _envelope(build, errors=(SuflerError, ValidationError))

    description = (
        f"Wyślij DOKUMENT ({formats}) rozmówcy 1:1 na Teams — renderuje podaną treść do pliku i "
        "wysyła go jako załącznik osobie, która pisze w TEJ rozmowie (ZAPIS — wysyła wiadomość z "
        "plikiem). Podajesz ``content`` (treść, Markdown/tekst), ``file_format`` (jeden z: "
        f"{formats}) oraz opcjonalnie ``filename`` (baza nazwy, bez rozszerzenia). Odbiorca jest "
        "ustalony z rozmowy — nie podajesz go. Użyj TYLKO gdy użytkownik WPROST prosi o plik."
    )
    return [ToolSpec("SendDocument", description, send_document_to_user, taints=False)]
