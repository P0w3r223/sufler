"""Testy narzędzia wdrożeniowego ``deploy/http/manage_tokens.py`` (Bramka 3, ADR 0007).

Kluczowy inwariant: token wydany przez narzędzie MUSI się zweryfikować w prawdziwym
weryfikatorze drzwi (``TokenVerifier``) — inaczej operator zostałby z „tokenem, który
nie działa mimo zgodnego hasha". Dlatego testy robią round-trip przez publiczne API
``TokenVerifier.from_file`` zamiast sprawdzać hash w izolacji.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

import pytest

from sufler.adapters.inbound.mcp.auth import TokenVerifier

_SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "http" / "manage_tokens.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("manage_tokens", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mt = _load_module()


@pytest.fixture
def store_and_data(tmp_path: Path) -> tuple[Path, Path]:
    """Magazyn tokenów i katalog danych — magazyn celowo POZA data/ (guard położenia)."""
    return tmp_path / "tokens.json", tmp_path / "data"


def _issue(store: Path, data: Path, person: str, capsys: pytest.CaptureFixture[str]) -> str:
    """Wydaj token; zwróć surowy token (jedyna linia stdout)."""
    rc = mt.main(["issue", "--person", person, "--store", str(store), "--data-dir", str(data)])
    assert rc == 0
    return capsys.readouterr().out.strip()


def test_issued_token_verifies_in_door(
    store_and_data: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, data = store_and_data
    token = _issue(store, data, "anna.kowalska", capsys)

    principal = TokenVerifier.from_file(store, data_dir=data).verify_header(f"Bearer {token}")

    assert principal is not None
    assert principal.person == "anna.kowalska"
    assert principal.scopes == ("read",)


def test_stored_hash_is_lowercase_sha256_of_token(
    store_and_data: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, data = store_and_data
    token = _issue(store, data, "marek.nowak", capsys)

    entry = mt._load_existing(store)[0]

    assert entry["token_sha256"] == hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_issue_leaves_no_temp_file(
    store_and_data: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, data = store_and_data
    _issue(store, data, "anna.kowalska", capsys)

    assert not store.with_name(store.name + ".tmp").exists()


def test_revoke_by_person_removes_all_their_entries(
    store_and_data: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, data = store_and_data
    _issue(store, data, "anna.kowalska", capsys)
    keep_token = _issue(store, data, "marek.nowak", capsys)

    rc = mt.main(
        ["revoke", "--person", "anna.kowalska", "--store", str(store), "--data-dir", str(data)]
    )

    assert rc == 0
    persons = [e["person"] for e in mt._load_existing(store)]
    assert persons == ["marek.nowak"]
    # osoba, która została, dalej się weryfikuje
    assert TokenVerifier.from_file(store, data_dir=data).verify_header(f"Bearer {keep_token}")


def test_rotation_revoke_by_hash_targets_one_entry(
    store_and_data: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, data = store_and_data
    old_token = _issue(store, data, "anna.kowalska", capsys)
    new_token = _issue(store, data, "anna.kowalska", capsys)  # rotacja: drugi wpis tej samej osoby
    old_prefix = hashlib.sha256(old_token.encode("utf-8")).hexdigest()[:16]

    rc = mt.main(["revoke", "--hash", old_prefix, "--store", str(store), "--data-dir", str(data)])

    assert rc == 0
    verifier = TokenVerifier.from_file(store, data_dir=data)
    assert verifier.verify_header(f"Bearer {old_token}") is None  # stary unieważniony
    assert verifier.verify_header(f"Bearer {new_token}") is not None  # nowy działa


def test_revoke_ambiguous_hash_prefix_refuses(store_and_data: tuple[Path, Path]) -> None:
    store, data = store_and_data
    # Dwa wpisy o WSPÓLNYM, niepustym prefiksie — realnie testuje gałąź len(matched) != 1.
    shared = "abc123"
    store.write_text(
        json.dumps(
            [
                {"person": "a", "token_sha256": shared + "0" * 58, "scopes": ["read"]},
                {"person": "b", "token_sha256": shared + "1" * 58, "scopes": ["read"]},
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="pasuje do 2"):
        mt.main(["revoke", "--hash", shared, "--store", str(store), "--data-dir", str(data)])


def test_issue_into_data_dir_refuses_before_writing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Guard położenia: sekret nie może trafić do data/ — odmowa PRZED jakimkolwiek zapisem.
    data = tmp_path / "data"
    data.mkdir()
    inside = data / "tokens.json"

    with pytest.raises(SystemExit, match="katalogu danych"):
        mt.main(["issue", "--person", "anna", "--store", str(inside), "--data-dir", str(data)])

    assert not inside.exists()  # nic nie zapisane
    assert capsys.readouterr().out == ""  # token nie wyciekł na stdout


def test_verify_reports_invalid_store(
    store_and_data: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    store, data = store_and_data
    store.write_text('{"nie": "lista"}', encoding="utf-8")

    rc = mt.main(["verify", "--store", str(store), "--data-dir", str(data)])

    assert rc == 1


def test_verify_rejects_store_inside_data_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Guard położenia: magazyn wewnątrz data/ = sekret w katalogu narzędzi → odrzucony.
    data = tmp_path / "data"
    data.mkdir()
    inside = data / "tokens.json"
    _issue(inside, tmp_path / "other", "anna.kowalska", capsys)  # zapisz gdziekolwiek

    rc = mt.main(["verify", "--store", str(inside), "--data-dir", str(data)])

    assert rc == 1


# --- domyślny magazyn: JEDNO źródło prawdy z serwerem ------------------------


def test_default_store_is_the_server_constant_not_a_copy() -> None:
    """Narzędzie i serwer MUSZĄ szukać magazynu w tym samym miejscu.

    ``config._DEFAULT_TOKENS_FILE`` dostał rozgałęzienie po ``os.name`` (windowsowy literał
    "C:/…" na Linuksie stawał się ścieżką WZGLĘDNĄ pod CWD), ale kopia literału w tym skrypcie
    poprawki nie zobaczyła: na Linuksie ``manage_tokens issue`` pisał do
    ``./C:/ProgramData/Sufler/tokens.json`` i meldował sukces, a serwer szukał magazynu
    w ``/var/lib/sufler/tokens.json`` i nie wpuszczał nikogo.
    """
    from sufler import config

    assert mt.DEFAULT_STORE == config._DEFAULT_TOKENS_FILE
    assert mt.DEFAULT_STORE.is_absolute(), "domyślny magazyn nie może być względny wobec CWD"


def test_default_store_is_not_written_as_a_path_literal() -> None:
    """Sama równość nie wystarczy: na Windows kopia i oryginał mają tę samą wartość, więc milczy.

    Sondujemy więc ZAPIS: prawa strona przypisania ``DEFAULT_STORE`` nie może być literałem
    tekstowym. Dopóki nim była, poprawka po stronie ``config/server.py`` nie miała jak tu dojechać —
    a rozjazd ujawniał się wyłącznie na Linuksie, czyli na jedynej platformie produkcyjnej.
    """
    przypisanie = re.search(
        r"^DEFAULT_STORE\s*=\s*(.+)$", _SCRIPT.read_text(encoding="utf-8"), re.M
    )
    assert przypisanie is not None, "brak przypisania DEFAULT_STORE w manage_tokens.py"

    prawa_strona = przypisanie.group(1)
    assert '"' not in prawa_strona and "'" not in prawa_strona, (
        f"DEFAULT_STORE = {prawa_strona} powiela literał ścieżki zamiast brać stałą "
        "z sufler.config (_DEFAULT_TOKENS_FILE)."
    )
