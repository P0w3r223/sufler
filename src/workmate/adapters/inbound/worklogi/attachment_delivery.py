"""Dostawa arkusza worklogu ZAŁĄCZNIKIEM (A′4, ADR 0035/0038 przez 0027) — wspólna dla drzwi.

Wsadowe drzwi (``worklogi``) i self-service (``worklog_selfservice``) dostarczają arkusz tak samo:
wgrywają `.xlsx` na OneDrive „głosu" bota i referują go jako załącznik w czacie 1:1 przez
``UserDocSender`` (adapter ``HttpxGraphUserDocPush``). Trzymamy to w JEDNYM miejscu, żeby zmiana
(timeout, komunikat bramki, polityka) nie ominęła jednego z drzwi i nie rozjechała ich zachowania.

Rdzeń o tym module nie wie — to warstwa DRZWI: bajta arkusza czyta się TU z dysku (rdzeń podał tylko
ścieżkę), a HTML treści to zaufany wynik ``render_timesheet_message`` (kontrakt ``caption_html``).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workmate.config import WORKLOG_ATTACHMENT_WRITE_SCOPES, TeamsPushSettings, WorklogiSettings

# Sufit czasu wgrania arkusza — hojny, bo to jednorazowe wgranie kilkudziesięciu KB na osobę/tydzień
# (nie ścieżka gorąca); wspólny dla obu drzwi.
_UPLOAD_TIMEOUT_S = 60


def send_worklog_document(
    token: Any, aad_user_id: str, file_path: str, body_html: str
) -> None:  # pragma: no cover - kompozycja I/O; logika UserDocSender testowana osobno
    """Wczytaj bajta arkusza z dysku i wgraj przez ``UserDocSender`` jako załącznik w czacie 1:1.

    Klient sync (``HttpxGraphUserDocPush``) powstaje i ginie na wywołanie — jak most sync→async w
    ``send_html`` drzwi. ``body_html`` to ZAUFANY HTML z rdzenia (tryb „w załączniku"), idzie jako
    ``caption_html`` bez escapowania (kontrakt ``send_document_to_user``).
    """
    import httpx

    from workmate.adapters.outbound.graph_user_doc_push import HttpxGraphUserDocPush
    from workmate.adapters.outbound.openpyxl_sheet_writer import XLSX_CONTENT_TYPE

    content = Path(file_path).read_bytes()
    with httpx.Client(timeout=_UPLOAD_TIMEOUT_S) as doc_http:
        HttpxGraphUserDocPush(doc_http, token).send_document_to_user(
            aad_user_id,
            Path(file_path).name,
            content,
            XLSX_CONTENT_TYPE,
            caption_html=body_html,
        )


def require_attachment_scopes(settings: WorklogiSettings, push: TeamsPushSettings) -> None:
    """Dostawa załącznikiem wgrywa arkusz na OneDrive — token push MUSI nieść zapis do plików.

    Fail-fast jak przy innych bramkach: włączona zdolność bez zakresu, który jej wymaga, to cicha,
    funkcjonalnie martwa konfiguracja (upload padłby dopiero na 403 w piątek). Akceptujemy węższy
    ``Files.ReadWrite`` lub szerszy ``Files.ReadWrite.All``. Sprawdzamy w wiringu drzwi, bo
    ``WorklogiSettings`` nie widzi zakresów push (ta sama zasada co ``_require_teams``).
    """
    if settings.enable_attachment and not any(
        scope in push.scopes for scope in WORKLOG_ATTACHMENT_WRITE_SCOPES
    ):
        raise SystemExit(
            "WORKMATE_WORKLOGI_ENABLE_ATTACHMENT=true wymaga zapisu do plików w "
            "WORKMATE_TEAMS_PUSH_SCOPES: dodaj 'Files.ReadWrite' (lub 'Files.ReadWrite.All') "
            "i przejdź ponowną zgodę device-code (--login)."
        )
