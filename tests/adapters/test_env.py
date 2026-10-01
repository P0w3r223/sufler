"""Testy WSPÓLNEGO loadera ``.env`` drzwi (``adapters/inbound/env.py``).

Loader jest wygodą deva i granicą wczytania sekretu z ``.env`` — musi być odporny
na kodowanie (PowerShell zapisuje UTF-16 LE z BOM) i NIE nadpisywać realnego env.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import httpx
import pytest

from sufler.adapters.inbound import env as env_module
from sufler.adapters.inbound.env import apply_env_file, configure_logging


def test_apply_env_file_parses_utf8_with_comments_and_quotes(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUFLER_TEST_UTF8", raising=False)
    env = tmp_path / ".env"
    env.write_text('# komentarz\nSUFLER_TEST_UTF8 = "abc123"\n', encoding="utf-8")

    apply_env_file(env)

    assert os.environ["SUFLER_TEST_UTF8"] == "abc123"


def test_apply_env_file_handles_utf16_bom_from_powershell(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUFLER_TEST_UTF16", raising=False)
    env = tmp_path / ".env"
    # PowerShell (Out-File/Set-Content) domyślnie zapisuje UTF-16 LE z BOM.
    env.write_text("SUFLER_TEST_UTF16=xyz789\n", encoding="utf-16")

    apply_env_file(env)

    assert os.environ["SUFLER_TEST_UTF16"] == "xyz789"


def test_apply_env_file_does_not_override_real_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SUFLER_TEST_PRIO", "z-realnego-env")
    env = tmp_path / ".env"
    env.write_text("SUFLER_TEST_PRIO=z-pliku\n", encoding="utf-8")

    apply_env_file(env)

    assert os.environ["SUFLER_TEST_PRIO"] == "z-realnego-env"  # setdefault: env wygrywa


def test_apply_env_file_skips_blank_and_comment_lines(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUFLER_TEST_KEEP", raising=False)
    env = tmp_path / ".env"
    env.write_text("\n# tylko komentarz\n\nSUFLER_TEST_KEEP=1\n", encoding="utf-8")

    apply_env_file(env)

    assert os.environ["SUFLER_TEST_KEEP"] == "1"


def test_apply_env_file_raises_clear_error_on_corrupt_encoding(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_bytes(b"\xff\xfe\x41")  # BOM UTF-16 + niepełny bajt → błąd dekodowania

    with pytest.raises(SystemExit, match="kodowanie"):
        apply_env_file(env)


def _configure_logging_from_fresh(monkeypatch, level_env: str | None) -> int:
    """Odtwórz warunek świeżego procesu (bez handlerów root — inaczej basicConfig jest no-op),
    ustaw SUFLER_LOG_LEVEL, zawołaj configure_logging i zwróć wynikowy poziom root loggera.
    """
    if level_env is None:
        monkeypatch.delenv("SUFLER_LOG_LEVEL", raising=False)
    else:
        monkeypatch.setenv("SUFLER_LOG_LEVEL", level_env)
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        for handler in list(root.handlers):
            root.removeHandler(handler)
        configure_logging()
        return root.level
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


def test_configure_logging_reads_workmate_log_level(monkeypatch):
    """R3: SUFLER_LOG_LEVEL=DEBUG realnie obniża próg root loggera (jak settings.log_level)."""
    assert _configure_logging_from_fresh(monkeypatch, "DEBUG") == logging.DEBUG


def test_configure_logging_defaults_to_info(monkeypatch):
    """R3: bez zmiennej domyślny poziom to INFO (kontrakt bez zmian)."""
    assert _configure_logging_from_fresh(monkeypatch, None) == logging.INFO


_SHAREPOINT_URL = (
    "https://contoso.sharepoint.com/sites/x/_layouts/15/download.aspx"
    "?UniqueId=abc&tempauth=eyJ0eXAiOiJKV1QifQ.sekret"
)


def _httpx_log_lines(url: str, caplog) -> list[str]:
    """Wyślij żądanie przez prawdziwy klient ``httpx`` (bez sieci) i zwróć linie jego logu."""
    httpx_logger = logging.getLogger("httpx")
    saved_filters = httpx_logger.filters[:]
    try:
        configure_logging("INFO")
        with caplog.at_level(logging.INFO, logger="httpx"):
            transport = httpx.MockTransport(lambda request: httpx.Response(200))
            with httpx.Client(transport=transport) as client:
                client.get(url)
        return [r.getMessage() for r in caplog.records if r.name == "httpx"]
    finally:
        httpx_logger.filters[:] = saved_filters


def test_httpx_log_hides_the_query_string_with_the_sharepoint_token(caplog):
    """Adres pobrania z SharePointa niesie w query token ``tempauth`` (ok. 1 h) — nie do logu."""
    lines = _httpx_log_lines(_SHAREPOINT_URL, caplog)
    assert len(lines) == 1, lines
    assert "tempauth" not in lines[0]
    assert "sekret" not in lines[0]
    assert (
        "GET https://contoso.sharepoint.com/sites/x/_layouts/15/download.aspx?[ukryte]" in lines[0]
    )
    assert "200" in lines[0]  # status zostaje: log dalej pokazuje, że drzwi pracują


def test_httpx_log_keeps_urls_without_a_query_string(caplog):
    lines = _httpx_log_lines("https://graph.microsoft.com/v1.0/teams/t1/channels", caplog)
    assert len(lines) == 1, lines
    assert "GET https://graph.microsoft.com/v1.0/teams/t1/channels " in lines[0]
    assert "[ukryte]" not in lines[0]


def test_url_filter_leaves_other_arguments_untouched():
    record = logging.LogRecord("httpx", logging.INFO, "", 0, "%s %s %d", ("GET", "a?b", 7), None)
    env_module._RedactUrlQuery().filter(record)
    assert record.getMessage() == "GET a?b 7"


def test_configure_logging_twice_installs_one_url_filter():
    httpx_logger = logging.getLogger("httpx")
    saved_filters = httpx_logger.filters[:]
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        filters = [f for f in httpx_logger.filters if isinstance(f, env_module._RedactUrlQuery)]
        assert len(filters) == 1
    finally:
        httpx_logger.filters[:] = saved_filters
