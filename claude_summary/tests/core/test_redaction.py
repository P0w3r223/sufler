"""Redakcja treści wrażliwej: sekrety, konta/IP/ID, ścieżki, wklejki i pełne pominięcie."""

from __future__ import annotations

import time

from claude_summary.core.redaction import person_label, redact_text, sanitize_prompt


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


def test_redacts_prefixed_secret_assignment() -> None:
    # Nazwy zmiennych env skopiowane z .env mają prefiks — granica \b za nim nie wypada.
    assert "Zaq12wsx" not in redact_text("DB_PASSWORD=Zaq12wsx")
    assert "[SEKRET]" in redact_text("DB_PASSWORD=Zaq12wsx")
    assert "Qr7~8vLpXk2Zt5Mn" not in redact_text("ustaw client_secret=Qr7~8vLpXk2Zt5Mn")
    assert "ATATT3xFfGF0abcdefgh" not in redact_text("SUFLER_JIRA_TOKEN=ATATT3xFfGF0abcdefgh")


def test_redacts_quoted_secret_value_without_tail_leak() -> None:
    redacted = redact_text('password="moje tajne haslo"')
    assert "[SEKRET]" in redacted
    assert "tajne haslo" not in redacted
    assert '"' not in redacted

    redacted_single = redact_text("password='moje tajne haslo'")
    assert "[SEKRET]" in redacted_single
    assert "tajne haslo" not in redacted_single

    redacted_pl = redact_text('haslo="moje tajne haslo"')
    assert "[SEKRET]" in redacted_pl
    assert "tajne haslo" not in redacted_pl


def test_token_in_prose_without_assignment_is_not_redacted() -> None:
    # "token" bez [:=] to zwykłe słowo prozy, nie przypisanie sekretu.
    text = "wygenerowałem token dostępu ręcznie w panelu"
    assert redact_text(text) == text


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


def test_user_path_covers_whole_segment_with_space_or_dash() -> None:
    # REGRESJA: segment kończył się na spacji/myślniku, więc nazwisko przechodziło dalej.
    with_space = redact_text("plik w C:\\Users\\Jan Kowalski\\app\\log.txt")
    assert "Kowalski" not in with_space
    assert "\\app\\log.txt" in with_space  # reszta ścieżki zachowana

    with_dash = redact_text("plik w /home/jan-kowalski/app")
    assert "kowalski" not in with_dash
    assert "/app" in with_dash


def test_user_path_in_dash_encoded_folder_name() -> None:
    # Nazwa folderu ~/.claude/projects: TU myślnik jest separatorem, spacja zostaje w nazwie.
    redacted = redact_text("C--Users-Jan Kowalski-BIAP-PROJEKT")
    assert "Kowalski" not in redacted
    assert redacted == "C--Users-[UŻYTKOWNIK]-BIAP-PROJEKT"


def test_user_path_does_not_swallow_prose_after_path() -> None:
    # Bez separatora domykającego segment bierzemy sam token — proza za ścieżką zostaje.
    redacted = redact_text("sprawdź C:\\Users\\jdoe i powiedz co dalej")
    assert "jdoe" not in redacted
    assert "i powiedz co dalej" in redacted


def test_redacts_ipv6_full_and_compressed() -> None:
    # REGRESJA: kategoria "ip" obejmowała wyłącznie IPv4.
    full = sanitize_prompt("serwer 2001:0db8:85a3:0000:0000:8a2e:0370:7334 nie odpowiada")
    assert "2001" not in full.text
    assert "[IP]" in full.text
    assert "ip" in full.categories

    short = sanitize_prompt("ping fe80::1c2d:3e4f i sprawdź trasę")
    assert "fe80" not in short.text
    assert "ip" in short.categories

    mixed = redact_text("adres 2001:db8::8a2e:370:7334 w konfiguracji")
    assert "8a2e" not in mixed


def test_ipv6_pattern_leaves_ordinary_text_alone() -> None:
    for text in ("spotkanie o 09:00:00", "użyj std::vector w tym miejscu", "wycinek x[::2]"):
        assert redact_text(text) == text


def test_unquoted_secret_value_is_redacted_to_end_of_line() -> None:
    # REGRESJA: bez cudzysłowów redagowany był tylko pierwszy token wartości.
    redacted = redact_text("haslo: moje tajne haslo")
    assert "[SEKRET]" in redacted
    assert "tajne" not in redacted

    two_lines = redact_text("password: moje tajne haslo\nzrób deploy")
    assert "tajne" not in two_lines
    assert "zrób deploy" in two_lines  # redakcja kończy się na końcu linii


def test_long_single_token_paste_is_scanned_in_bounded_time() -> None:
    """REGRESJA: nieograniczony prefiks + brak okna dawały kwadratowe skanowanie (dziesiątki s)."""
    blob = "napraw to: " + ("a1b2c3d4e5f6" * 5_000)  # 60 kB jednego tokenu
    start = time.perf_counter()
    result = sanitize_prompt(blob)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"redakcja zajęła {elapsed:.1f}s — wzorce skanują całą wklejkę"
    assert result.dropped is False
    assert len(result.text) <= 300  # i tak zostaje samo okno wyniku

    identifiers = "opisz: " + ("nazwa_zmiennej_" * 3_000)  # 45 kB znaków słownych
    start = time.perf_counter()
    sanitize_prompt(identifiers)
    assert time.perf_counter() - start < 2.0


def test_scan_window_does_not_leak_half_a_secret() -> None:
    """REGRESJA: okno cięło w środku tokenu, a redakcja skraca tekst — urwany ogon sekretu
    wjeżdżał w zachowywane znaki wyniku (dopasowanie nie łapie fragmentu)."""
    # Dziesięć długich kluczy skraca się do dziesięciu etykiet, więc to, co stoi na granicy
    # okna, ląduje w wyniku daleko przed limitem _MAX_KEEP.
    prefix = ("sk-ant-" + "a" * 180 + " ") * 10
    filler = "x" * (1910 - len(prefix) - 1) + " "
    aws_key = "AKIAABCDEFGHIJKLMNOP"  # granica okna (1920) wypada w środku tego tokenu
    result = sanitize_prompt(prefix + filler + aws_key + " reszta wklejki")

    assert "AKIA" not in result.text
    assert "[SEKRET]" in result.text


def test_person_label_never_carries_email() -> None:
    assert person_label("jan.kowalski@firma.pl") == "Jan Kowalski"
    assert person_label("P0w3r223@users.noreply.github.com") == "P0w3r223"
    assert person_label("team.bot+tag@example.org") == "Team Bot"
    assert person_label("Jan Kowalski") == "Jan Kowalski"  # nie-adres zostaje
    assert person_label("") == ""


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
