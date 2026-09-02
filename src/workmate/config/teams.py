"""Ustawienia drzwi Teams (Agents SDK / Bot Framework)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from workmate.config._env import _bool_from_env, _int_from_env, _path_from_env


@dataclass(frozen=True)
class TeamsSettings:
    """Konfiguracja drzwi Teams (Faza 2) — osobny proces od serwera MCP.

    Tryb anonimowy (bez ``app_id``/``app_password``) służy lokalnemu testowi w Bot
    Framework Emulator bez Azure. Tryb uwierzytelniony wymaga tożsamości
    single-tenant z Azure (``app_id`` + ``app_password`` + ``tenant_id``) — brak
    ``tenant_id`` przy single-tenant to klasyczna przyczyna 401 w Teams.
    """

    app_id: str = ""
    # Sekret: repr=False, żeby przypadkowe zalogowanie obiektu/traceback nie ujawniło hasła.
    app_password: str = field(default="", repr=False)
    tenant_id: str = ""
    bind_host: str = "localhost"
    bind_port: int = 3978
    anonymous_auth: bool = False
    # Mapa tożsamości (AAD id → członek pionu) — TEN SAM format co
    # ``WORKMATE_TEAMS_GRAPH_IDENTITIES`` i zwykle ten sam plik: obie pary drzwi Teams rozpoznają
    # tego samego człowieka po tym samym ``aad_user_id``, bo Bot Framework niesie go w
    # ``activity.from.aadObjectId``, a Graph w ``from.user.id``. Osobna zmienna, nie współdzielona
    # z tamtymi drzwiami, bo procesy bywają wdrażane osobno (flota wozi dziś tylko `teams-graph`).
    identities: Path = Path()
    # Bramka członkostwa ODCZYTU bazy wiedzy (ADR 0062), bliźniacza do
    # ``WORKMATE_TEAMS_GRAPH_ENABLE_NOTE_READ_AUTHZ``. Domyślnie OFF (bezpieczny rollout: włączenie
    # przy niekompletnej mapie odcina realnych członków pionu), włączona WYMAGA mapy tożsamości.
    enable_note_read_authz: bool = False

    @classmethod
    def from_env(cls) -> TeamsSettings:
        return cls(
            app_id=os.environ.get("WORKMATE_TEAMS_APP_ID", ""),
            app_password=os.environ.get("WORKMATE_TEAMS_APP_PASSWORD", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_TENANT_ID", ""),
            bind_host=os.environ.get("WORKMATE_TEAMS_BIND_HOST", "localhost"),
            bind_port=_int_from_env("WORKMATE_TEAMS_PORT", 3978),
            anonymous_auth=_bool_from_env("WORKMATE_TEAMS_ANONYMOUS", default=False),
            identities=_path_from_env("WORKMATE_TEAMS_IDENTITIES", Path()),
            enable_note_read_authz=_bool_from_env(
                "WORKMATE_TEAMS_ENABLE_NOTE_READ_AUTHZ", default=False
            ),
        )

    def validate(self) -> None:
        """Twardy błąd startowy, gdy konfiguracja jest niebezpieczna albo niepełna.

        Lepiej nie wystartować niż ruszyć bez działającego uwierzytelniania. Do
        lokalnego testu w Emulatorze użyj ``WORKMATE_TEAMS_ANONYMOUS=true`` — ale
        tylko na loopbacku, żeby nie wystawić nieuwierzytelnionego bota na sieć.
        """
        if self.enable_note_read_authz and not self.identities.is_file():
            # Bramka odczytu (ADR 0062) bez mapy tożsamości nie ma po czym rozpoznać nadawcy —
            # fail-fast, jak na drzwiach delegowanych. Sprawdzane PRZED gałęzią anonimową, bo
            # Emulator też nadaje ``aadObjectId`` i bramka ma tam działać tak samo.
            raise ValueError(
                "WORKMATE_TEAMS_ENABLE_NOTE_READ_AUTHZ=true wymaga WORKMATE_TEAMS_IDENTITIES "
                "= ścieżka do mapy tożsamości (członkostwo autoryzuje odczyt, ADR 0062); "
                f"brak pliku: {self.identities}."
            )
        loopback = ("localhost", "127.0.0.1", "::1")
        if self.anonymous_auth:
            if self.bind_host not in loopback:
                raise ValueError(
                    "Tryb anonimowy (WORKMATE_TEAMS_ANONYMOUS=true) jest dozwolony "
                    "tylko na loopbacku (localhost/127.0.0.1/::1), a "
                    f"WORKMATE_TEAMS_BIND_HOST={self.bind_host!r}. Nie wystawiaj "
                    "nieuwierzytelnionego bota na sieć."
                )
            return
        missing = [
            name
            for name, value in (
                ("WORKMATE_TEAMS_APP_ID", self.app_id),
                ("WORKMATE_TEAMS_APP_PASSWORD", self.app_password),
                ("WORKMATE_TEAMS_TENANT_ID", self.tenant_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "Drzwi Teams wymagają tożsamości single-tenant: brakuje "
                + ", ".join(missing)
                + ". Do lokalnego testu w Emulatorze ustaw WORKMATE_TEAMS_ANONYMOUS=true."
            )
