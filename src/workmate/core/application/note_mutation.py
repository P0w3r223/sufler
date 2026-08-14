"""Bramka mutacji bazy wiedzy (ADR 0065) — jedyna droga do zmiany istniejącej notatki.

Kolejność jest tu treścią, nie porządkiem czytania. Twarde kontrole padają PRZED sędzią i
niezależnie od jego zdania; sędzia stoi na wierzchu i potrafi tylko ZAWĘZIĆ:

1. **Adresowanie po identyfikatorze**, nigdy po ścieżce od modelu. Notatka musi istnieć.
2. **Ścieżki deterministyczne zostają create-only** — notatki ze spotkania i wątku opierają
   idempotencję na kolizji ``os.link`` (ADR 0043/0048); mutacja by tę gwarancję rozbroiła.
3. **Strażnik treści** (``reject_dangerous_content``) na nowej treści, jak przy zapisie.
4. **Migawka** — i to ona odmawia operacji, gdy się nie uda (patrz port ``NoteSnapshots``).
5. **Sędzia** — dopiero teraz, na komplecie faktów.

Autoryzacja nadawcy po AAD (ADR 0042/0062) pada jeszcze wyżej, w drzwiach: do tej bramki nie
dochodzi nikt nierozpoznany, więc sędzia nigdy nie decyduje o tym, KTO może pisać — wyłącznie
o tym, czy TA zmiana jest rozsądna.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from workmate.core.domain.mutation import JudgeVerdict, MutationRequest, refusal
from workmate.core.domain.sanitize import reject_dangerous_content
from workmate.core.errors import WriteError

if TYPE_CHECKING:
    from workmate.core.domain.models import Note
    from workmate.core.ports.confirmations import ConfirmationLedger
    from workmate.core.ports.judge import MutationJudge
    from workmate.core.ports.repositories import NotesRepository, NotesWriter
    from workmate.core.ports.snapshots import NoteSnapshots

# Znaczniki identyfikatorów, których treść jest wyprowadzana deterministycznie z ŹRÓDŁA
# (spotkanie, wątek Teams). Ich niezmienność to nie ostrożność, tylko mechanizm idempotencji:
# ponowne przetworzenie tego samego spotkania ma trafić na istniejący plik i odbić się, a nie
# nadpisać wynik poprzedniego przebiegu.
_DETERMINISTIC_MARKERS = ("-mtg-", "-thr-")


@dataclass(frozen=True)
class MutationOutcome:
    """Wynik próby mutacji: co się stało i co powiedzieć człowiekowi.

    ``applied`` rozróżnia „zmieniono" od „nie zmieniono"; ``verdict`` niesie powód, a
    ``snapshot`` — gdzie leży kopia sprzed zmiany, żeby cofnięcie nie wymagało śledztwa.
    """

    applied: bool
    verdict: JudgeVerdict
    snapshot: str = ""


class NoteMutationService:
    """Zmiana i usunięcie notatki — z migawką i sędzią, bez wyjątków od tej drogi."""

    def __init__(
        self,
        notes: NotesRepository,
        writer: NotesWriter,
        snapshots: NoteSnapshots,
        judge: MutationJudge,
        confirmations: ConfirmationLedger,
        *,
        allow_delete: bool = False,
    ) -> None:
        self._notes = notes
        self._writer = writer
        self._snapshots = snapshots
        self._judge = judge
        self._confirmations = confirmations
        # Kasowanie ma WŁASNY przełącznik, osobny od edycji. ADR 0065 wiąże jego wypuszczenie
        # z działającą nocną kopią wolumenu: migawka cofa jedną pomyłkę, ale przed złym dniem
        # ratuje dopiero kopia poza hostem. Dopóki operator jej nie uruchomi, ta zdolność ma
        # zostać niedostępna — nie „dostępna z ostrzeżeniem".
        self._allow_delete = allow_delete

    def edit_note(self, note_id: str, new_body: str, *, requester: str, intent: str) -> Note:
        """Podmień TREŚĆ istniejącej notatki; metadane zostają nietknięte.

        Metadane (projekt, data, uczestnicy) są poza zasięgiem rozmyślnie: to z nich wywodzi się
        identyfikator i miejsce pliku, więc ich zmiana byłaby w istocie przeniesieniem notatki
        pod inny adres — inną operacją, z innym promieniem rażenia niż „popraw treść".
        """
        note = self._require_mutable(note_id)
        reject_dangerous_content(new_body)
        request = MutationRequest(
            kind="edit",
            note_id=note_id,
            requester=requester,
            intent=intent,
            current_body=note.body,
            new_body=new_body.strip(),
        )
        outcome = self._decide(request, note.body)
        if not outcome.applied:
            raise MutationRefused(outcome)
        self._writer.overwrite(note.model_copy(update={"body": new_body.strip()}))
        return note.model_copy(update={"body": new_body.strip()})

    def delete_note(self, note_id: str, *, requester: str, intent: str) -> MutationOutcome:
        """Usuń POJEDYNCZĄ notatkę — po migawce i po werdykcie sędziego."""
        if not self._allow_delete:
            raise WriteError(
                "usuwanie notatek jest wyłączone (ADR 0065 wiąże je z działającą kopią zapasową)"
            )
        note = self._require_mutable(note_id)
        request = MutationRequest(
            kind="delete",
            note_id=note_id,
            requester=requester,
            intent=intent,
            current_body=note.body,
        )
        outcome = self._decide(request, note.body)
        if not outcome.applied:
            raise MutationRefused(outcome)
        self._writer.delete(note_id)
        return outcome

    def _require_mutable(self, note_id: str) -> Note:
        """Zwróć notatkę, jeśli w ogóle wolno ją ruszać; inaczej ``WriteError`` z powodem."""
        if any(marker in note_id for marker in _DETERMINISTIC_MARKERS):
            raise WriteError(
                f"notatka {note_id} pochodzi ze spotkania lub wątku i jest tylko do odczytu "
                "— jej niezmienność jest gwarancją, że powtórne przetworzenie źródła niczego "
                "nie nadpisze"
            )
        note = self._notes.get(note_id)
        if note is None:
            raise WriteError(f"notatka nie istnieje: {note_id}")
        return note

    def _decide(self, request: MutationRequest, current: str) -> MutationOutcome:
        """Migawka, potem sędzia. Awaria któregokolwiek kroku = odmowa.

        Migawka PRZED werdyktem, choć przy odmowie okaże się niepotrzebna: gdyby powstawała po
        werdykcie, jej awaria zostawiałaby operację zatwierdzoną i niezabezpieczoną, a to gorszy
        stan niż jedna zbędna kopia. Kopia jest tania, utrata notatki nie.
        """
        try:
            location = self._snapshots.save(request.note_id, current)
        except Exception as exc:
            return MutationOutcome(False, refusal(f"nie udało się zabezpieczyć kopii: {exc}"))
        try:
            verdict = self._judge.review(request)
        except Exception as exc:  # implementacja portu ma nie rzucać — ale to bramka, nie ufa
            return MutationOutcome(False, refusal(f"sędzia niedostępny: {exc}"), location)
        if verdict.verdict == "confirm":
            return MutationOutcome(self._confirmed(request), verdict, location)
        return MutationOutcome(verdict.verdict == "allow", verdict, location)

    def _confirmed(self, request: MutationRequest) -> bool:
        """Czy ta dokładnie prośba wróciła po zapowiedzi (punkt kontrolny człowieka).

        Pierwsze wystąpienie: zapamiętujemy i ODMAWIAMY — model ma powiedzieć człowiekowi, co
        miałoby się stać. Powtórzenie w późniejszej turze przechodzi, bo tura powstaje tylko
        wtedy, gdy ktoś napisał. Zgoda jest jednorazowa: po wykonaniu wpis znika, więc kolejne
        kasowanie znów zaczyna od zapowiedzi.
        """
        key = self._confirmation_key(request)
        if self._confirmations.seen(key):
            self._confirmations.forget(key)
            return True
        self._confirmations.remember(key)
        return False

    @staticmethod
    def _confirmation_key(request: MutationRequest) -> str:
        """Klucz zapowiedzi: człowiek + rodzaj + notatka + TREŚĆ.

        Treść wchodzi w klucz, żeby zapowiedź „popraw akapit o terminie" nie autoryzowała
        podmiany całej notatki na coś innego przy powtórzeniu — potwierdzeniu ma podlegać
        konkretna zmiana, nie sama chęć zmieniania.
        """
        material = f"{request.requester}|{request.kind}|{request.note_id}|{request.new_body}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


class MutationRefused(WriteError):
    """Mutacja nie doszła do skutku — niesie werdykt, żeby wołający mógł go pokazać.

    Dziedziczy z ``WriteError``, więc każda istniejąca koperta błędów zapisu łapie ją bez zmian
    i żaden wołający nie zamieni odmowy w traceback.
    """

    def __init__(self, outcome: MutationOutcome) -> None:
        super().__init__(outcome.verdict.reason)
        self.outcome = outcome
