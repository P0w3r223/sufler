"""``ContainerEngine`` nad Docker Engine API po gnieździe unix (ADR infra 0012).

To jedyny komponent floty rozmawiający z ``docker.sock`` — równoważnikiem roota na hoście. Cała
niebezpieczna moc jest tu zwężona do trzech czasowników i JEDNEGO stałego szablonu ``docker run``:
obraz, ``command``, ``network_mode: none``, ``read_only``, ``no-new-privileges``, ``user 10001`` i
montaże są niezmienne, a jedyna zmienna pochodząca z niezaufanego wejścia — ``subpath`` (zwalidowany
scope) — trafia do pola ``VolumeOptions.Subpath`` żądania JSON, nigdy do wiersza poleceń. Nie ma
więc
drogi wstrzyknięcia: menedżer nie „uruchamia" polecenia, tylko wypełnia szablon.

Transport jest stdlib (``http.client`` nad ``AF_UNIX``), bez SDK Dockera: zależność mniej, a
powierzchnia — węższa. POSIX-only jak reszta warstwy wykonawcy (gniazda unix nie istnieją na
Windows).
"""

from __future__ import annotations

import http.client
import json
import logging
import socket
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from workmate.core.errors import ExecManagerError

if TYPE_CHECKING:
    from workmate.core.ports.exec_manager import ContainerSpec, RunningExecutor

if sys.platform == "win32":  # pragma: no cover — nieosiągalne w obrazie
    raise ImportError("DockerHttpEngine działa wyłącznie na POSIX (gniazdo unix docker.sock).")

logger = logging.getLogger(__name__)

# Wersja API przypięta jawnie: żądania idą pod ``/v<API>/...`` zamiast pod nieokreślony „najnowszy",
# żeby zachowanie nie zależało od wersji demona pod spodem. 1.45 wprowadziło
# ``VolumeOptions.Subpath``
# — bez niego montaż samego podkatalogu (istota izolacji ADR 0012) nie istnieje.
_API_VERSION = "v1.45"
_LABEL_MANAGED = "workmate.exec.managed"
_LABEL_SCOPE = "workmate.exec.scope"


@dataclass(frozen=True)
class DockerRunTemplate:
    """Stały szablon wykonawcy — wszystko, czego menedżer NIE bierze od modelu ani ze scope'a.

    Nazwy wolumenów i obraz stoją tu (z konfiguracji menedżera), a nie w kodzie: paczka pod-bija tag
    obrazu przy każdym wydaniu, a wolumeny nazywa compose. ``skills_source`` jest opcjonalny —
    procedury (ADR 0005) są bindem hosta, więc menedżer dostaje ich ścieżkę tylko, gdy compose ją
    poda.
    """

    image: str
    scratchpad_volume: str
    sock_volume: str
    data_volume: str
    notes_dir: str = "/mnt/system/notes"
    skills_source: str | None = None
    command: tuple[str, ...] = ("workmate-exec",)
    user: str = "10001:10001"
    tmpfs_size: str = "size=64m"
    extra_env: tuple[str, ...] = field(default_factory=tuple)
    # ── Granice zużycia wykonawcy (ADR infra 0013) ────────────────────────────────────────
    # Drugie piętro obrony, nad `ulimit` z `exec_server`. Tamto daje czytelny komunikat, ale
    # zależy od tego, czy wykonawca faktycznie założył limity; te obowiązują KAŻDY proces
    # kontenera — także taki, który powstał drogą, o której `exec_server` nie wie.
    memory_mb: int = 512
    pids_limit: int = 128
    cpu_limit: float = 1.0
    max_file_mb: int = 5
    max_open_files: int = 256


class DockerHttpEngine:
    """Silnik kontenerów po ``docker.sock``: postaw/usuń/wypisz wykonawców ze stałego szablonu."""

    def __init__(
        self,
        template: DockerRunTemplate,
        *,
        socket_path: str = "/var/run/docker.sock",
        timeout_s: float = 30.0,
    ) -> None:
        self._template = template
        self._socket_path = socket_path
        self._timeout_s = timeout_s

    def run(self, spec: ContainerSpec) -> str:
        """Utwórz i wystartuj wykonawcę scope'a; zwróć id kontenera.

        Idempotencja jest po stronie MENEDŻERA (rejestr scope→kontener), więc gdyby kontener o tej
        nazwie już istniał (ślad po nieczystym zamknięciu), usuwamy go przed utworzeniem — inaczej
        ``create`` odbiłby się o konflikt nazwy zamiast dać czysty start.
        """
        self._remove_by_name(spec.name)
        body = self._create_body(spec)
        try:
            created = self._request("POST", f"/containers/create?name={spec.name}", body)
            container_id = str(created["Id"])
            self._request("POST", f"/containers/{container_id}/start", None)
        except _DockerApiError as exc:
            # Jak w ``remove``: odmowa Docker API ma wyjść jako ``ExecManagerError``, bo TO jest
            # błąd, który ``ManagedCommandRunner`` umie zamienić w wynik polecenia. Surowy
            # ``_DockerApiError`` przelatywał przez menedżera i wywracał turę agenta.
            raise ExecManagerError(f"nie udało się postawić wykonawcy {spec.name}: {exc}") from exc
        except (KeyError, TypeError) as exc:
            raise ExecManagerError(
                f"Docker API nie zwróciło id kontenera dla {spec.name}: {exc}"
            ) from exc
        return container_id

    def remove(self, container_id: str) -> None:
        """Usuń kontener wymuszenie. 404 (już go nie ma) to cel osiągnięty, nie błąd."""
        try:
            self._request("DELETE", f"/containers/{container_id}?force=true", None)
        except _DockerApiError as exc:
            if exc.status == 404:
                return
            raise ExecManagerError(
                f"usunięcie kontenera {container_id[:12]} nie powiodło się: {exc}"
            ) from exc

    def list_managed(self) -> list[RunningExecutor]:
        """Wypisz żywe kontenery z etykietą zarządzania — do reconcile."""
        from workmate.core.ports.exec_manager import RunningExecutor

        # `quote`, bo `json.dumps` wstawia SPACJĘ po dwukropku, a `http.client` odrzuca ścieżkę
        # z jakimkolwiek znakiem z zakresu [\x00-\x20\x7f] (`InvalidURL`). Bez kodowania
        # `list_managed` rzucało więc ZAWSZE — czyli `reconcile` nie mógł wypisać ani jednego
        # wykonawcy i sprzątanie nigdy nie biegło. Objaw na produkcji 2026-08-20:
        #   ExecManagerError: docker.sock niedostępny (/var/run/docker.sock):
        #   URL can't contain control characters. '/v1.45/containers/json?filters={"label": [...]}'
        # `ensure` działało bez zarzutu, więc awaria była WYŁĄCZNIE po stronie odzysku: kontenery
        # wykonawców narastały bez końca, jeden na rozmowę, i nic ich nie zdejmowało.
        # `safe=""` — kodujemy też `{`, `}`, `"` i `[`, nie licząc na tolerancję demona.
        filters = quote(json.dumps({"label": [f"{_LABEL_MANAGED}=1"]}), safe="")
        try:
            raw = self._request("GET", f"/containers/json?filters={filters}", None)
        except _DockerApiError as exc:
            raise ExecManagerError(f"nie udało się wypisać wykonawców: {exc}") from exc
        result: list[RunningExecutor] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict) or "Id" not in item:
                continue
            labels = item.get("Labels") or {}
            result.append(
                RunningExecutor(
                    container_id=str(item["Id"]),
                    scope=str(labels.get(_LABEL_SCOPE, "")),
                )
            )
        return result

    # ── budowa żądania ────────────────────────────────────────────────────────

    def _create_body(self, spec: ContainerSpec) -> dict[str, Any]:
        """Złóż ciało ``/containers/create`` ze STAŁEGO szablonu + pól scope'a. Bez powłoki, bez
        modelu."""
        template = self._template
        mounts: list[dict[str, Any]] = [
            # Podkatalog scope'a montujemy pod ŚCIEŻKĄ, którą aplikacja i tak liczy jako `cwd`
            # (`/home/scratchpad/<scope>`), a nie w korzeniu — dzięki temu `cwd` polecenia jest
            # ważny bez tłumaczenia układów, a `build_shell_catalog` nie musi wiedzieć o montażu.
            # Wykonawca widzi WYŁĄCZNIE ten podkatalog: `/home/scratchpad/<inny-scope>` nie istnieje
            # w jego drzewie, więc ścieżka bezwzględna nie ma dokąd sięgnąć (istota izolacji 0012).
            {
                "Type": "volume",
                "Source": template.scratchpad_volume,
                "Target": f"/home/scratchpad/{spec.subpath}",
                "VolumeOptions": {"Subpath": spec.subpath},
            },
            {
                "Type": "volume",
                "Source": template.sock_volume,
                "Target": "/var/run/workmate",
                "VolumeOptions": {"Subpath": spec.subpath},
            },
            {
                "Type": "volume",
                "Source": template.data_volume,
                "Target": "/mnt/system",
                "ReadOnly": True,
            },
        ]
        if template.skills_source is not None:
            mounts.append(
                {
                    "Type": "bind",
                    "Source": template.skills_source,
                    "Target": "/mnt/skills",
                    "ReadOnly": True,
                }
            )
        # Sufity plikowe idą do wykonawcy TAKŻE zmienną środowiskową, choć `Ulimits` wyżej
        # nakłada je już na kontener. To nie jest dublowanie przez nieuwagę: `exec_server`
        # zakłada je jeszcze raz `ulimitem`, żeby móc PRZETŁUMACZYĆ przekroczenie na zdanie dla
        # modelu (sam `Ulimits` daje mu kod `-25` i nic więcej). Wartość ma być jedna, więc
        # jedzie stąd, a nie z drugiego miejsca konfiguracji.
        env = [
            f"WORKMATE_NOTES_DIR={template.notes_dir}",
            f"WORKMATE_EXEC_MAX_FILE_MB={template.max_file_mb}",
            f"WORKMATE_EXEC_MAX_OPEN_FILES={template.max_open_files}",
            *template.extra_env,
        ]
        return {
            "Image": template.image,
            "Cmd": list(template.command),
            "User": template.user,
            "Env": env,
            "Labels": spec.labels,
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "SecurityOpt": ["no-new-privileges"],
                # Wykonawca biegnie jako 10001, na tylko-do-odczytu korzeniu, bez sieci: nie ma
                # czynności, do której zdolności byłyby mu potrzebne (ADR infra 0013).
                "CapDrop": ["ALL"],
                # Bomba widłowa umiera na granicy KONTENERA. Bez tego pola `:(){ :|:& };:`
                # wyczerpuje tablicę procesów HOSTA — kontener nie ma własnej.
                "PidsLimit": template.pids_limit,
                "Memory": template.memory_mb * 1024 * 1024,
                # RÓWNE `Memory` = swap wyłączony dla tego kontenera. Bez tego pola Docker daje
                # swapowi drugie tyle, więc `Memory: 512M` znaczyłoby 512 MB RAM PLUS 512 MB
                # swapu — czyli inną granicę, niż mówi liczba. Dziś host swapu nie ma i różnicy
                # nie widać; pole jest tu po to, żeby jego włączenie nie zmieniło po cichu limitu.
                "MemorySwap": template.memory_mb * 1024 * 1024,
                "NanoCpus": int(template.cpu_limit * 1_000_000_000),
                # Te same sufity, co `ulimit` w `exec_server`, ale niezależne od jego kodu.
                # `Soft` == `Hard`, żeby proces w kontenerze nie mógł podnieść sobie miękkiego.
                "Ulimits": [
                    {
                        "Name": "fsize",
                        "Soft": template.max_file_mb * 1024 * 1024,
                        "Hard": template.max_file_mb * 1024 * 1024,
                    },
                    {
                        "Name": "nofile",
                        "Soft": template.max_open_files,
                        "Hard": template.max_open_files,
                    },
                ],
                "Tmpfs": {"/tmp": template.tmpfs_size},
                "Mounts": mounts,
                "AutoRemove": False,
                "RestartPolicy": {"Name": "no"},
            },
        }

    def _remove_by_name(self, name: str) -> None:
        """Usuń ewentualny kontener o tej nazwie (ślad po nieczystym zamknięciu) — best-effort."""
        try:
            self._request("DELETE", f"/containers/{name}?force=true", None)
        except _DockerApiError as exc:
            if exc.status != 404:
                logger.warning("Nie udało się usunąć starego kontenera %s: %s", name, exc)

    # ── transport ─────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, body: dict[str, Any] | None) -> Any:
        """Jedno żądanie do Docker API po gnieździe unix; JSON w obie strony, błąd → wyjątek.

        Transport (brak gniazda, zerwane połączenie, zły JSON) i status != 2xx wracają jako wyjątek
        —
        to warstwa menedżera decyduje, co degradować (``remove`` łyka 404), a co podnieść wyżej.
        """
        conn = _UnixHTTPConnection(self._socket_path, timeout=self._timeout_s)
        try:
            payload = json.dumps(body).encode("utf-8") if body is not None else None
            headers = {"Host": "docker", "Accept": "application/json"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            conn.request(method, f"/{_API_VERSION}{path}", body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            if not 200 <= response.status < 300:
                raise _DockerApiError(response.status, raw.decode("utf-8", errors="replace"))
            if not raw:
                return None
            return json.loads(raw)
        except (OSError, http.client.HTTPException, json.JSONDecodeError) as exc:
            # ``http.client`` sygnalizuje uciętą/niezrozumiałą odpowiedź WŁASNYMI wyjątkami
            # (``BadStatusLine``, ``IncompleteRead``), a zepsuty JSON — ``JSONDecodeError``;
            # żaden z nich nie jest ``OSError``, więc dotąd przelatywały obok ``ExecManagerError``
            # i wywracały turę zamiast wrócić jako niedostępność wykonawcy (``CommandResult`` -1).
            raise ExecManagerError(f"docker.sock niedostępny ({self._socket_path}): {exc}") from exc
        finally:
            conn.close()


class _DockerApiError(Exception):
    """Docker API zwróciło status != 2xx. Niesie kod, żeby ``remove`` mógł zignorować 404."""

    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Docker API {status}: {body.strip()[:200]}")
        self.status = status


class _UnixHTTPConnection(http.client.HTTPConnection):
    """``HTTPConnection`` mówiące po gnieździe unix zamiast po TCP (Docker Engine API)."""

    def __init__(self, socket_path: str, *, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock
