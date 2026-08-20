"""Port migawek notatki przed mutacją (ADR 0065).

Migawka jest tu NOŚNA, nie zapasowa. Do 0065 odwracalność bazy wiedzy była strukturalna —
create-only ``os.link`` sprawiał, że nie dało się zniszczyć niczego, co już istnieje. Gdy ta
własność znika, jedyne, co zostaje między pomyłką a utratą wiedzy pionu, to kopia. Dlatego
nieudany zapis migawki ODMAWIA operacji: „nie udało się zrobić kopii, więc skasowałem mimo to"
byłoby dokładnie odwrotnością tego, po co ta warstwa istnieje.

Migawka i nocna kopia wolumenu (infra ADR 0008) grają różne role i obie są potrzebne: migawka
cofa JEDNĄ pomyłkę natychmiast, kopia ratuje przed złym dniem. Sama migawka nie wystarcza, bo
mieszka na tym samym wolumenie co notatki.
"""

from __future__ import annotations

from typing import Protocol


class NoteSnapshots(Protocol):
    """Zapis kopii treści notatki przed jej zmianą albo usunięciem."""

    def save(self, note_id: str, content: str) -> str:
        """Zapisz migawkę PLIKU notatki i zwróć jej lokalizację (do komunikatu i do audytu).

        ``content`` to bajty pliku z dysku, nie render z modelu — i to jest różnica między
        kopią a rekonstrukcją. Wartość notatki w tym schemacie siedzi w dużej mierze we
        frontmatterze: tytuł, uczestnicy, DECYZJE, zadania, pytania otwarte. Render odtwarzał
        wyłącznie pola ZNANE schematowi ``NoteMetadata`` (pydantic z ``extra="ignore"``), więc
        wszystko dopisane ręcznie — `status:`, `source_url:`, komentarz YAML — ginęło w kopii po
        cichu. Przy ``edit`` ratowała jeszcze nocna kopia wolumenu; przy ``delete`` migawka jest
        JEDYNYM odzyskiem, więc strata była nieodwracalna.

        Kopia bajtów nie ma też jak rozjechać się z formatem zapisu, bo niczego nie formatuje.

        Podnosi ``WriteError``, gdy kopia się nie uda — wołający MUSI wtedy odmówić mutacji.
        """
        ...
