"""Port sędziego mutacji bazy wiedzy (ADR 0065).

Osobne wywołanie modelu, oceniające, czy proponowana zmiana notatki jest szkodliwa — wzorem
przebiegu weryfikacyjnego notatki ze spotkania (ADR 0047), który w tym projekcie działa: druga,
niezależna tura z wymuszonym schematem odpowiedzi.

Dlaczego port, a nie wywołanie wprost: sędzia ma być WYMIENIALNY i WYŁĄCZALNY. Rdzeń zna tylko
kontrakt „prośba → werdykt", więc bramkę da się przetestować bez sieci, a operator może wpiąć
zamiast modelu implementację odmawiającą wszystkiego, gdyby trzeba było zamknąć mutacje w minutę.

**To NIE jest granica bezpieczeństwa.** Sędzia czyta treść, która bywa wroga (diff notatki,
intencja modelu ukształtowana przez przeczytany plik), więc jest wzorcem Dual-LLM tylko z nazwy:
rozgałęzia się na skwarantowanej wartości. Twarde kontrole — autoryzacja AAD przed nim, ścieżka
i schemat poza nim, migawka niezależnie od jego zdania — trzymają niezależnie od tego, co powie.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from workmate.core.domain.mutation import JudgeVerdict, MutationRequest


class MutationJudge(Protocol):
    """Ocena pojedynczej prośby o mutację notatki."""

    def review(self, request: MutationRequest) -> JudgeVerdict:
        """Zwróć werdykt dla prośby; treść oceniana jako DANE, nigdy jako polecenia.

        Implementacja NIE PODNOSI wyjątków na błędach modelu ani sieci — zwraca wtedy odmowę
        z powodem. Bramka i tak łapie wyjątki, ale kontrakt jest jawny, żeby nikt nie napisał
        implementacji „przepuść, skoro model nie odpowiedział".
        """
        ...
