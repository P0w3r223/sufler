"""Ustawienia katalogu procedur agenta montowanego pod ``/mnt/skills`` (ADR 0005)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from workmate.config._env import _int_from_env, _optional_path_from_env

# Ile procedur trafia do nagłówka sesji. Granica jest po to, żeby lista nie rosła w nieskończoność
# kosztem KAŻDEJ tury (nagłówek jest poza cache'em prefiksu), a przekroczenie było GŁOŚNE:
# w Claude Code analogiczny budżet ucina listę bez ostrzeżenia, więc procedura leży na dysku,
# jest poprawna i jest nieosiągalna — objaw nie do odróżnienia od „model jej nie użył".
_MAX_SKILLS_IN_HEADER = 20


@dataclass(frozen=True)
class SkillsSettings:
    """Katalog procedur powtarzalnej pracy montowany read-only (ADR 0005, `/mnt/skills`).

    Zdolność włącza się SAMĄ obecnością ścieżki — nie ma osobnej bramki, bo nie ma czego bramkować:
    katalog jest read-only, a model czyta go tą samą powłoką, którą już ma. Bez ścieżki lista
    w nagłówku sesji zostaje pusta i zachowanie jest dokładnie dawne.
    """

    skills_dir: Path | None = None
    max_in_header: int = _MAX_SKILLS_IN_HEADER

    @classmethod
    def from_env(cls) -> SkillsSettings:
        return cls(
            skills_dir=_optional_path_from_env("WORKMATE_SKILLS_DIR"),
            max_in_header=_int_from_env("WORKMATE_SKILLS_MAX_IN_HEADER", _MAX_SKILLS_IN_HEADER),
        )
