"""Katalog tożsamości: mapa AAD → Jira z jawnego pliku (ADR 0042/0054).

Dwa systemy, dwa identyfikatory, żaden nie wynika z pozostałego: ``aad_user_id`` (konto Teams,
używane też do autoryzacji ``/notatka``) i ``jira_user`` (e-mail albo ``accountId`` na Jirze, do
"moich zadań"). Mapowanie jest jawnym plikiem YAML — żaden z dwóch systemów nie zna konta w drugim.

**Fail-closed.** Nieznany ``aad_user_id`` → ``None``: brak autoryzacji notatki, brak listy zadań.
Nigdy nie dopasowujemy po nazwisku.

**Wymagane jest wyłącznie ``aad_user_id``** (ADR 0070 §1). Wpis bez ``jira_user`` to osoba, która
NIE MA konta Jira — pełny członek pionu, bez narzędzia ``Jira``. Nie jest to wpis niekompletny do
uzupełnienia: dopisanie tam cudzego konta pokazałoby tej osobie CUDZE zadania.

Format ``identities.yaml``::

    EMP-042:
      aad_user_id: 8a1f-...
      jira_user: mikolaj@example.org
      display_name: Mikołaj Anonimowicz   # opcjonalnie

    EMP-051:
      aad_user_id: 4c7d-...
      display_name: Tadeusz Anonimowski  # bez konta Jira: ``jira_user`` pominięte (ADR 0070)
"""

from __future__ import annotations

import logging
from pathlib import Path

from workmate.core.domain.identity import Person

logger = logging.getLogger(__name__)


class YamlIdentityDirectory:
    """Mapowanie wyłącznie z pliku — bez Graph. Działa offline i na dowolnym tenancie."""

    def __init__(self, path: Path) -> None:
        self._people = _load_map(path)
        self._by_aad = {p.aad_user_id: p for p in self._people.values() if p.aad_user_id}

    def resolve_by_aad_user_id(self, aad_user_id: str) -> Person | None:
        """Osoba adresowana danym kontem Teams albo ``None`` (fail-closed)."""
        return self._by_aad.get(aad_user_id)

    def resolve_by_display_name(self, name: str) -> Person | None:
        """Osoba o podanym imieniu i nazwisku albo ``None`` (fail-closed przy niejednoznaczności).

        Dopasowanie po nazwisku jest tu BEZPIECZNE, bo zbiór kandydatów to zaufana mapa tożsamości
        (nie zgadywanie konta w Jirze): docelowe ``jira_user`` i tak pochodzi z pliku. Używane przez
        „zadania członka zespołu" na Teams. Osoby bez ``display_name`` są pomijane; brak dokładnego
        albo jednoznacznego trafienia → ``None`` (wołający degraduje do czytelnej odmowy).
        """
        from workmate.core.domain.names import match_name

        candidates = [(p.display_name, p) for p in self._people.values() if p.display_name]
        person, _ambiguous = match_name(candidates, name)
        return person


def _load_map(path: Path) -> dict[str, Person]:
    """Wczytaj mapę tożsamości; brak pliku albo brak ``aad_user_id`` = TWARDY błąd startu.

    Fail-fast przy starcie, nie przy pierwszym użyciu: niekompletna mapa oznacza, że część
    ludzi po cichu nie dostanie nic, a to najgorszy rodzaj awarii — niewidoczny.

    Wymagane jest TYLKO ``aad_user_id`` (ADR 0070 §1). Brak ``jira_user`` jest legalnym wpisem
    osoby bez konta Jira, nie brakiem danych — do 2026-09-04 wywracał start, i to trzymało bramkę
    odczytu bazy wiedzy wyłączoną, bo jej włączenie odcięłoby taką osobę od notatek.
    """
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ValueError(f"brak pliku mapy tożsamości: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"mapa tożsamości {path} jest uszkodzona: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"mapa tożsamości {path} musi być słownikiem 'source_id: {{...}}'.")

    people: dict[str, Person] = {}
    for source_id, entry in raw.items():
        if not isinstance(entry, dict):
            raise ValueError(f"wpis {source_id!r} w {path} musi być słownikiem.")
        if not entry.get("aad_user_id"):
            raise ValueError(
                f"wpis {source_id!r} w {path} nie ma pola 'aad_user_id' — to ono adresuje "
                "człowieka i po nim rozstrzyga każda bramka autoryzacji. Pole 'jira_user' jest "
                "opcjonalne (ADR 0070 §1), 'aad_user_id' nie."
            )
        people[str(source_id)] = Person(
            source_id=str(source_id),
            aad_user_id=str(entry["aad_user_id"]),
            jira_user=str(entry.get("jira_user") or ""),
            display_name=str(entry.get("display_name") or ""),
        )
    _reject_shared_identifiers(people, path)
    logger.info("Mapa tożsamości %s: %d osób.", path, len(people))
    tylko_teams = sorted(sid for sid, person in people.items() if not person.jira_user)
    if tylko_teams:
        # Wpis w mapie NADAJE zdolności — bazę wiedzy, notatki ze spotkań, POWŁOKĘ i mutację
        # notatek (ADR 0070 §3) — i robi to niezależnie od flagi odczytu. Wpis „tylko Teams"
        # różni się od pozostałych wyłącznie brakiem narzędzia Jira, więc bez tej linii jego
        # obecność nie zostawiałaby przy starcie żadnego śladu.
        logger.info(
            "Mapa tożsamości %s: %d bez konta Jira (%s) — pełne członkostwo, bez narzędzia Jira.",
            path,
            len(tylko_teams),
            ", ".join(tylko_teams),
        )
    return people


def _reject_shared_identifiers(people: dict[str, Person], path: Path) -> None:
    """Dwie osoby NIE MOGĄ dzielić konta Teams ani konta Jiry — twardy błąd startu.

    Skopiowany w YAML-u blok bez podmiany ``jira_user`` sprawiłby, że lista zadań drugiej osoby
    pokazuje CUDZE zgłoszenia; współdzielony ``aad_user_id`` autoryzowałby notatkę pod cudzym
    imieniem albo pokazał komuś cudzą listę zadań.

    Puste wartości są POMIJANE (ADR 0070 §2): brak konta Jira to nie jest konto współdzielone.
    Bez tego pominięcia DRUGA osoba bez Jiry kładłaby start błędem o zdublowanym identyfikatorze —
    zatrzymaniem fail-closed spowodowanym tym, że dwoje ludzi poprawnie nie ma niczego. Pominięcie
    musi dotyczyć WARTOŚCI, nie całego pola: ``continue`` na polu zdjęłoby ochronę przed dwiema
    osobami o tym samym, niepustym ``jira_user``, czyli przed usterką z akapitu wyżej. Dla
    ``aad_user_id`` gałąź pominięcia jest dziś NIEOSIĄGALNA z ``_load_map`` (puste pole odrzuca
    walidacja wpisu wcześniej) — stoi tam dla symetrii pętli, nie jako czynna ochrona.

    **Zakres tej ochrony jest węższy, niż brzmi: porównujemy IDENTYCZNE napisy.** Zmierzone:
    ``X@e.pl`` obok ``x@e.pl`` oraz ``' x@e.pl'`` obok ``'x@e.pl'`` przechodzą jako dwa różne
    konta, choć Jira Cloud dopasowuje e-maile bez rozróżniania wielkości liter. To luka SPRZED
    ADR 0070, świadomie tu nie domykana: normalizacja klucza porównania bez normalizacji
    ``_by_aad`` dałaby naprawę ASYMETRYCZNĄ — a to jest w tym projekcie znany sposób na drugą
    usterkę zamiast jednej. Domknięcie wymaga przejścia WSZYSTKICH punktów porównania
    identyfikatorów naraz, czyli własnego kroku.
    """
    for field in ("aad_user_id", "jira_user"):
        seen: dict[str, str] = {}
        for person in people.values():
            value = getattr(person, field)
            if not value:
                continue
            if value in seen:
                raise ValueError(
                    f"mapa tożsamości {path}: {field}={value!r} występuje u dwóch osób "
                    f"({seen[value]!r} i {person.source_id!r}) — każdy identyfikator musi "
                    "należeć do dokładnie jednej osoby."
                )
            seen[value] = person.source_id
