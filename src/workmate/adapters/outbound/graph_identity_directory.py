"""Katalog tożsamości: mapa AAD → Jira z jawnego pliku (ADR 0042/0054).

Dwa systemy, dwa identyfikatory, żaden nie wynika z pozostałego: ``aad_user_id`` (konto Teams,
używane też do autoryzacji ``/notatka``) i ``jira_user`` (e-mail albo ``accountId`` na Jirze, do
"moich zadań"). Mapowanie jest jawnym plikiem YAML — żaden z dwóch systemów nie zna konta w drugim.

**Fail-closed.** Nieznany ``aad_user_id`` → ``None``: brak autoryzacji notatki, brak listy zadań.
Nigdy nie dopasowujemy po nazwisku.

Format ``identities.yaml``::

    EMP-042:
      aad_user_id: 8a1f-...
      jira_user: mikolaj@example.org
      display_name: Mikołaj Anonimowicz   # opcjonalnie
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

        candidates = [
            (p.display_name, p) for p in self._people.values() if p.display_name
        ]
        person, _ambiguous = match_name(candidates, name)
        return person


def _load_map(path: Path) -> dict[str, Person]:
    """Wczytaj mapę tożsamości; brak pliku albo brak wymaganego pola = TWARDY błąd startu.

    Fail-fast przy starcie, nie przy pierwszym użyciu: niekompletna mapa oznacza, że część
    ludzi po cichu nie dostanie nic, a to najgorszy rodzaj awarii — niewidoczny.
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
        missing = [field for field in ("aad_user_id", "jira_user") if not entry.get(field)]
        if missing:
            raise ValueError(f"wpis {source_id!r} w {path} nie ma pól: {', '.join(missing)}.")
        people[str(source_id)] = Person(
            source_id=str(source_id),
            aad_user_id=str(entry["aad_user_id"]),
            jira_user=str(entry["jira_user"]),
            display_name=str(entry.get("display_name") or ""),
        )
    _reject_shared_identifiers(people, path)
    logger.info("Mapa tożsamości %s: %d osób.", path, len(people))
    return people


def _reject_shared_identifiers(people: dict[str, Person], path: Path) -> None:
    """Dwie osoby NIE MOGĄ dzielić konta Teams ani konta Jiry — twardy błąd startu.

    Skopiowany w YAML-u blok bez podmiany ``jira_user`` sprawiłby, że lista zadań drugiej osoby
    pokazuje CUDZE zgłoszenia; współdzielony ``aad_user_id`` autoryzowałby notatkę pod cudzym
    imieniem albo pokazał komuś cudzą listę zadań.
    """
    for field in ("aad_user_id", "jira_user"):
        seen: dict[str, str] = {}
        for person in people.values():
            value = getattr(person, field)
            if value in seen:
                raise ValueError(
                    f"mapa tożsamości {path}: {field}={value!r} występuje u dwóch osób "
                    f"({seen[value]!r} i {person.source_id!r}) — każdy identyfikator musi "
                    "należeć do dokładnie jednej osoby."
                )
            seen[value] = person.source_id
