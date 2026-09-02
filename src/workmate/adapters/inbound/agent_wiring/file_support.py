"""Fabryka narzędzia ``File`` i odkładanie załączników na dysk katalogu rozmowy (ADR 0064)."""

from __future__ import annotations

import base64
import binascii
import logging
import uuid
from typing import TYPE_CHECKING

from workmate.adapters.inbound.document_text import BINARY_EXTS
from workmate.adapters.inbound.teams_graph.attachments import FileBytesMaterializer
from workmate.adapters.outbound.filesystem_workspace import (
    FilesystemWorkspaceRepository,
    FilesystemWorkspaceWriter,
)
from workmate.core.application.note_mutation import NoteMutationService
from workmate.core.application.tools import (
    build_file_catalog,
)
from workmate.core.application.workspace import (
    WorkspaceLimits,
    WorkspaceService,
    WorkspaceWriteService,
)
from workmate.core.errors import WriteError
from workmate.core.ports.llm import Attachment, AttachmentQueue
from workmate.core.ports.materialization import MaterializationLimits

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from workmate.config import (
        WorkspaceSettings,
    )
    from workmate.core.application.note_mutation import NoteMutationService
    from workmate.core.application.note_read_authz import NoteReadAuthorizer
    from workmate.core.application.tools import ToolSpec
    from workmate.core.domain.mutation import Verdict
    from workmate.core.domain.workspace import WorkspaceScope
    from workmate.core.ports.identity import AadIdentityLookup

logger = logging.getLogger(__name__)


def build_file_support(
    workspace_settings: WorkspaceSettings,
    *,
    max_image_edge: int,
    staged_ext: frozenset[str],
    materialization_limits: MaterializationLimits,
    mutations: NoteMutationService | None = None,
    identities: AadIdentityLookup | None = None,
    read_authorizer: NoteReadAuthorizer | None = None,
    shell_available: bool = False,
) -> tuple[
    # Przedostatni argument fabryki to SKAZA rozmowy podana LENIWIE (``Callable``, nie ``bool``):
    # sędzia mutacji czyta ją w chwili orzekania, a nie budowy katalogu (ADR 0066). Ostatni to
    # ujście werdyktu sędziego do wiersza audytu tury (ADR 0065 §8) — ``None`` bez audytu.
    Callable[
        [
            WorkspaceScope,
            AttachmentQueue,
            str,
            str,
            Callable[[], bool],
            Callable[[Verdict, str], None] | None,
        ],
        list[ToolSpec],
    ],
    Callable[[WorkspaceScope, Sequence[Attachment]], list[str]],
]:
    """Zbuduj parę dla ``File`` (ADR 0064): fabrykę narzędzia i odkładanie załączników.

    Jedna funkcja zwraca oba, bo obie strony MUSZĄ patrzeć na ten sam katalog roboczy —
    rozdzielone montaże rozjechałyby się cicho: model czytałby z jednego miejsca, drzwi pisały
    do drugiego, a objawem byłoby wyłącznie „nie ma takiego pliku".

    ``staged_ext`` jest szersza niż lista rozszerzeń, które model wolno mu TWORZYĆ: użytkownik
    przysyła pdf/obrazy/dokumenty, a nie tylko md/txt/csv/json. To rozróżnienie jest celowe —
    odkładamy CUDZY plik do wglądu, nie pozwalamy modelowi pisać binariów.

    ``shell_available`` przenosi się wprost do opisu ``File`` (ADR 0068 §2): to on rozstrzyga,
    czy opis odsyła po tekst do `cat`-a, czy do ``ReadFile``, i skąd wziąć identyfikator notatki.
    Bierze się — jak wszędzie w tych drzwiach — z OBECNOŚCI fabryki powłoki, nie z ustawienia.
    """
    repo = FilesystemWorkspaceRepository(workspace_settings.workspace_dir)
    limits = WorkspaceLimits(
        max_file_bytes=workspace_settings.max_file_mb * 1024 * 1024,
        max_files_per_scope=workspace_settings.max_files_per_scope,
        max_total_bytes=workspace_settings.max_total_mb * 1024 * 1024,
        allowed_ext=frozenset(workspace_settings.allowed_ext),
    )
    read_service = WorkspaceService(repo)
    write_service = WorkspaceWriteService(
        FilesystemWorkspaceWriter(workspace_settings.workspace_dir), repo, limits
    )
    materializer = FileBytesMaterializer(max_image_edge=max_image_edge)

    def factory(
        scope: WorkspaceScope,
        queue: AttachmentQueue,
        sender_id: str,
        trust_class: str = "unknown",
        tainted: bool | Callable[[], bool] = True,
        verdict_sink: Callable[[Verdict, str], None] | None = None,
    ) -> list[ToolSpec]:
        """Zbuduj ``File`` dla tej tury; akcje mutujące TYLKO dla rozpoznanego człowieka.

        Rozwiązanie tożsamości pada TU, przy budowie katalogu — tak samo jak przy bramce
        powłoki (ADR 0063): nierozpoznany nadawca nie dostaje zdolności, zamiast dostawać ją
        i odbijać się dopiero przy wywołaniu. Nierozwiązywalna tożsamość degraduje do samego
        odczytu (fail-closed), nie do wyjątku — tura ma się odbyć.
        """
        requester = ""
        if mutations is not None and identities is not None and sender_id:
            try:
                person = identities.resolve_by_aad_user_id(sender_id)
                # Kluczem jest ``source_id`` (klucz mapy, unikalny z definicji), nie nazwa
                # wyświetlana: ta bywa pusta i bywa wspólna dla dwóch osób, a służy tu ZARAZEM
                # za tożsamość w rejestrze potwierdzeń. Dwie osoby o tej samej nazwie dzieliłyby
                # przestrzeń zgód — jedna domykałaby zapowiedź drugiej.
                requester = person.source_id if person is not None else ""
                if requester and read_authorizer is not None:
                    # ADR 0065 §7: kto nie może CZYTAĆ bazy wiedzy, nie może jej też zmieniać.
                    # Bez tego bramka odczytu (ADR 0062) przestawałaby cokolwiek znaczyć dla
                    # ścieżki NISZCZĄCEJ, a odmowa sędziego (niosąca fragment treści) byłaby
                    # kanałem odczytu wokół niej.
                    read_authorizer.authorize(sender_id)
            except Exception:
                logger.warning("Nadawca %r bez prawa mutacji bazy wiedzy — same odczyty", sender_id)
                requester = ""
        return build_file_catalog(
            scope,
            read_service,
            materializer,
            queue,
            materialization_limits,
            mutations if requester else None,
            requester,
            trust_class,
            tainted,
            # Token TURY: każde wywołanie fabryki to jedna tura, więc token wylosowany tutaj
            # jest dokładnie tym, czego potrzebuje punkt kontrolny człowieka — zapowiedź i
            # wykonanie muszą pochodzić z RÓŻNYCH tur.
            uuid.uuid4().hex,
            shell_available,
            verdict_sink,
        )

    def stage(scope: WorkspaceScope, attachments: Sequence[Attachment]) -> list[str]:
        """Zapisz załączniki tury na dysk rozmowy; zwróć nazwy, pod którymi wylądowały.

        Pojedynczy załącznik, którego nie da się odłożyć (nieznane rozszerzenie, limit dysku),
        jest POMIJANY z logiem — reszta tury jedzie dalej. Model i tak widzi go w kontekście;
        brak kopii na dysku odbiera mu jedynie możliwość wrócenia do pliku później.
        """
        names: list[str] = []
        for att in attachments:
            plik = _attachment_for_disk(att)
            if plik is None:
                continue
            nazwa, data = plik
            try:
                names.append(
                    write_service.stage_attachment(scope, nazwa, data, allowed_ext=staged_ext).name
                )
            except (WriteError, OSError):
                logger.warning("Nie odłożyłem załącznika %r na dysk rozmowy — pomijam", att.name)
        return names

    return factory, stage


def _attachment_for_disk(att: Attachment) -> tuple[str, bytes] | None:
    """``(nazwa, bajty)`` do zapisu: base64 dla obrazu/PDF, tekst dla plików zekstrahowanych.

    ``None`` dla załącznika, który nie niesie ani jednego, ani drugiego — czyli dla NOTKI
    statusu, którą materializer wstawia zamiast pliku (limit/błąd/nieobsługiwany typ). Odkładanie
    notki na dysk byłoby zapisaniem komunikatu o błędzie pod nazwą pliku, którego nie ma.

    NAZWA bywa inna niż oryginalna i to jest sedno tej funkcji. Drzwi materializują ``.docx``/
    ``.xlsx``/``.pptx`` jako TEKST po ekstrakcji (Claude API nie przyjmuje Worda natywnie) —
    oryginalnych bajtów już nie ma. Zapisanie tego tekstu pod nazwą ``raport.docx`` dawało plik,
    który KŁAMIE rozszerzeniem: model czytał go potem ``File(read)``, materializer rozpoznawał
    ``.docx`` i puszczał na niego czytnik Worda, który przewracał się na „to nie jest zip".
    Ta sama pułapka czekała na ``workmate-extract`` w powłoce. Rozszerzenie idzie więc za
    ZAWARTOŚCIĄ: tekst zapisujemy jako ``.txt``, a nazwa (razem z nią) trafia do nagłówka sesji,
    więc model woła plik tak, jak ten naprawdę się nazywa.
    """
    if att.data_base64:
        try:
            return att.name, base64.b64decode(att.data_base64, validate=True)
        except (binascii.Error, ValueError):
            return None
    if not att.text or "." not in att.name:
        return None
    stem, _, ext = att.name.rpartition(".")
    nazwa = f"{stem}.txt" if ext.lower() in BINARY_EXTS else att.name
    return nazwa, att.text.encode("utf-8")
