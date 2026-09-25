"""Odczyt katalogu procedur ``/mnt/skills`` — nazwa i jednolinijkowy opis do nagłówka sesji.

Układ jest najprostszy, jaki da się obronić: ``<korzeń>/<nazwa>/SKILL.md``. Nazwa procedury to
nazwa KATALOGU, opis to pierwsza niepusta linia pliku spoza nagłówków Markdown. **Nie ma parsera
frontmattera i to jest decyzja, nie uproszczenie** — najcięższe udokumentowane ataki na katalogi
procedur (wykonanie polecenia osadzonego w pliku, zanim model cokolwiek zobaczy; nadawanie sobie
uprawnień polem `allowed-tools`) są własnością PREPROCESORA, nie własnością czytania plików.
Dopóki plik jest tekstem, którego treść ląduje w kontekście przez zwykłe `cat`, cała ta klasa
nas nie dotyczy. Pierwszy dodany parser pola sterującego tę własność kasuje.

Czytamy przy składaniu drzwi, RAZ na proces: lista jest stała w obrębie procesu, więc odczyt per
turę byłby wyłącznie kosztem. Dodanie procedury wymaga restartu kontenera — co jest zgodne z
regułą „prompt opisuje świat, który agent ZASTAJE".
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

logger = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"

# Opis jedzie do nagłówka sesji przy KAŻDEJ turze, więc długość jest kosztem powtarzalnym.
_MAX_DESCRIPTION_CHARS = 200

# Znaki niewidoczne w podglądzie, a niosące treść do modelu: zero-width, znaczniki kierunku pisma
# i separatory. To udokumentowana technika ukrywania instrukcji w plikach procedur — przy dostawie
# przez `cat` nie ma żadnej sanityzacji po drodze, więc odsiewamy je tutaj, na wejściu do promptu.
_INVISIBLE_CATEGORIES = frozenset({"Cf", "Cc", "Zl", "Zp"})


def read_skill_catalog(root: Path | None, *, limit: int) -> tuple[tuple[str, str], ...]:
    """Zwróć pary ``(nazwa, opis)`` posortowane po nazwie; pusta krotka, gdy katalogu nie ma.

    ``limit`` ucina listę — ale GŁOŚNO, przez ostrzeżenie w logu. Ciche ucięcie zostawia
    procedurę na dysku poprawną i nieosiągalną, a objaw („model jej nie użył") nie wskazuje
    przyczyny. Katalog bez ``SKILL.md`` jest pomijany, też z ostrzeżeniem: to niedokończona
    procedura, a nie sygnał, że czegoś nie ma.
    """
    if root is None:
        return ()
    if not root.is_dir():
        logger.warning("Katalog procedur %s nie istnieje — lista skilli zostaje pusta.", root)
        return ()

    found: list[tuple[str, str]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        description = _describe(entry / SKILL_FILENAME)
        if description is None:
            logger.warning(
                "Procedura %r nie ma czytelnego %s — pomijam.", entry.name, SKILL_FILENAME
            )
            continue
        found.append((entry.name, description))

    if len(found) > limit:
        dropped = ", ".join(name for name, _ in found[limit:])
        logger.warning(
            "Procedur jest %d przy limicie %d — do nagłówka nie wejdą: %s. "
            "Model ich nie zobaczy, mimo że leżą na dysku.",
            len(found),
            limit,
            dropped,
        )
    return tuple(found[:limit])


def _describe(path: Path) -> str | None:
    """Pierwsza niepusta linia treści (spoza nagłówków Markdown) albo ``None``.

    Świadomie NIE stosujemy tu reguł redakcyjnych promptu (bez wersalików nacisku, bez
    dyrektyw). Pomiar aktywacji procedur pokazuje odwrotną zależność niż w prompcie
    systemowym: opis dyrektywny, z jawnym wyzwalaczem, wybierany jest wielokrotnie
    częściej niż opis rzeczowy. To inny artefakt i inny reżim — patrz ADR 0009 paczki.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    for line in raw.splitlines():
        stripped = _strip_invisible(line).strip()
        if not stripped or stripped.startswith("#"):
            continue
        return stripped[:_MAX_DESCRIPTION_CHARS]
    return None


def _strip_invisible(text: str) -> str:
    """Usuń znaki niewidoczne w podglądzie pliku, a widoczne dla modelu."""
    return "".join(ch for ch in text if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES)
