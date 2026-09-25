"""Testy uwierzytelniania drzwi HTTP (``auth.py``, Bramka 3 / ADR 0007).

Sprawdzamy trzy warstwy: parsowanie/weryfikację nagłówka, twarde reguły ładowania
magazynu (poza ``data/``, poprawna struktura) oraz zachowanie middleware end-to-end
napędzanego czysto po ASGI, bez sieci. Testy asynchroniczne uruchamiamy przez
``asyncio.run`` — repo nie ma ``pytest-asyncio``, a middleware jest zwykłym ASGI.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path
from typing import Any

import pytest

from sufler.adapters.inbound.mcp.auth import (
    Principal,
    TokenAuthMiddleware,
    TokenStoreError,
    TokenVerifier,
)

_TOKEN = "opaque-token-do-testow-123"
_TOKEN_HASH = hashlib.sha256(_TOKEN.encode()).hexdigest()


def _write_store(path: Path, entries: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


def _verifier(tmp_path: Path, entries: list[dict[str, Any]] | None = None) -> TokenVerifier:
    """Zbuduj weryfikator z magazynu poza ``data/`` (data_dir to inny podkatalog)."""
    store = _write_store(
        tmp_path / "tokens.json",
        entries
        if entries is not None
        else [{"person": "anna", "token_sha256": _TOKEN_HASH, "scopes": ["read"]}],
    )
    return TokenVerifier.from_file(store, data_dir=tmp_path / "data")


# --- parsowanie i weryfikacja nagłówka ---------------------------------------


def test_verify_header_none_returns_none(tmp_path: Path):
    assert _verifier(tmp_path).verify_header(None) is None


@pytest.mark.parametrize(
    "header",
    ["", "Basic abc", "Bearer", "Bearer   ", f"Token {_TOKEN}"],
)
def test_verify_header_malformed_returns_none(tmp_path: Path, header: str):
    assert _verifier(tmp_path).verify_header(header) is None


def test_verify_header_wrong_token_returns_none(tmp_path: Path):
    assert _verifier(tmp_path).verify_header("Bearer nieprawidlowy") is None


def test_verify_header_valid_token_returns_principal(tmp_path: Path):
    principal = _verifier(tmp_path).verify_header(f"Bearer {_TOKEN}")

    assert principal == Principal(person="anna", scopes=("read",))


def test_verify_header_scheme_is_case_insensitive(tmp_path: Path):
    verifier = _verifier(tmp_path)

    assert verifier.verify_header(f"bearer {_TOKEN}") is not None
    assert verifier.verify_header(f"BEARER {_TOKEN}") is not None


def test_verify_header_matches_uppercase_stored_hash(tmp_path: Path):
    """Hash zapisany WIELKIMI literami (typowe wyjście certutil/Get-FileHash) pasuje."""
    verifier = _verifier(
        tmp_path,
        entries=[{"person": "anna", "token_sha256": _TOKEN_HASH.upper(), "scopes": ["read"]}],
    )

    assert verifier.verify_header(f"Bearer {_TOKEN}") == Principal("anna", ("read",))


def test_verify_header_returns_matching_person_among_many(tmp_path: Path):
    """Przy wielu wpisach zwracany jest Principal WŁAŚCIWEJ osoby, nie pierwszej."""
    marek_token = "token-marka-999"
    marek_hash = hashlib.sha256(marek_token.encode()).hexdigest()
    verifier = _verifier(
        tmp_path,
        entries=[
            {"person": "anna", "token_sha256": _TOKEN_HASH, "scopes": ["read"]},
            {"person": "marek", "token_sha256": marek_hash, "scopes": ["read", "audit"]},
        ],
    )

    assert verifier.verify_header(f"Bearer {marek_token}") == Principal("marek", ("read", "audit"))


def test_verify_scans_all_entries_constant_time(tmp_path: Path, monkeypatch):
    """Weryfikacja używa compare_digest dla KAŻDEGO wpisu, bez wczesnego wyjścia."""
    other_hash = hashlib.sha256(b"inny").hexdigest()
    verifier = _verifier(
        tmp_path,
        entries=[
            {"person": "anna", "token_sha256": _TOKEN_HASH, "scopes": ["read"]},
            {"person": "marek", "token_sha256": other_hash, "scopes": ["read"]},
        ],
    )
    calls = {"n": 0}
    real = hmac.compare_digest

    def counting(a: object, b: object) -> bool:
        calls["n"] += 1
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", counting)

    assert verifier.verify_header(f"Bearer {_TOKEN}") == Principal("anna", ("read",))
    assert calls["n"] == 2  # skan po obu wpisach, mimo trafienia na pierwszym


# --- ładowanie magazynu -------------------------------------------------------


def test_from_file_rejects_store_inside_data_dir(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    store = _write_store(
        data_dir / "tokens.json",
        [{"person": "anna", "token_sha256": _TOKEN_HASH}],
    )

    with pytest.raises(TokenStoreError, match="katalogu danych"):
        TokenVerifier.from_file(store, data_dir=data_dir)


def test_from_file_missing_file_raises(tmp_path: Path):
    with pytest.raises(TokenStoreError, match="odczytać"):
        TokenVerifier.from_file(tmp_path / "brak.json", data_dir=tmp_path / "data")


def test_from_file_invalid_json_raises(tmp_path: Path):
    store = tmp_path / "tokens.json"
    store.write_text("{nie-json", encoding="utf-8")

    with pytest.raises(TokenStoreError, match="JSON"):
        TokenVerifier.from_file(store, data_dir=tmp_path / "data")


def test_from_file_non_list_json_raises(tmp_path: Path):
    """Poprawny JSON, ale nie lista (np. mapa osoba→hash) → czytelny błąd startowy."""
    store = tmp_path / "tokens.json"
    store.write_text(json.dumps({"anna": _TOKEN_HASH}), encoding="utf-8")

    with pytest.raises(TokenStoreError, match="listą"):
        TokenVerifier.from_file(store, data_dir=tmp_path / "data")


def test_from_file_non_string_scopes_raises(tmp_path: Path):
    with pytest.raises(TokenStoreError, match="scopes"):
        _verifier(
            tmp_path,
            entries=[{"person": "anna", "token_sha256": _TOKEN_HASH, "scopes": "read"}],
        )


def test_from_file_malformed_hash_raises(tmp_path: Path):
    """Wartość 'token_sha256' spoza formatu sha256 → błąd startowy, nie cichy brak dostępu."""
    with pytest.raises(TokenStoreError, match="hexem"):
        _verifier(tmp_path, entries=[{"person": "anna", "token_sha256": "za-krotki"}])


def test_from_file_entry_missing_hash_raises(tmp_path: Path):
    with pytest.raises(TokenStoreError, match="token_sha256"):
        _verifier(tmp_path, entries=[{"person": "anna"}])


def test_from_file_empty_store_raises(tmp_path: Path):
    with pytest.raises(TokenStoreError, match="pusty"):
        _verifier(tmp_path, entries=[])


def test_from_file_defaults_scopes_to_read(tmp_path: Path):
    principal = _verifier(
        tmp_path, entries=[{"person": "anna", "token_sha256": _TOKEN_HASH}]
    ).verify_header(f"Bearer {_TOKEN}")

    assert principal == Principal(person="anna", scopes=("read",))


# --- middleware end-to-end (ASGI, bez sieci) ---------------------------------


class _Downstream:
    """Zaślepka aplikacji ASGI: zapamiętuje tożsamość ze scope i zwraca 200."""

    def __init__(self) -> None:
        self.called = False
        self.seen_principal: Principal | None = None

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.called = True
        state = scope.get("state") or {}
        self.seen_principal = state.get("sufler_principal")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run_request(
    middleware: TokenAuthMiddleware,
    headers: list[tuple[bytes, bytes]],
    *,
    state: dict[str, Any] | None = None,
) -> tuple[int, dict[bytes, bytes], bytes]:
    """Przepuść jedno żądanie http przez middleware; zwróć (status, nagłówki, ciało).

    ``state`` pozwala wstrzyknąć ``scope['state']`` istniejące już przed middleware
    (tak jak robi to Starlette w produkcji), by sprawdzić, że nie jest nadpisywane.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": headers,
    }
    if state is not None:
        scope["state"] = state
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    asyncio.run(middleware(scope, receive, send))

    start = next(m for m in messages if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    resp_headers = {k: v for k, v in start["headers"]}
    return start["status"], resp_headers, body


def test_middleware_missing_header_returns_401(tmp_path: Path):
    downstream = _Downstream()
    middleware = TokenAuthMiddleware(downstream, verifier=_verifier(tmp_path))

    status, headers, body = _run_request(middleware, headers=[])

    assert status == 401
    assert headers.get(b"www-authenticate") == b"Bearer"
    assert not downstream.called
    assert _TOKEN.encode() not in body  # nigdy nie echo tokenu


def test_middleware_bad_token_returns_401(tmp_path: Path):
    downstream = _Downstream()
    middleware = TokenAuthMiddleware(downstream, verifier=_verifier(tmp_path))

    status, _, _ = _run_request(middleware, headers=[(b"authorization", b"Bearer zly-token")])

    assert status == 401
    assert not downstream.called


def test_middleware_valid_token_passes_through_with_identity(tmp_path: Path):
    downstream = _Downstream()
    middleware = TokenAuthMiddleware(downstream, verifier=_verifier(tmp_path))

    status, _, body = _run_request(
        middleware, headers=[(b"authorization", f"Bearer {_TOKEN}".encode())]
    )

    assert status == 200
    assert body == b"ok"
    assert downstream.called
    assert downstream.seen_principal == Principal(person="anna", scopes=("read",))


def test_middleware_does_not_leak_token_to_logs(tmp_path: Path, caplog):
    """Log audytu loguje tożsamość (INFO), ale NIGDY samego tokenu."""
    downstream = _Downstream()
    middleware = TokenAuthMiddleware(downstream, verifier=_verifier(tmp_path))

    with caplog.at_level(logging.INFO):
        _run_request(middleware, headers=[(b"authorization", f"Bearer {_TOKEN}".encode())])

    assert _TOKEN not in caplog.text
    assert "anna" in caplog.text  # tożsamość jednak jest logowana
    assert any(r.levelno == logging.INFO for r in caplog.records)


def test_middleware_preserves_existing_scope_state(tmp_path: Path):
    """Middleware DOKŁADA Principal do scope['state'], nie kasuje kluczy Starlette."""
    downstream = _Downstream()
    middleware = TokenAuthMiddleware(downstream, verifier=_verifier(tmp_path))
    existing: dict[str, Any] = {"starlette_key": "value"}

    _run_request(
        middleware,
        headers=[(b"authorization", f"Bearer {_TOKEN}".encode())],
        state=existing,
    )

    assert existing["starlette_key"] == "value"
    assert existing["sufler_principal"] == Principal(person="anna", scopes=("read",))


def test_middleware_passes_non_http_scopes_untouched(tmp_path: Path):
    """Lifespan i inne nie-http scope przechodzą bez uwierzytelniania."""
    seen = {"type": None}

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        seen["type"] = scope["type"]

    middleware = TokenAuthMiddleware(downstream, verifier=_verifier(tmp_path))

    async def noop() -> dict[str, Any]:
        return {}

    asyncio.run(middleware({"type": "lifespan"}, noop, noop))

    assert seen["type"] == "lifespan"
