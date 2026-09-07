"""Wypisz członków zespołu z ich AAD user-id — do ustawienia ONLY_USER_IDS i wyboru konta bota.

Potrzebny podwójnie. Po pierwsze `POWIADOMIENIA_ONLY_USER_IDS` wymaga identyfikatorów AAD.
Po drugie od A10 **logi i alerty nie wypisują nazwisk** — to jest miejsce, w którym operator
rozwiązuje identyfikator na człowieka: na żądanie, na własnym terminalu, bez zostawiania śladu
w kanale z retencją. Z wyniku wybierasz:
  - konto kierownicze, którym bot będzie pisał (musi mieć rolę `owner` — zapis do Shifts
    jest menedżerski); to konto logujesz przez `powiadomienia-teams --login`,
  - odbiorców przypomnień → `POWIADOMIENIA_ONLY_USER_IDS` (lista po przecinku).

Tylko odczyt po stronie Graph (`GET /me`, `GET /teams/{id}/members`) — nic nie wysyła i nie
dotyka pliku stanu. **Zapisuje natomiast cache tokenu MSAL**: `build_token_provider` utrwala go po
każdym pobraniu tokenu, bo ciche odświeżenie potrafi zrotować refresh-token. Stąd jedno
ograniczenie — nie uruchamiaj tego równolegle z `--login`.

Lokalnie (z katalogu projektu):
    uv run --no-sync python scripts/lista_czlonkow.py

Na serwerze (wdrożenie DOCKEROWE — to jest obowiązująca ścieżka, patrz deploy/README-docker.md):
    cd /opt/teams-shifts-reminder
    docker compose run --rm --entrypoint python powiadomienia /app/scripts/lista_czlonkow.py

Do 2026-09-07 stało tu `sudo -u powiadomienia /opt/teams-shifts-reminder/.venv/bin/python …`, czyli
instrukcja dla wariantu **systemd** (`deploy/powiadomienia-teams.service`). Na serwerze klienta nie
ma ani użytkownika `powiadomienia`, ani tego venva — polecenie kończyło się `user not found`,
a `deploy/DO-WYKONANIA.md` podawał obok wersję dockerową. Dwie instrukcje, jedna nieprawdziwa.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from powiadomienia_teams.config import ConfigError, Settings
from powiadomienia_teams.graph.auth import AuthExpiredError, build_token_provider
from powiadomienia_teams.graph.client import GraphClient


def _load_env() -> None:
    """Wczytaj `.env` z katalogu roboczego, jeśli jest.

    Ścieżka JAWNIE: `load_dotenv()` bez argumentu szuka od katalogu pliku wywołującego, a nie od
    CWD — dla skryptu uruchamianego spoza projektu nie znalazłby niczego. Na serwerze konfiguracja
    i tak przychodzi ze środowiska (`EnvironmentFile`), więc brak `.env` nie jest błędem.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = Path.cwd() / ".env"
    if env.exists():
        load_dotenv(env)


def main() -> int:
    # Konsola Windows bywa cp1250, a serwer bez LANG — POSIX/ASCII. Wymuszamy UTF-8, żeby polskie
    # znaki w nazwiskach nie wywalały skryptu UnicodeEncodeError.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    _load_env()
    # Węższa walidacja: ten skrypt wyłącznie CZYTA roster, więc nie ma powodu wymagać od niego
    # kompletu warunków wysyłki. Bramka pilotażu żąda identyfikatorów, które wypisuje właśnie ten
    # skrypt — pełne `validate()` zamykałoby operatora w kółku. Błąd konfiguracji to jedno zdanie,
    # nie ślad stosu: `from_env()` też potrafi go zgłosić (literówka w wartości logicznej).
    try:
        settings = Settings.from_env()
        settings.validate_dostep()
    except ConfigError as blad:
        print(f"Błąd konfiguracji: {blad}", file=sys.stderr)
        return 2

    provider = build_token_provider(settings)
    try:
        provider()
    except AuthExpiredError:
        print("Brak ważnego tokenu. Zaloguj się: powiadomienia-teams --login", file=sys.stderr)
        return 1

    with httpx.Client(timeout=30) as http:
        client = GraphClient(http, provider)
        client.refresh_auth()
        me_id = client.get_me()
        members = client.list_members(settings.team_id)

    print(f"\nZalogowane konto (»głos« bota): {me_id}\n")
    print(f"{'AAD user-id':38}  {'rola':8}  {'imię i nazwisko':28}  e-mail")
    print("-" * 108)
    for m in sorted(members, key=lambda x: x.display_name):
        rola = "owner" if "owner" in m.roles else "member"
        marker = "  <-- zalogowany" if m.user_id == me_id else ""
        print(f"{m.user_id:38}  {rola:8}  {m.display_name:28}  {m.email or '-'}{marker}")

    owners = [m for m in members if "owner" in m.roles]
    print(f"\nCzłonków: {len(members)}, w tym właścicieli: {len(owners)}")
    if me_id not in {m.user_id for m in members}:
        print(
            "\nUWAGA: zalogowane konto NIE jest członkiem tego zespołu — może czytać roster, ale "
            "nie nadaje się na »głos« bota. Zaloguj konto z listy właścicieli."
        )
    elif me_id not in {m.user_id for m in owners}:
        print(
            "\nUWAGA: zalogowane konto nie jest właścicielem zespołu — zapis zmian w Shifts "
            "prawdopodobnie skończy się błędem 403."
        )

    print("\nWłaściciele (kandydaci na konto bota):")
    for m in owners:
        print(f"  {m.user_id}  {m.display_name}  {m.email or '-'}")

    print("\nDo POWIADOMIENIA_ONLY_USER_IDS wklej wybrane id po przecinku (pusty = wszyscy).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
