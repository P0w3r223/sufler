"""Repozytorium projektów oparte na jednym pliku YAML (rejestr).

Format (patrz ``data/projects/registry.yaml``)::

    projects:
      - key: mpwik
        name: MPWiK
        description: ...
        status: active
        health: green
        phase: ...
        summary: ...
        last_updated: 2025-06-20

Rejestr jest małym, ręcznie utrzymywanym źródłem prawdy o statusie projektów.
Część "żywa" statusu (liczba notatek, otwarte action items) jest dowyliczana
przez rdzeń z notatek — tutaj trzymamy tylko część zadeklarowaną.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from workmate.core.domain.models import Project, ProjectStatusRecord
from workmate.core.errors import RepositoryError


class ProjectsRegistryError(RepositoryError):
    """Rejestr projektów jest nieczytelny lub niezgodny ze schematem."""


class YamlProjectsRepository:
    """Czyta rejestr projektów z pliku YAML.

    Parsowanie jest cache'owane i unieważniane fingerprintem pliku ``(st_mtime_ns, st_size)``:
    ``all()``/``get()``/``status_record()`` w obrębie jednego wywołania re-używają jednego
    parsowania, a ręczna edycja rejestru (także z innego procesu) jest wykrywana. ``Lock``, bo
    repozytorium bywa wołane z pul wątków (HTTP MCP / drzwi async).
    """

    def __init__(self, registry_path: Path) -> None:
        self._registry_path = registry_path
        self._lock = threading.Lock()
        self._cache: tuple[tuple[int, int], list[dict[str, Any]]] | None = None

    def all(self) -> list[Project]:
        return [self._project(entry) for entry in self._entries()]

    def _project(self, entry: dict[str, Any]) -> Project:
        """Zbuduj ``Project`` z wpisu rejestru; uszkodzony wpis → ``ProjectsRegistryError``.

        Rejestr jest edytowany ręcznie, więc brakujący ``key``/``name`` albo pole złego typu to
        realny stan pliku, nie defekt kodu. Bez tłumaczenia wychodziłby surowy ``KeyError``
        („'key'") — komunikat, z którego nie da się odczytać, że chodzi o rejestr projektów.
        Tłumaczymy tak samo jak ``status_record`` robi to przez pydantic.
        """
        try:
            return Project.model_validate(
                {
                    "key": entry["key"],
                    "company": entry.get("company", ""),
                    "name": entry["name"],
                    "description": entry.get("description", ""),
                    "github_repos": list(entry.get("github_repos") or []),
                    "jira_project_key": entry.get("jira_project_key"),
                }
            )
        except (KeyError, TypeError, ValidationError) as exc:
            raise ProjectsRegistryError(
                f"{self._registry_path}: niepoprawny wpis projektu: {exc}"
            ) from exc

    def get(self, key: str) -> Project | None:
        for project in self.all():
            if project.key.lower() == key.lower():
                return project
        return None

    def status_record(self, key: str) -> ProjectStatusRecord | None:
        for entry in self._entries():
            if str(entry.get("key", "")).lower() == key.lower():
                try:
                    return ProjectStatusRecord.model_validate(entry)
                except ValidationError as exc:
                    raise ProjectsRegistryError(
                        f"{self._registry_path}: niepoprawny wpis statusu dla '{key}': {exc}"
                    ) from exc
        return None

    def _entries(self) -> list[dict[str, Any]]:
        if not self._registry_path.is_file():
            raise ProjectsRegistryError(f"Rejestr projektów nie istnieje: {self._registry_path}")
        stat = self._registry_path.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            if self._cache is not None and self._cache[0] == fingerprint:
                return self._cache[1]
            try:
                data = yaml.safe_load(self._registry_path.read_text(encoding="utf-8"))
            except yaml.YAMLError as exc:
                raise ProjectsRegistryError(
                    f"{self._registry_path}: błąd składni YAML: {exc}"
                ) from exc

            if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
                raise ProjectsRegistryError(
                    f"{self._registry_path}: oczekiwano mapy z listą pod kluczem 'projects'"
                )
            raw = data["projects"]
            # Wpis inny niż mapa (np. goły napis w liście) wywracał się dopiero u wołającego
            # surowym ``AttributeError`` na ``entry.get`` — czyli jak defekt kodu, a nie jak
            # uszkodzone dane, którymi jest.
            entries: list[dict[str, Any]] = [item for item in raw if isinstance(item, dict)]
            if len(entries) != len(raw):
                raise ProjectsRegistryError(
                    f"{self._registry_path}: każda pozycja 'projects' musi być mapą klucz-wartość"
                )
            self._cache = (fingerprint, entries)
            return entries
