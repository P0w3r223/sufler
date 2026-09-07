"""Pomiar D1: czy wzorzec dałby INNĄ propozycję niż dzisiejsze „jak w zeszłym tygodniu".

`reminders/wzorzec.py` to 351 linii gotowego, udokumentowanego kodu, którego nic nie woła i nic
nie testuje — pozycja **D1** planu rozwoju. Rozstrzygnięcie „podłączyć czy skasować" ma zapaść na
LICZBACH z prawdziwej historii tego zespołu, nie w dyskusji: jeśli nikt w zespole nie chodzi
w rytmie przemiennym, podłączenie nie kupuje nic, a kosztuje adapter, ADR, złoty korpus, nowe
zdania w wiadomościach do ludzi i nowe ryzyko przy propozycji, która jest jedno „tak" od
nieodwracalnego zapisu (N1).

**Tylko odczyt.** `GET /me`, `GET /teams/{id}/schedule/shifts`, `.../timesOff`. Nic nie wysyła,
nic nie zapisuje, nie bierze blokady pliku stanu i nie dotyka pamięci ani stanu usługi — można
uruchomić przy działającej usłudze.

**Anonimizacja jest wbudowana, nie opcjonalna.** Na wyjściu ludzie występują jako `os1`, `os2`…;
identyfikatory AAD, nazwiska i adresy nie opuszczają procesu. To jest wynik, który trafi do
`docs/plan-rozwoju.md` przy pozycji D1, więc nie ma prawa nieść danych osobowych (A10).

Lokalnie (z katalogu projektu):
    uv run --no-sync python scripts/zbierz_historie.py
    uv run --no-sync python scripts/zbierz_historie.py --tygodni 12

Na serwerze, na koncie usługi:
    sudo -u powiadomienia /opt/teams-shifts-reminder/.venv/bin/python \
        /opt/teams-shifts-reminder/scripts/zbierz_historie.py

Kryterium decyzji (zapisane w planie razem z wynikiem):
    ≥ 2 osoby z pewnością WYSOKA i INNĄ propozycją  → podłączyć (defekt realny i powtarzalny)
    0–1 osoba                                       → skasować albo zaparkować, z tą liczbą
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from powiadomienia_teams.config import ConfigError, Settings
from powiadomienia_teams.domain.models import Shift, TimeOff
from powiadomienia_teams.graph.auth import AuthExpiredError, build_token_provider
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.reminders.propose import proposal_from_last_week
from powiadomienia_teams.reminders.wzorzec import (
    DzienGrafiku,
    Interwal,
    Pewnosc,
    TydzienHistorii,
    wnioskuj,
)
from powiadomienia_teams.scheduler.weekly import this_week_monday

_UTC = timezone.utc


def _load_env() -> None:
    """Wczytaj `.env` z katalogu roboczego, jeśli jest — ścieżka jawnie, jak w `lista_czlonkow`."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = Path.cwd() / ".env"
    if env.exists():
        load_dotenv(env)


def _interwal(zmiana: Shift, tz: ZoneInfo) -> Interwal:
    """Zmiana → interwał ściany zegara LOKALNEGO, z motywem sprowadzonym do jednej postaci.

    Normalizacja motywu jest tu decyzją, nie kosmetyką: `None`, `""` i różnica wielkości liter to
    dla Graph ta sama informacja, a dla reguły alfabetu — trzy różne wartości, więc bez tego
    alfabet rozszczepiłby się i pewność spadałaby bez powodu.
    """
    start = zmiana.start.astimezone(tz)
    koniec = zmiana.end.astimezone(tz)
    motyw = (zmiana.theme or "").strip().lower() or None
    return Interwal(
        poczatek=start.timetz().replace(tzinfo=None),
        koniec=koniec.timetz().replace(tzinfo=None),
        motyw=motyw,
    )


def _historia_osoby(
    member_id: str,
    zmiany: list[Shift],
    wolne: list[TimeOff],
    poniedzialki: list[date],
    tz: ZoneInfo,
) -> list[TydzienHistorii]:
    """Historia jednej osoby, od NAJŚWIEŻSZEGO tygodnia — taki kontrakt ma `wnioskuj`.

    Dzień przypisania to dzień ROZPOCZĘCIA zmiany, ten sam niezmiennik co wszędzie w tym kodzie
    (nocka 22:00–06:00 należy do piątku, nie do soboty).
    """
    per_tydzien: dict[date, dict[int, set[Interwal]]] = defaultdict(lambda: defaultdict(set))
    urlopy: dict[date, set[int]] = defaultdict(set)
    for zmiana in zmiany:
        if zmiana.member_id != member_id:
            continue
        start = zmiana.start.astimezone(tz)
        pon = (start - timedelta(days=start.weekday())).date()
        per_tydzien[pon][start.weekday()].add(_interwal(zmiana, tz))
    for wpis in wolne:
        if wpis.member_id != member_id:
            continue
        start = wpis.start.astimezone(tz)
        koniec = wpis.end.astimezone(tz)
        dzien = start
        while dzien < koniec:
            pon = (dzien - timedelta(days=dzien.weekday())).date()
            urlopy[pon].add(dzien.weekday())
            dzien += timedelta(days=1)
    return [
        TydzienHistorii(
            poczatek=pon,
            dni={wd: DzienGrafiku(iv) for wd, iv in per_tydzien.get(pon, {}).items()},
            dni_urlopu=frozenset(urlopy.get(pon, set())),
        )
        for pon in poniedzialki
    ]


def _z_propozycji(schedule, tz: ZoneInfo) -> dict[int, DzienGrafiku]:
    """Dzisiejsza propozycja („jak w zeszłym tygodniu") w tej samej postaci co wynik `wnioskuj`."""
    dni: dict[int, set[Interwal]] = defaultdict(set)
    for zmiana in schedule.shifts:
        start = zmiana.start.astimezone(tz)
        dni[start.weekday()].add(_interwal(zmiana, tz))
    return {wd: DzienGrafiku(iv) for wd, iv in dni.items()}


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="Pomiar D1 — wzorzec kontra »jak w zeszłym tygodniu«"
    )
    parser.add_argument("--tygodni", type=int, default=8, help="ile tygodni historii (domyślnie 8)")
    args = parser.parse_args()

    _load_env()
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

    tz = settings.tz
    teraz = datetime.now(_UTC)
    biezacy_pon = this_week_monday(teraz, tz)
    # W-1 … W-n: tygodnie ZAMKNIĘTE, od najświeższego. Bieżący pomijamy — jest niepełny, więc
    # wchodziłby do alfabetu jako dzień wolny w każdym dniu, który jeszcze nie nadszedł.
    poniedzialki = [(biezacy_pon - timedelta(weeks=i + 1)).date() for i in range(args.tygodni)]
    okno_od = biezacy_pon - timedelta(weeks=args.tygodni)

    with httpx.Client(timeout=60) as http:
        client = GraphClient(http, provider)
        client.refresh_auth()
        members = client.list_members(settings.team_id)
        me_id = client.get_me()
        zmiany = list(
            client.read_shifts(
                settings.team_id, okno_od.astimezone(_UTC), biezacy_pon.astimezone(_UTC)
            )
        )
        wolne = list(
            client.read_time_off(
                settings.team_id, okno_od.astimezone(_UTC), biezacy_pon.astimezone(_UTC)
            )
        )

    print(f"\nPomiar D1 — {args.tygodni} tygodni historii, zespół {len(members)} osób")
    print(f"Okno: {poniedzialki[-1]} … {poniedzialki[0]} (tygodnie zamknięte)")
    print(f"Zmian w oknie: {len(zmiany)}, wpisów czasu wolnego: {len(wolne)}\n")

    print(
        f"{'osoba':6}  {'pewność':9}  {'podstawa':22}  "
        f"{'inna niż »zeszły tydzień«':26}  tygodni z danymi"
    )
    print("-" * 100)
    licznik = Counter()
    for nr, czlonek in enumerate(sorted(members, key=lambda m: m.user_id), start=1):
        if czlonek.user_id == me_id:
            continue  # konto bota nie jest przedmiotem pomiaru
        etykieta = f"os{nr}"
        historia = _historia_osoby(czlonek.user_id, zmiany, wolne, poniedzialki, tz)
        propozycja = wnioskuj(historia)
        zeszly = proposal_from_last_week(
            czlonek.user_id,
            [
                s
                for s in zmiany
                if s.member_id == czlonek.user_id
                and s.start.astimezone(tz).date() >= poniedzialki[0]
            ],
            poniedzialki[0] + timedelta(days=7),
            tz=tz,
        )
        dzisiejsza = _z_propozycji(zeszly, tz)
        inna = bool(propozycja.dni) and propozycja.dni != dzisiejsza
        z_danymi = sum(1 for t in historia if t.dni or t.dni_urlopu)
        licznik[(propozycja.pewnosc, inna)] += 1
        print(
            f"{etykieta:6}  {propozycja.pewnosc.value:9}  {propozycja.podstawa.value:22}  "
            f"{'TAK' if inna else 'nie':26}  {z_danymi}/{args.tygodni}"
        )

    mocne = licznik[(Pewnosc.WYSOKA, True)]
    print(f"\nOsób z pewnością WYSOKA i INNĄ propozycją: {mocne}")
    print(
        "Kryterium: ≥2 → podłączyć wzorzec do `reminders/propose.py` (ADR + złoty korpus + testy); "
        "0–1 → skasować moduł albo zaparkować, zapisując TĘ liczbę przy pozycji D1 planu."
    )
    print("Wynik wpisz do `docs/plan-rozwoju.md`, żeby za pół roku nikt nie zaczynał od zera.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
