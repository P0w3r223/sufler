"""Zarządzanie magazynem tokenów drzwi HTTP WorkMate (Bramka 3, ADR 0007).

Wydawanie, rotacja, unieważnianie, listowanie i walidacja tokenów per osoba dla
wdrożenia ``streamable-http``. Zastępuje kruche inline'owe ``python -c`` z
[`deploy-http.md`](../../docs/how-to/deploy-http.md) jednym narzędziem, które
hashuje token DOKŁADNIE tak jak weryfikator drzwi
(``adapters/inbound/mcp/auth.py``: ``sha256(token.encode("utf-8")).hexdigest()``),
więc wydany token na pewno się zweryfikuje.

Reguły bezpieczeństwa (te same, co w runbooku):

* Token surowy pokazujemy WYŁĄCZNIE raz, na ``stdout``, przy ``issue`` — magazyn
  trzyma tylko ``sha256``. Cała ludzka podpowiedź idzie na ``stderr``, żeby
  ``stdout`` dało się bezpiecznie przechwycić do zmiennej.
* Zapis jest atomowy (plik tymczasowy + ``os.replace``) — przerwanie nie zostawia
  obciętego magazynu.
* Po każdej mutacji plik jest rewalidowany regułami weryfikatora drzwi, więc
  narzędzie nigdy nie zostawi magazynu, którego serwer HTTP by nie przyjął.

Użycie (w środowisku projektu, żeby ``workmate`` był importowalny)::

    uv run --no-sync python deploy/http/manage_tokens.py issue --person anna.kowalska
    uv run --no-sync python deploy/http/manage_tokens.py list
    uv run --no-sync python deploy/http/manage_tokens.py revoke --hash 1a2b3c
    uv run --no-sync python deploy/http/manage_tokens.py verify
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
from collections.abc import Sequence
from pathlib import Path

from workmate.adapters.inbound.mcp.auth import TokenStoreError, TokenVerifier
from workmate.config import _DEFAULT_TOKENS_FILE

# Domyślny magazyn — TA SAMA stała co u serwera (poza data/, poza repo), nie jej kopia.
# Powielony literał windowsowy przeżył tu poprawkę ``config.py`` (rozgałęzienie po ``os.name``),
# więc na Linuksie narzędzie pisało do WZGLĘDNEGO ``./C:/ProgramData/WorkMate/tokens.json``
# i meldowało sukces, a serwer szukał magazynu w ``/var/lib/workmate/tokens.json``.
# Nadpisywalny przez ``--store`` albo ``WORKMATE_TOKENS_FILE`` (jak serwer przy starcie).
DEFAULT_STORE = _DEFAULT_TOKENS_FILE

Entry = dict[str, object]


def _hash_token(token: str) -> str:
    """Zwróć ``sha256`` tokenu tak, jak liczy je weryfikator drzwi (lowercase hex)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _repo_data_dir() -> Path:
    """Katalog danych do guardu „magazyn poza data/".

    Honoruje ``WORKMATE_DATA_DIR`` tak jak serwer (``config``), żeby guard narzędzia
    nie rozjechał się z faktycznym data-dir wdrożenia; inaczej ``data/`` repozytorium.
    """
    env = os.environ.get("WORKMATE_DATA_DIR")
    if env:
        return Path(env)
    # deploy/http/manage_tokens.py → parents[2] == korzeń repozytorium.
    return Path(__file__).resolve().parents[2] / "data"


def _guard_store_location(store: Path, data_dir: Path) -> None:
    """Odrzuć magazyn wewnątrz ``data/`` PRZED zapisem (sekret poza zasięgiem narzędzi).

    Ten sam warunek, co ``TokenVerifier.from_file``, ale sprawdzony na wejściu komend
    mutujących — inaczej hash tokenu wylądowałby w folderze indeksowanym, zanim
    rewalidacja zdążyłaby to wykryć.
    """
    resolved = store.resolve()
    data_root = data_dir.resolve()
    if resolved == data_root or data_root in resolved.parents:
        raise SystemExit(
            f"Magazyn tokenów nie może leżeć w katalogu danych ({data_root}): {resolved}. "
            "Przenieś go poza data/ (sekrety poza zasięgiem narzędzi)."
        )


def _load_or_empty(store: Path) -> list[Entry]:
    """Wczytaj wpisy; brak pliku = pusta lista (do pierwszego ``issue``)."""
    if not store.exists():
        return []
    return _load_existing(store)


def _load_existing(store: Path) -> list[Entry]:
    """Wczytaj wpisy z istniejącego magazynu; twardo błąd, jeśli go nie ma/jest zły."""
    try:
        parsed = json.loads(store.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"Magazyn tokenów nie istnieje: {store}") from None
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Magazyn {store} to niepoprawny JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise SystemExit(f"Magazyn {store} musi być listą wpisów.")
    return [dict(e) for e in parsed]


def _atomic_write(store: Path, entries: list[Entry]) -> None:
    """Zapis atomowy: plik tymczasowy obok magazynu + ``os.replace``."""
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_name(store.name + ".tmp")
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, store)


def _revalidate(store: Path, data_dir: Path) -> None:
    """Potwierdź, że NIEPUSTY magazyn przejdzie regułami weryfikatora drzwi.

    Błąd rewalidacji zgłaszamy jako schludny ``SystemExit`` (nie surowy traceback) —
    to błąd konfiguracji operatora, nie awaria narzędzia.
    """
    try:
        TokenVerifier.from_file(store, data_dir=data_dir)
    except TokenStoreError as exc:
        raise SystemExit(f"Magazyn po zmianie jest niepoprawny dla drzwi: {exc}") from exc


def _entry_hash(entry: Entry) -> str:
    """Hash wpisu jako napis; toleruje ręcznie zepsuty magazyn (pusty zamiast KeyError)."""
    return str(entry.get("token_sha256") or "")


def _hash_prefix(entry: Entry) -> str:
    return _entry_hash(entry)[:12]


def cmd_issue(args: argparse.Namespace) -> int:
    """Wydaj nowy token osobie; wypisz surowy token RAZ na stdout."""
    _guard_store_location(args.store, args.data_dir)
    entries = _load_or_empty(args.store)
    if any(e.get("person") == args.person for e in entries):
        print(
            f"uwaga: {args.person} ma już wpis — to OK dla rotacji; "
            "po podmianie usuń stary przez `revoke --hash <prefiks>`.",
            file=sys.stderr,
        )
    token = secrets.token_urlsafe(32)
    entries.append(
        {"person": args.person, "token_sha256": _hash_token(token), "scopes": list(args.scopes)}
    )
    _atomic_write(args.store, entries)
    _revalidate(args.store, args.data_dir)
    print(
        f"Wydano token dla {args.person} -> {args.store}. Przekaż go bezpiecznym "
        "kanałem (NIE mailem, NIE w repo). Magazyn trzyma tylko sha256.",
        file=sys.stderr,
    )
    print(token)  # stdout = wyłącznie sekret, do przechwycenia do zmiennej.
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    """Usuń wpis(y) po osobie albo po prefiksie hasha (rotacja)."""
    _guard_store_location(args.store, args.data_dir)
    entries = _load_existing(args.store)
    if args.hash is not None:
        prefix = args.hash.lower()
        matched = [e for e in entries if _entry_hash(e).lower().startswith(prefix)]
        if len(matched) != 1:
            raise SystemExit(f"Prefiks hasha {prefix!r} pasuje do {len(matched)} wpisów (chcę 1).")
        kept = [e for e in entries if not _entry_hash(e).lower().startswith(prefix)]
        removed = 1
    else:
        removed = sum(1 for e in entries if e.get("person") == args.person)
        if removed == 0:
            raise SystemExit(f"Brak wpisów dla osoby {args.person!r}.")
        kept = [e for e in entries if e.get("person") != args.person]
    _atomic_write(args.store, kept)
    if kept:
        _revalidate(args.store, args.data_dir)
    else:
        print(
            "uwaga: magazyn jest teraz pusty — drzwi HTTP NIE wystartują (brak osób "
            "z dostępem). To celowe tylko przy wycofaniu wdrożenia.",
            file=sys.stderr,
        )
    print(
        f"Usunięto {removed} wpis(y). Zrestartuj usługę, by przeładować magazyn.", file=sys.stderr
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Wypisz osoby, scope'y i prefiks hasha (bez sekretów)."""
    entries = _load_existing(args.store)
    if not entries:
        print("(magazyn pusty)", file=sys.stderr)
        return 0
    for e in entries:
        raw_scopes = e.get("scopes")
        scopes_list = raw_scopes if isinstance(raw_scopes, list) else []
        scopes = ",".join(str(s) for s in scopes_list)
        print(f"{str(e.get('person')):24} [{scopes}]  {_hash_prefix(e)}…")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Sprawdź, że magazyn przejdzie regułami weryfikatora drzwi (jak start serwera)."""
    try:
        TokenVerifier.from_file(args.store, data_dir=args.data_dir)
    except TokenStoreError as exc:
        print(f"NIEPOPRAWNY: {exc}", file=sys.stderr)
        return 1
    persons = [str(e.get("person")) for e in _load_existing(args.store)]
    print(f"OK: {len(persons)} osób z dostępem — {', '.join(persons)}", file=sys.stderr)
    return 0


def _default_store() -> Path:
    env = os.environ.get("WORKMATE_TOKENS_FILE")
    return Path(env) if env else DEFAULT_STORE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="manage_tokens",
        description="Zarządzanie magazynem tokenów drzwi HTTP WorkMate (Bramka 3, ADR 0007).",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--store", type=Path, default=_default_store(), help="Ścieżka magazynu tokenów (JSON)."
    )
    common.add_argument(
        "--data-dir", type=Path, default=_repo_data_dir(), help="Katalog danych (guard położenia)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_issue = sub.add_parser("issue", parents=[common], help="Wydaj token osobie.")
    p_issue.add_argument("--person", required=True, help="Login/identyfikator osoby.")
    p_issue.add_argument("--scopes", nargs="+", default=["read"], help="Scope'y (domyślnie: read).")
    p_issue.set_defaults(func=cmd_issue)

    p_revoke = sub.add_parser("revoke", parents=[common], help="Unieważnij token(y).")
    group = p_revoke.add_mutually_exclusive_group(required=True)
    group.add_argument("--person", help="Usuń wszystkie wpisy tej osoby.")
    group.add_argument("--hash", help="Usuń jeden wpis po prefiksie sha256 (rotacja).")
    p_revoke.set_defaults(func=cmd_revoke)

    p_list = sub.add_parser("list", parents=[common], help="Wypisz osoby i prefiksy hashy.")
    p_list.set_defaults(func=cmd_list)

    p_verify = sub.add_parser("verify", parents=[common], help="Zwaliduj magazyn tokenów.")
    p_verify.set_defaults(func=cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
