"""Silnik Docker menedżera (ADR infra 0012) — kształt żądania ``docker run`` bez demona.

Silnik nie „uruchamia" polecenia, tylko wypełnia STAŁY szablon: jedyną zmienną z niezaufanego
wejścia
jest ``subpath`` (== scope), lądujący w ``VolumeOptions.Subpath``. Tu pilnujemy właśnie tego — że
utwardzenia (brak sieci, read-only, no-new-privileges, uid 10001) są w ciele, a scope trafia do pola
JSON, nie do wiersza poleceń. Prawdziwy ``docker.sock`` ma pokrycie w obrazie/integracji, nie tutaj.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fcntl", reason="silnik Docker jest POSIX-only (gniazdo unix docker.sock)")

from workmate.adapters.outbound.docker_engine import (  # noqa: E402
    DockerHttpEngine,
    DockerRunTemplate,
)
from workmate.core.ports.exec_manager import ContainerSpec  # noqa: E402

_SCOPE = f"teams-graph/{'a' * 32}"


def _spec() -> ContainerSpec:
    return ContainerSpec(
        name=f"workmate-exec-teams-graph-{'a' * 32}",
        scope=_SCOPE,
        subpath=_SCOPE,
        labels={"workmate.exec.managed": "1", "workmate.exec.scope": _SCOPE},
    )


def _engine(**template_kwargs) -> DockerHttpEngine:
    template = DockerRunTemplate(
        image="workmate:1.9.0-deploy",
        scratchpad_volume="workmate-scratchpad",
        sock_volume="workmate-exec-sock",
        data_volume="workmate-data",
        **template_kwargs,
    )
    return DockerHttpEngine(template)


def test_body_niesie_utwardzenia_i_stały_szablon():
    """Brak sieci, read-only, no-new-privileges, uid 10001 i obraz są STAŁE — nie od modelu."""
    body = _engine()._create_body(_spec())  # noqa: SLF001 — sonda kształtu żądania od środka

    assert body["Image"] == "workmate:1.9.0-deploy"
    assert body["Cmd"] == ["workmate-exec"]
    assert body["User"] == "10001:10001"
    host = body["HostConfig"]
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True
    assert host["SecurityOpt"] == ["no-new-privileges"]
    assert host["AutoRemove"] is False
    assert host["RestartPolicy"] == {"Name": "no"}


def test_subpath_scope_ląduje_w_montazu_a_nie_w_poleceniu():
    """Jedyna zmienna z niezaufanego wejścia idzie do ``VolumeOptions.Subpath`` obu wolumenów."""
    body = _engine()._create_body(_spec())  # noqa: SLF001
    mounts = {m["Target"]: m for m in body["HostConfig"]["Mounts"]}

    scratch = mounts[f"/home/scratchpad/{_SCOPE}"]
    assert scratch["Source"] == "workmate-scratchpad"
    assert scratch["VolumeOptions"]["Subpath"] == _SCOPE

    sock = mounts["/var/run/workmate"]
    assert sock["VolumeOptions"]["Subpath"] == _SCOPE

    system = mounts["/mnt/system"]
    assert system["Source"] == "workmate-data"
    assert system["ReadOnly"] is True


def test_baza_wiedzy_jest_montowana_read_only_i_bez_zapisywalnej_sciezki():
    """Wolumen bazy wiedzy wchodzi WYŁĄCZNIE jako ``/mnt/system:ro`` — granica 0007 nietknięta."""
    body = _engine()._create_body(_spec())  # noqa: SLF001
    targets = {m["Target"] for m in body["HostConfig"]["Mounts"]}

    assert "/mnt/system" in targets
    assert "/app/data" not in targets  # brak drugiej, zapisywalnej ścieżki do notatek


def test_skills_montowane_tylko_gdy_podano_zrodlo():
    """Procedury (bind hosta) wchodzą jako ``/mnt/skills:ro`` tylko, gdy compose poda ścieżkę."""
    bez = _engine()._create_body(_spec())  # noqa: SLF001
    assert all(m["Target"] != "/mnt/skills" for m in bez["HostConfig"]["Mounts"])

    z = _engine(skills_source="/opt/sufler/skills")._create_body(_spec())  # noqa: SLF001
    skills = next(m for m in z["HostConfig"]["Mounts"] if m["Target"] == "/mnt/skills")
    assert skills["Type"] == "bind"
    assert skills["Source"] == "/opt/sufler/skills"
    assert skills["ReadOnly"] is True


def test_notes_dir_jest_w_env_wykonawcy():
    """``workmate-search`` w powłoce czyta korzeń bazy wiedzy z env — bez niego kończy się
    błędem."""
    body = _engine()._create_body(_spec())  # noqa: SLF001

    assert "WORKMATE_NOTES_DIR=/mnt/system/notes" in body["Env"]
