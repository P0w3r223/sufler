"""Silnik Docker menedżera (ADR infra 0012) — kształt żądania ``docker run`` bez demona.

Silnik nie „uruchamia" polecenia, tylko wypełnia STAŁY szablon: jedyną zmienną z niezaufanego
wejścia
jest ``subpath`` (== scope), lądujący w ``VolumeOptions.Subpath``. Tu pilnujemy właśnie tego — że
utwardzenia (brak sieci, read-only, no-new-privileges, uid 10001) są w ciele, a scope trafia do pola
JSON, nie do wiersza poleceń. Prawdziwy ``docker.sock`` ma pokrycie w obrazie/integracji, nie tutaj.
"""

from __future__ import annotations

import http.client

import pytest

pytest.importorskip("fcntl", reason="silnik Docker jest POSIX-only (gniazdo unix docker.sock)")

from workmate.adapters.outbound import docker_engine  # noqa: E402
from workmate.adapters.outbound.docker_engine import (  # noqa: E402
    DockerHttpEngine,
    DockerRunTemplate,
)
from workmate.core.errors import ExecManagerError  # noqa: E402
from workmate.core.ports.exec_manager import ContainerSpec  # noqa: E402
from urllib.parse import unquote

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


def test_body_niesie_granice_zuzycia_wykonawcy():
    """ADR infra 0013: `HostConfig` niósł dotąd `NetworkMode`/`ReadonlyRootfs`/`SecurityOpt` i NIC
    o zużyciu. Bez `PidsLimit` bomba widłowa wyczerpuje tablicę procesów HOSTA — kontener swojej
    nie ma. Sonda pilnuje KOMPLETU: lista podana częściowo wygląda jak granica, a przepuszcza to,
    czego nie wymienia."""
    host = _engine()._create_body(_spec())["HostConfig"]  # noqa: SLF001

    assert host["CapDrop"] == ["ALL"]
    assert host["PidsLimit"] == 128
    assert host["Memory"] == 512 * 1024 * 1024
    assert host["NanoCpus"] == 1_000_000_000
    ulimity = {u["Name"]: u for u in host["Ulimits"]}
    assert ulimity["fsize"]["Soft"] == 5 * 1024 * 1024
    assert ulimity["nofile"]["Soft"] == 256


def test_ulimity_maja_rowny_sufit_miekki_i_twardy():
    """Miękki niższy od twardego proces w kontenerze podniósłby sobie sam — czyli granica
    obowiązywałaby dokładnie do chwili, w której komuś zaczęłaby przeszkadzać."""
    host = _engine()._create_body(_spec())["HostConfig"]  # noqa: SLF001

    for ulimit in host["Ulimits"]:
        assert ulimit["Soft"] == ulimit["Hard"], ulimit["Name"]


def test_sufit_pliku_jedzie_do_wykonawcy_takze_zmienna_srodowiskowa():
    """Dwa piętra tej samej granicy mają brać liczbę z JEDNEGO miejsca.

    `Ulimits` zabija proces, ale daje modelowi sam kod `-25`; `exec_server` zakłada ten sam sufit
    `ulimitem`, żeby przetłumaczyć przekroczenie na zdanie. Rozjazd tych dwóch wartości dałby
    komunikat mówiący o innej liczbie niż ta, która realnie ucięła zapis.
    """
    body = _engine(max_file_mb=7)._create_body(_spec())  # noqa: SLF001

    assert "WORKMATE_EXEC_MAX_FILE_MB=7" in body["Env"]
    ulimity = {u["Name"]: u for u in body["HostConfig"]["Ulimits"]}
    assert ulimity["fsize"]["Soft"] == 7 * 1024 * 1024


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


class _FakeResponse:
    """Odpowiedź ``http.client`` o zadanym statusie i surowej treści."""

    def __init__(self, status: int, raw: bytes) -> None:
        self.status = status
        self._raw = raw

    def read(self) -> bytes:
        return self._raw


class _FakeConnection:
    """Połączenie, które zamiast rozmawiać z demonem oddaje zadaną odpowiedź albo wyjątek."""

    def __init__(self, *, raises: Exception | None = None, status: int = 200, raw: bytes = b"{}"):
        self._raises = raises
        self._status = status
        self._raw = raw

    def request(self, method, path, body=None, headers=None) -> None:  # noqa: ARG002
        # Ścieżkę ZAPAMIĘTUJEMY. Atrapa przyjmowała dotąd dowolną i nigdy jej nie oglądała —
        # dlatego `list_managed` z niezakodowanym `filters` przechodziło cały zestaw, a padało
        # przy PIERWSZYM `reconcile` na produkcji.
        self.ostatnia_sciezka = path
        return None

    def getresponse(self) -> _FakeResponse:
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(self._status, self._raw)

    def close(self) -> None:
        return None


def _with_connection(monkeypatch, connection: _FakeConnection) -> DockerHttpEngine:
    monkeypatch.setattr(
        docker_engine, "_UnixHTTPConnection", lambda socket_path, *, timeout: connection
    )
    return _engine()


def test_uciety_dialog_http_wraca_jako_ExecManagerError(monkeypatch):
    """``http.client`` sygnalizuje zerwaną odpowiedź WŁASNYMI wyjątkami, nie ``OSError``.

    ``BadStatusLine``/``IncompleteRead`` przelatywały obok ``except OSError`` i wywracały turę,
    zamiast wrócić jako niedostępność wykonawcy — a ``ManagedCommandRunner`` degraduje wyłącznie
    ``ExecManagerError``.
    """
    engine = _with_connection(
        monkeypatch, _FakeConnection(raises=http.client.BadStatusLine("smieci"))
    )

    with pytest.raises(ExecManagerError):
        engine.list_managed()


def test_zepsuty_json_od_demona_wraca_jako_ExecManagerError(monkeypatch):
    """``JSONDecodeError`` to ``ValueError``, nie ``OSError`` — też przelatywał obok osłony."""
    engine = _with_connection(monkeypatch, _FakeConnection(raw=b"to nie jest JSON"))

    with pytest.raises(ExecManagerError):
        engine.list_managed()


def test_odmowa_docker_api_przy_starcie_wraca_jako_ExecManagerError(monkeypatch):
    """Status != 2xx z ``/containers/create`` wychodził surowym ``_DockerApiError``.

    ``remove`` tłumaczył go od początku; ``run`` — nie, więc odmowa demona (brak obrazu, konflikt
    nazwy) kładła turę agenta zamiast wrócić wynikiem polecenia z kodem ``-1``.
    """
    engine = _with_connection(
        monkeypatch, _FakeConnection(status=500, raw=b'{"message": "no such image"}')
    )

    with pytest.raises(ExecManagerError):
        engine.run(_spec())


def test_filtry_list_managed_przechodza_walidacje_sciezki_http(monkeypatch):
    """`json.dumps` wstawia spację po dwukropku, a `http.client` odrzuca ją w ścieżce.

    Objaw był całkowity, nie brzegowy: `list_managed` rzucało ZAWSZE, więc `reconcile` nie mógł
    wypisać ani jednego wykonawcy i sprzątanie nigdy nie biegło. `ensure` działało bez zarzutu,
    więc na produkcji wyglądało to na sprawny menedżer — przy kontenerach narastających bez
    końca, po jednym na rozmowę.

    Sonda pyta o to, o co pyta `http.client`: żadnego znaku z zakresu [\x00-\x20\x7f].
    Sprawdzenie „czy jest spacja" byłoby węższe niż kontrola, która to odrzuca.
    """
    import re

    polaczenie = _FakeConnection(raw=b"[]")
    engine = _with_connection(monkeypatch, polaczenie)

    engine.list_managed()

    sciezka = polaczenie.ostatnia_sciezka
    zakazane = getattr(
        http.client, "_contains_disallowed_url_pchar_re", re.compile("[\x00-\x20\x7f]")
    )
    assert not zakazane.search(sciezka), f"http.client odrzuci tę ścieżkę: {sciezka!r}"
    # Filtr ma nadal DZIAŁAĆ, nie tylko przechodzić walidację — etykieta musi w nim być.
    assert "workmate.exec.managed" in unquote(sciezka), sciezka
