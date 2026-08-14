"""Pojęcia mutacji bazy wiedzy i werdyktu sędziego (ADR 0065).

To jest miejsce, w którym ten projekt ODWRACA swoją dotychczasową postawę. Do 0065 baza wiedzy
była niezniszczalna z konstrukcji: jedynym pisarzem był ``os.link`` typu create-only, więc
kolizja kończyła się błędem, a ścieżki nadpisania ani kasowania po prostu nie było. Wykonawca
montuje notatki ``ro`` (ADR 0057), a drzwi Teams nie mają zapisu (ADR 0006). Odwracalność była
STRUKTURALNA — nie dało się zepsuć tego, czego nie dało się zmienić.

Od 0065 odwracalność jest PROCEDURALNA: mutacja jest możliwa, a bezpieczeństwo daje kopia
(migawka przed operacją + nocna kopia wolumenu). To świadoma decyzja właściciela z 2026-08-14,
z jawnym rejestrem ryzyk w ADR — nie przypadkowe poluzowanie.

Sędzia (osobne wywołanie modelu) jest **obroną w głębi NA WIERZCHU** twardych kontroli, nigdy
zamiast nich: autoryzacja nadawcy po AAD pada PRZED nim, ścieżka i schemat są wymuszone poza
nim, migawka powstaje niezależnie od jego zdania. Sędzia potrafi tylko ZAWĘZIĆ to, co i tak
byłoby dozwolone — nigdy niczego nie poszerza. To istotne, bo sam sędzia jest probabilistyczny
i czyta treść, która bywa wroga: gdyby był jedyną granicą, byłby granicą do obejścia.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# Rodzaje mutacji. ``write`` (utworzenie nowej notatki) NIE jest mutacją — to dotychczasowa,
# create-only ścieżka zapisu, która nie niszczy niczego i nie potrzebuje sędziego.
MutationKind = Literal["edit", "delete"]

# Werdykt sędziego. ``confirm`` to nie „prawie tak" — to żądanie punktu kontrolnego człowieka.
Verdict = Literal["allow", "refuse", "confirm"]


@dataclass(frozen=True)
class MutationRequest:
    """Prośba o zmianę istniejącej notatki — komplet, na którym pracuje sędzia.

    ``intent`` to powód podany przez MODEL i jest daną, nie prawdą o świecie: pochodzi z tej
    samej tury, którą mogła ukształtować treść przeczytana z pliku. Sędzia dostaje go jako
    materiał do oceny, nie jako uzasadnienie do przyjęcia.

    ``trust_class``/``tainted`` (ADR 0066) opisują POCHODZENIE tury. Dopóki 0066 nie jest
    włączone, wołający podaje wartości domyślne — czyli „skażona, nieznana klasa" — bo ściśle
    bezpieczny domysł jest tu ważniejszy niż dokładność.
    """

    kind: MutationKind
    note_id: str
    requester: str  # nazwa człowieka rozwiązanego z AAD — do wglądu sędziego i do audytu
    intent: str
    current_body: str
    new_body: str = ""  # pusty dla ``delete``
    trust_class: str = "unknown"
    tainted: bool = True
    # Token TURY (ADR 0065). Punkt kontrolny człowieka opiera się na tym, że powtórzenie
    # prośby przychodzi z INNEJ tury — a tura powstaje tylko wtedy, gdy ktoś napisał. Bez
    # tego pola pętla narzędzi (do 8 rund w jednej turze, a każda runda może nieść wiele
    # wywołań) pozwalała modelowi zapowiedzieć i wykonać zmianę samemu, bez udziału człowieka.
    turn_token: str = ""


@dataclass(frozen=True)
class JudgeVerdict:
    """Odpowiedź sędziego: rozstrzygnięcie + POWÓD, który zobaczy człowiek.

    Powód jest częścią kontraktu, nie ozdobą: odmowa bez powodu zmusza użytkownika do zgadywania,
    a model do ponawiania w kółko tej samej operacji z inną formułką.
    """

    verdict: Verdict
    reason: str


def refusal(reason: str) -> JudgeVerdict:
    """Werdykt odmowny — jedyne wyjście przy błędzie, niepewności i braku odpowiedzi sędziego.

    Sędzia zawodzi W STRONĘ ODMOWY. Odwrotny domysł („nie wiem, więc puszczam") czyniłby z awarii
    sieci automatyczną zgodę na skasowanie notatki.
    """
    return JudgeVerdict("refuse", reason)
