"""Ustawienia wypychania wiadomości do Teams (proaktywny DM, token push)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from workmate.config._env import _bool_from_env, _list_from_env, _path_from_env
from workmate.config.teams_graph import _DEFAULT_TEAMS_GRAPH_CACHE

# Zakresy delegowane proaktywnego push do Teams (ADR 0022): tworzenie/pisanie czatu 1:1 oraz
# wysyłka na kanał. MSAL dokłada offline_access/openid/profile sam (nie wpisujemy ich).
_DEFAULT_TEAMS_PUSH_SCOPES = (
    "Chat.Create",
    "Chat.ReadWrite",
    "ChatMessage.Send",
    "ChannelMessage.Send",
    "User.Read",
    # Lista członków zespołu dla drzwi kart czasu (ADR 0035). NIE wymaga nowej zgody admina:
    # to ta sama rejestracja aplikacji i ten sam cache MSAL co ``Powiadomienia_teams``, gdzie
    # scope jest skonsentowany od 2026-07-14 — tu po prostu też o niego prosimy.
    "TeamMember.Read.All",
)


@dataclass(frozen=True)
class TeamsPushSettings:
    """Konfiguracja proaktywnego push do Teams (dual-target, ADR 0022) — notifier zdarzeń → Teams.

    Tożsamość = zalogowany użytkownik (device-code MSAL, jak ``teams_graph``); może współdzielić
    ten sam ``token_cache_path`` (jedno logowanie). Sam obiekt nie trzyma sekretu (sekretem jest
    CACHE tokenu na dysku). OBA cele są konfigurowalne (decyzja użytkownika): czat 1:1 i kanał —
    włączane niezależnie flagami ``enable_chat``/``enable_channel``. Gdy oba wyłączone, notifier
    nie startuje (drzwi GitHub działają wtedy jako ingest-only). ``enable_channel_threading``
    (ADR 0024, domyślnie OFF) dokłada zdarzenia tego samego issue/PR do JEDNEGO wątku na kanale
    (zamiast nowego roota za każdym razem); wymaga włączonego celu kanału.
    """

    client_id: str = ""
    tenant_id: str = ""
    scopes: tuple[str, ...] = _DEFAULT_TEAMS_PUSH_SCOPES
    token_cache_path: Path = _DEFAULT_TEAMS_GRAPH_CACHE
    chat_user_id: str = ""
    team_id: str = ""
    channel_id: str = ""
    enable_chat: bool = False
    enable_channel: bool = False
    enable_channel_threading: bool = False

    @property
    def authority(self) -> str:
        """URL authority MSAL dla aplikacji single-tenant (z ``tenant_id``)."""
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def enabled(self) -> bool:
        """Czy notifier ma w ogóle wystartować (włączony co najmniej jeden cel)."""
        return self.enable_chat or self.enable_channel

    @classmethod
    def from_env(cls) -> TeamsPushSettings:
        return cls(
            client_id=os.environ.get("WORKMATE_TEAMS_PUSH_CLIENT_ID", ""),
            tenant_id=os.environ.get("WORKMATE_TEAMS_PUSH_TENANT_ID", ""),
            scopes=_list_from_env("WORKMATE_TEAMS_PUSH_SCOPES", _DEFAULT_TEAMS_PUSH_SCOPES),
            token_cache_path=_path_from_env(
                "WORKMATE_TEAMS_PUSH_TOKEN_CACHE", _DEFAULT_TEAMS_GRAPH_CACHE
            ),
            chat_user_id=os.environ.get("WORKMATE_TEAMS_PUSH_CHAT_USER_ID", ""),
            team_id=os.environ.get("WORKMATE_TEAMS_PUSH_TEAM_ID", ""),
            channel_id=os.environ.get("WORKMATE_TEAMS_PUSH_CHANNEL_ID", ""),
            enable_chat=_bool_from_env("WORKMATE_TEAMS_PUSH_ENABLE_CHAT", default=False),
            enable_channel=_bool_from_env("WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL", default=False),
            enable_channel_threading=_bool_from_env(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING", default=False
            ),
        )

    def validate(self) -> None:
        """Twardy błąd startu, gdy włączony cel jest niekompletny (brak id celu/aplikacji).

        Gdy notifier wyłączony (żaden cel), nie wymagamy niczego — drzwi GitHub są ingest-only.
        """
        if not self.enabled:
            return
        if not self.client_id or not self.tenant_id:
            raise ValueError(
                "Proaktywny push do Teams wymaga tożsamości aplikacji: ustaw "
                "WORKMATE_TEAMS_PUSH_CLIENT_ID i WORKMATE_TEAMS_PUSH_TENANT_ID."
            )
        if self.enable_chat and not self.chat_user_id:
            raise ValueError(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHAT wymaga WORKMATE_TEAMS_PUSH_CHAT_USER_ID "
                "(AAD user id adresata)."
            )
        if self.enable_channel and (not self.team_id or not self.channel_id):
            raise ValueError(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL wymaga WORKMATE_TEAMS_PUSH_TEAM_ID "
                "i WORKMATE_TEAMS_PUSH_CHANNEL_ID."
            )
        # Wątkowanie dotyczy WYŁĄCZNIE kanału (czat 1:1 nie ma wątków) — bez celu kanału byłoby
        # martwe; odrzucamy cichą, sprzeczną konfigurację (ADR 0024, Faza 3).
        if self.enable_channel_threading and not self.enable_channel:
            raise ValueError(
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL_THREADING wymaga "
                "WORKMATE_TEAMS_PUSH_ENABLE_CHANNEL=true (wątki są tylko na kanale)."
            )
