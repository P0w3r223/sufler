"""Redakcja treści wrażliwej: sekrety, konta/IP/ID, ścieżki, wklejki i pełne pominięcie."""

from __future__ import annotations

from claude_summary.core.redaction import redact_text, sanitize_prompt


def test_redacts_ip_and_account_keeps_instruction() -> None:
    result = sanitize_prompt("zaloguj przez ssh deploy@192.0.2.10 na serwer")
    assert "192.0.2.10" not in result.text
    assert "deploy@192.0.2.10" not in result.text
    assert "[KONTO]" in result.text
    assert "zaloguj" in result.text  # instrukcja zachowana
    assert result.dropped is False
    assert "account" in result.categories


def test_redacts_secret_assignment() -> None:
    assert "[SEKRET]" in redact_text("ustaw API_KEY=sk-ant-abc123def456ghi789")
    assert "sk-ant" not in redact_text("token=sk-ant-abc123def456ghi789")


def test_redacts_uuid() -> None:
    out = redact_text("konto 3534249a-6d2c-480a-8f8b-a0c746180c71 usera")
    assert "[ID]" in out
    assert "3534249a" not in out


def test_redacts_bearer_and_private_key() -> None:
    assert "[SEKRET]" in redact_text("Authorization: Bearer abcdefgh12345678")
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIabc123\n-----END RSA PRIVATE KEY-----"
    redacted = redact_text(key)
    assert "[SEKRET]" in redacted
    assert "MIIabc123" not in redacted


def test_redacts_windows_user_path() -> None:
    assert "[UŻYTKOWNIK]" in redact_text("plik w C:\\Users\\jdoe\\BIAP")
    assert "jdoe" not in redact_text("C:\\Users\\jdoe\\x")


def test_pasted_shell_output_is_dropped() -> None:
    dump = "deploy@intern-ubuntu:/opt/app$ sudo docker compose logs\nINFO start\nERROR boom"
    result = sanitize_prompt(dump)
    assert result.dropped is True


def test_paste_keeps_leading_instruction_only() -> None:
    text = 'wykonuję i wklejam log "deploy@intern-ubuntu:/opt/app$ sudo docker ps\nINFO x"'
    result = sanitize_prompt(text)
    assert result.dropped is False
    assert "wykonuję i wklejam log" in result.text
    assert "intern-ubuntu" not in result.text
    assert "INFO x" not in result.text  # wklejka pominięta
    assert "paste" in result.categories


def test_short_instruction_unchanged() -> None:
    result = sanitize_prompt("napraw te testy teraz")
    assert result.text == "napraw te testy teraz"
    assert result.dropped is False
    assert result.categories == frozenset()


def test_long_prompt_truncated() -> None:
    # 300 znaków, jedna linia, bez sygnałów wklejki → gałąź czystego przycięcia.
    long_text = "slowo " * 50
    result = sanitize_prompt(long_text)
    assert result.dropped is False
    assert result.text.endswith("[…]")
    assert len(result.text) < len(long_text)
