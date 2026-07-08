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

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from workmate.core.domain.models import Project, ProjectStatusRecord
from workmate.core.errors import RepositoryError


class ProjectsRegistryError(RepositoryError):
    """Rejestr projektów jest nieczytelny lub niezgodny ze schematem."""


class YamlProjectsRepository:
    """Czyta rejestr projektów z pliku YAML (ładowanie leniwe, przy każdym wywołaniu)."""

    def __init__(self, registry_path: Path) -> None:
        self._registry_path = registry_path

    def all(self) -> list[Project]:
        return [
            Project(
                key=entry["key"],
                company=entry.get("company", ""),
                name=entry["name"],
                description=entry.get("description", ""),
            )
            for entry in self._entries()
        ]

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
            raise ProjectsRegistryError(
                f"Rejestr projektów nie istnieje: {self._registry_path}"
            )
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
        return data["projects"]
