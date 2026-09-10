"""Modele widoku i wszystkie zdania programu. Moduł czysty — bez biblioteki wyjścia.

Kształt `Block` skopiowany z `ceidg-tool/ceidg_tool/ui/texts.py` (kopia z 2026-09-10); treść
napisana od nowa. Dzięki temu każdy ekran daje się sprawdzić w teście bez terminala, a
`cli.py` nie układa ani jednego zdania (reguła granic 7) — co z kolei jest warunkiem, żeby
reguła 6 dała się w ogóle sprawdzić skanem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

NAZWA = "krs-tool"


@dataclass(frozen=True)
class Block:
    """Model widoku: tytuł, opcjonalna tabela, przypisy."""

    title: str
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_text(self) -> str:
        """Reprezentacja tekstowa — to na niej opierają się testy ekranów."""
        parts = [self.title]
        if self.headers:
            parts.append(" | ".join(self.headers))
        parts.extend(" | ".join(row) for row in self.rows)
        parts.extend(self.notes)
        return "\n".join(parts)


def pierwszy_ekran() -> Block:
    """Ekran powitalny.

    Niesie dwie rzeczy, które w tym projekcie są granicami, a nie ozdobą: **kogo narzędzie
    obsługuje** (spółki z KRS, nie jednoosobowe działalności — granica prawna, `docs/adr/0001`)
    oraz **że nie łączy się z rejestrem**. Operator ma to wiedzieć, zanim zapyta.
    """
    return Block(
        title=f"{NAZWA} — raport o ryzyku spółki na podstawie odpisu z KRS",
        headers=("zakres", "opis"),
        rows=(
            (
                "kogo obsługuje",
                "spółki wpisane do rejestru przedsiębiorców KRS",
            ),
            (
                "kogo nie obsługuje",
                "jednoosobowe działalności — te są w CEIDG i obsługuje je ceidg-tool",
            ),
            (
                "skąd bierze dane",
                "z odpisu zapisanego wcześniej przez operatora do pliku",
            ),
            (
                "czego nie robi",
                "nie łączy się z rejestrem i nie pobiera niczego samodzielnie",
            ),
        ),
        notes=(
            "Na tym etapie narzędzie nie ocenia jeszcze terminowości składania sprawozdań.",
            "Powód i stan prac: docs/status.md oraz docs/niezmierzone.md.",
        ),
    )
