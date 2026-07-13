"""Testy repozytorium projektów opartego na rejestrze YAML (parsowanie, błędy, cache)."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest import mock

import pytest
import yaml

from workmate.adapters.outbound.yaml_projects_repo import (
    ProjectsRegistryError,
    YamlProjectsRepository,
)

REGISTRY = """projects:
  - key: scada-integration
    company: mpwik
    name: Integracja SCADA MPWiK
    description: Integracja MPWiK
    status: active
    health: green
    phase: Faza 1
    summary: Prace w toku
    last_updated: 2025-06-26
  - key: workmate
    company: biap
    name: WorkMate
    description: Asystent wiedzy
    status: active
    health: yellow
    phase: Faza 1
    summary: Testy i wdrożenie
    last_updated: 2025-06-24
"""


def _registry(tmp_path: Path, content: str = REGISTRY) -> Path:
    path = tmp_path / "registry.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_all_returns_projects(tmp_path: Path):
    repo = YamlProjectsRepository(_registry(tmp_path))

    projects = repo.all()

    assert {p.key for p in projects} == {"scada-integration", "workmate"}
    # Firma (company) jest wczytywana z rejestru.
    assert {p.company for p in projects} == {"mpwik", "biap"}


def test_get_is_case_insensitive(tmp_path: Path):
    repo = YamlProjectsRepository(_registry(tmp_path))

    project = repo.get("SCADA-Integration")

    assert project is not None
    assert project.name == "Integracja SCADA MPWiK"
    assert project.company == "mpwik"


def test_status_record_parsed(tmp_path: Path):
    repo = YamlProjectsRepository(_registry(tmp_path))

    record = repo.status_record("workmate")

    assert record is not None
    assert record.health == "yellow"
    assert record.last_updated == date(2025, 6, 24)


def test_unknown_project_returns_none(tmp_path: Path):
    repo = YamlProjectsRepository(_registry(tmp_path))

    assert repo.get("nieznany") is None
    assert repo.status_record("nieznany") is None


def test_missing_registry_raises(tmp_path: Path):
    repo = YamlProjectsRepository(tmp_path / "brak.yaml")

    with pytest.raises(ProjectsRegistryError):
        repo.all()


def test_malformed_registry_raises(tmp_path: Path):
    path = _registry(tmp_path, "to: nie jest lista projektów\n")
    repo = YamlProjectsRepository(path)

    with pytest.raises(ProjectsRegistryError):
        repo.all()


# --- Cache parsowania z unieważnianiem fingerprintem ----------------------------


def test_all_get_and_status_parse_registry_exactly_once(tmp_path: Path):
    """``all()`` + ``get()`` + ``status_record()`` po kolei → JEDNO parsowanie YAML.

    Dowodzi eliminacji re-parsu przy cache oraz braku podwójnego parse w drodze
    ``get_project_status`` (get + status_record na tym samym rejestrze).
    """
    repo = YamlProjectsRepository(_registry(tmp_path))

    with mock.patch(
        "workmate.adapters.outbound.yaml_projects_repo.yaml.safe_load",
        wraps=yaml.safe_load,
    ) as spy:
        repo.all()
        repo.get("workmate")
        repo.status_record("workmate")
        assert spy.call_count == 1


def test_modified_registry_is_reparsed_on_next_call(tmp_path: Path):
    """Ręczna edycja rejestru (inny rozmiar) unieważnia cache — kolejne wywołanie widzi zmianę."""
    path = _registry(tmp_path)
    repo = YamlProjectsRepository(path)

    assert {p.key for p in repo.all()} == {"scada-integration", "workmate"}

    path.write_text(
        REGISTRY
        + "  - key: enerkom\n"
        "    company: enerkom\n"
        "    name: Enerkom\n"
        "    description: Nowy projekt\n"
        "    status: active\n"
        "    health: green\n"
        "    phase: Faza 1\n"
        "    summary: Start\n"
        "    last_updated: 2025-07-01\n",
        encoding="utf-8",
    )

    assert {p.key for p in repo.all()} == {"scada-integration", "workmate", "enerkom"}
