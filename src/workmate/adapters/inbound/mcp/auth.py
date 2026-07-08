"""Uwierzytelnianie drzwi HTTP: bearer token per osoba (Bramka 3, ADR 0007).

Warstwa DRZWI, nie rdzeń — tożsamość jest atrybutem transportu, którego cztery
przypadki użycia odczytu nigdy nie widzą. Zgodnie z regułą zależności
``core ↛ adapters`` cały ten moduł żyje w adapterze wejściowym.

Tokeny są opaque (losowe); w magazynie trzymamy wyłącznie ich ``sha256``, a
porównanie jest stałoczasowe (``hmac.compare_digest``). Magazyn MUSI leżeć poza
katalogiem danych (``data/``), którego dotykają narzędzia — inaczej sekret
byłby w folderze indeksowanym (zasada przekrojowa roadmapy). Loader wymusza to
twardo przy starcie: zły albo źle położony magazyn = proces się nie uruchamia.

Middleware jest czystym ASGI (bez importu Starlette), więc jego jedyną zależnością
jest kontrakt ASGI, a testy napędzają je bez sieci.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from workmate.core.errors import WorkMateError

logger = logging.getLogger(__name__)

# sha256 w zapisie heksadecymalnym: dokładnie 64 znaki [0-9a-f] (po normalizacji).
_SHA256_RE = re.compile(r"[0-9a-f]{64}")

# Typy ASGI. Wartości scope/message są heterogeniczne z natury protokołu
# (bytes, str, listy par bajtów, zagnieżdżone słowniki), więc ``Any`` jest tu
# świadomym i standardowym wyborem dla warstwy transportu.
Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class TokenStoreError(WorkMateError):
    """Magazyn tokenów jest nieczytelny, wadliwy albo leży w zabronionym miejscu.

    To błąd *startowy* (konfiguracja wdrożenia), nie błąd runtime — proces ma się
    nie uruchomić, zamiast wystartować bez działającego uwierzytelniania.
    """


@dataclass(frozen=True)
class Principal:
    """Tożsamość ustalona z tokenu na drzwiach HTTP."""

    person: str
    scopes: tuple[str, ...]


class TokenVerifier:
    """Weryfikuje nagłówek ``Authorization: Bearer`` wobec magazynu hashy tokenów."""

    def __init__(self, entries: list[tuple[str, Principal]]) -> None:
        # Lista par (sha256 tokenu, Principal). Świadomie lista, nie mapa po hashu:
        # weryfikacja skanuje wszystkie wpisy przez ``compare_digest``, więc czas
        # nie zależy od pozycji trafienia (patrz ``verify_header``). Buduj przez
        # ``from_file``, żeby walidacja i guard położenia były w jednym miejscu.
        self._entries = tuple(entries)

    @classmethod
    def from_file(cls, tokens_file: Path, *, data_dir: Path) -> TokenVerifier:
        """Wczytaj i zwaliduj magazyn tokenów; wymuś położenie poza ``data_dir``.

        Rzuca ``TokenStoreError`` przy: magazynie wewnątrz ``data/``, braku pliku,
        złym JSON-ie, złej strukturze wpisu lub pustym magazynie. Świadomie twardy
        błąd startowy — bez tego drzwi HTTP nie mają kogo wpuścić.
        """
        resolved = tokens_file.resolve()
        data_root = data_dir.resolve()
        if resolved == data_root or data_root in resolved.parents:
            raise TokenStoreError(
                f"Magazyn tokenów nie może leżeć w katalogu danych ({data_root}): "
                f"{resolved}. Przenieś go poza data/ (sekrety poza zasięgiem narzędzi)."
            )
        try:
            raw = resolved.read_text(encoding="utf-8")
        except OSError as exc:
            raise TokenStoreError(
                f"Nie można odczytać magazynu tokenów {resolved}: {exc}"
            ) from exc
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TokenStoreError(
                f"Magazyn tokenów {resolved} to niepoprawny JSON: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise TokenStoreError(
                f"Magazyn tokenów {resolved} musi być listą wpisów, "
                f"jest: {type(parsed).__name__}."
            )
        entries = [
            _parse_entry(entry, index=i, source=resolved)
            for i, entry in enumerate(parsed)
        ]
        if not entries:
            raise TokenStoreError(
                f"Magazyn tokenów {resolved} jest pusty — brak osób z dostępem."
            )
        return cls(entries)

    def verify_header(self, authorization: str | None) -> Principal | None:
        """Zwróć ``Principal`` dla poprawnego bearer tokenu, inaczej ``None``.

        Parsowanie jest liberalne przy schemacie (case-insensitive „bearer"),
        a dopasowanie stałoczasowe: skan po wszystkich wpisach przez
        ``hmac.compare_digest`` bez wczesnego wyjścia, żeby czas nie zdradzał
        pozycji trafienia.
        """
        token = _extract_bearer(authorization)
        if token is None:
            return None
        presented = hashlib.sha256(token.encode("utf-8")).hexdigest()
        match: Principal | None = None
        for stored_hash, principal in self._entries:
            if hmac.compare_digest(presented, stored_hash):
                match = principal
        return match


def _parse_entry(entry: Any, *, index: int, source: Path) -> tuple[str, Principal]:
    """Zwaliduj pojedynczy wpis magazynu → (hash tokenu, Principal)."""
    if not isinstance(entry, dict):
        raise TokenStoreError(f"Wpis #{index} w {source} nie jest obiektem JSON.")
    person = entry.get("person")
    token_hash = entry.get("token_sha256")
    scopes = entry.get("scopes", ["read"])
    if not isinstance(person, str) or not person.strip():
        raise TokenStoreError(
            f"Wpis #{index} w {source}: brakuje niepustego pola 'person'."
        )
    if not isinstance(token_hash, str) or not token_hash.strip():
        raise TokenStoreError(
            f"Wpis #{index} w {source}: brakuje niepustego pola 'token_sha256'."
        )
    if not isinstance(scopes, list) or not all(isinstance(s, str) for s in scopes):
        raise TokenStoreError(
            f"Wpis #{index} w {source}: 'scopes' musi być listą napisów."
        )
    # Normalizuj do lowercase (typowe narzędzia hashujące zwracają wielkie litery),
    # potem twardo waliduj format — wyłapuje literówkę w magazynie na starcie,
    # zamiast cichego „token nie działa mimo zgodnego hasha".
    normalized_hash = token_hash.strip().lower()
    if not _SHA256_RE.fullmatch(normalized_hash):
        raise TokenStoreError(
            f"Wpis #{index} w {source}: 'token_sha256' musi być 64-znakowym hexem sha256."
        )
    return normalized_hash, Principal(person=person, scopes=tuple(scopes))


def _extract_bearer(authorization: str | None) -> str | None:
    """Wyłuskaj token z nagłówka ``Authorization: Bearer <token>`` albo ``None``."""
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


class TokenAuthMiddleware:
    """Czyste ASGI middleware: wymusza bearer token na żądaniach HTTP.

    Żądania inne niż ``http`` (lifespan, websocket) przepuszcza bez zmian. Przy
    braku lub złym tokenie odsyła 401 z ``WWW-Authenticate: Bearer`` (bez issuera —
    zero powierzchni OAuth) i generycznym ciałem (nigdy nie echo tokenu). Przy
    sukcesie przypina ``Principal`` do ``scope['state']`` i loguje tożsamość
    żądania (kto, jaka metoda i ścieżka).
    """

    def __init__(self, app: ASGIApp, *, verifier: TokenVerifier) -> None:
        self._app = app
        self._verifier = verifier

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        principal = self._verifier.verify_header(_header(scope, b"authorization"))
        if principal is None:
            logger.warning(
                "Odrzucono nieuwierzytelnione żądanie: %s %s",
                scope.get("method", "?"),
                _path(scope),
            )
            await _send_401(send)
            return

        scope.setdefault("state", {})
        state = scope["state"]
        if isinstance(state, dict):
            state["workmate_principal"] = principal
        logger.info(
            "Żądanie od %s: %s %s",
            principal.person,
            scope.get("method", "?"),
            _path(scope),
        )
        await self._app(scope, receive, send)


def _header(scope: Scope, name: bytes) -> str | None:
    """Zwróć wartość nagłówka HTTP (case-insensitive) z ASGI scope albo ``None``."""
    headers = scope.get("headers") or []
    if not isinstance(headers, (list, tuple)):
        return None
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return bytes(value).decode("latin-1")
    return None


def _path(scope: Scope) -> str:
    path = scope.get("path", "")
    return path if isinstance(path, str) else "?"


async def _send_401(send: Send) -> None:
    """Odeślij 401 bez echa tokenu i bez powierzchni OAuth."""
    body = '{"error": "Brak lub niepoprawny token uwierzytelniający."}'.encode()
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"www-authenticate", b"Bearer"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
