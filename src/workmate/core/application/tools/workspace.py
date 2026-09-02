"""Katalog roboczy agenta bez powłoki: ``CreateFile``/``ReadFile``/``ListFiles``."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from workmate.core.application.tools.spec import ToolSpec, _envelope
from workmate.core.application.workspace import WorkspaceService, WorkspaceWriteService
from workmate.core.domain.workspace import WorkspaceScope
from workmate.core.errors import WorkMateError


def build_workspace_catalog(
    scope: WorkspaceScope,
    read_service: WorkspaceService,
    write_service: WorkspaceWriteService,
) -> list[ToolSpec]:
    """Zbuduj narzędzia KATALOGU ROBOCZEGO agenta dla danej rozmowy (ADR 0018).

    Osobne od ``build_tool_catalog`` i używane WYŁĄCZNIE przez runtime agenta (nie przez drzwi
    MCP) — dlatego golden-test powierzchni MCP zostaje nietknięty. ``scope`` (podkatalog rozmowy)
    jest DOMKNIĘTY w closurach — model go nie widzi w schemacie (nie może wskazać cudzej rozmowy).
    """

    def create_file(name: str, content: str) -> dict[str, Any]:
        """Utwórz plik roboczy w katalogu tej rozmowy (ZAPIS — tworzy nowy plik).

        ``name`` musi mieć rozszerzenie (dozwolone: md, txt, csv, json), np. 'raport-mpwik.md'.
        Nazwa jest zawężana do bezpiecznego sluga; nigdy nie nadpisuje (przy kolizji dokłada
        sufiks). Plik zostaje w katalogu roboczym rozmowy — użyj ListFiles/ReadFile, by do
        niego wrócić w kolejnej turze.
        """

        def build() -> dict[str, Any]:
            created = write_service.create_file(scope, name, content)
            return {"created": True, "name": created.name, "path": created.relpath}

        return _envelope(build, errors=(WorkMateError, ValidationError))

    def read_file(name: str) -> dict[str, Any]:
        """Odczytaj treść wcześniej utworzonego pliku roboczego tej rozmowy (nazwa z ListFiles)."""

        def build() -> dict[str, Any]:
            content = read_service.read_file(scope, name)
            if content is None:
                return {"error": f"Plik nie istnieje w katalogu roboczym: {name}"}
            return {"name": name, "content": content}

        return _envelope(build, errors=(WorkMateError, ValidationError))

    def list_files() -> dict[str, Any]:
        """Wypisz pliki utworzone w katalogu roboczym tej rozmowy (nazwa i rozmiar w bajtach)."""

        def build() -> dict[str, Any]:
            files = read_service.list_files(scope)
            return {
                "count": len(files),
                "files": [{"name": f.name, "size": f.size} for f in files],
            }

        return _envelope(build)

    # Nazwy w konwencji agenta (PascalCase, ADR 0068 §3): katalog roboczy nie jest na powierzchni
    # MCP, więc zamrożenie go nie dotyczy, a dwie konwencje w jednym katalogu kodowały modelowi
    # rozróżnienie („skonsolidowane" kontra „zastane"), którego nie ma jak odczytać.
    return [
        ToolSpec("CreateFile", create_file.__doc__ or "", create_file),
        ToolSpec("ReadFile", read_file.__doc__ or "", read_file),
        ToolSpec("ListFiles", list_files.__doc__ or "", list_files),
    ]
