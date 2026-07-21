"""Katalog tożsamości (ADR 0035) — Graph daje konto Teams, konfiguracja daje konto Jiry.

Trzy systemy, trzy identyfikatory i żaden nie wynika z pozostałych:

- ``source_id`` — klucz ze źródła godzin (numer pracownika, login RCP, cokolwiek);
- ``aad_user_id`` — adres czatu 1:1 w Teams;
- ``jira_user`` — e-mail albo ``accountId`` do kolumny ``User`` arkusza.

Graph (``GET /teams/{id}/members``, scope ``TeamMember.Read.All``) dostarcza AAD i nazwę
wyświetlaną oraz WALIDUJE, że osoba nadal jest w zespole. Konta Jiry Graph nie zna — bywa nim
prywatny adres spoza tenanta — więc mapowanie na nie jest jawnym plikiem YAML.

**Fail-closed.** Nieznany ``source_id``, brak konta Jiry albo osoba spoza zespołu → ``None``,
czyli żaden plik i żadna wiadomość. Nigdy nie dopasowujemy po nazwisku: zły ``jira_user`` wpisze
czyjeś godziny na CUDZE konto Jiry przy imporcie, a worklogi są create-only i nieusuwalne
narzędziem (ADR 0034). Brak wiadomości jest naprawialny; cudzy czas w czyjejś ewidencji nie.

Format ``identities.yaml``::

    EMP-042:
      aad_user_id: 8a1f-...
      jira_user: mikolaj@example.org
      display_name: Mikołaj Anonimowicz   # opcjonalnie, nadpisuje nazwę z Graph
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from workmate.core.domain.timesheet import Person

logger = logging.getLogger(__name__)

_GRAPH = "https://graph.microsoft.com/v1.0"


class YamlIdentityDirectory:
    """Mapowanie wyłącznie z pliku — bez Graph. Działa offline i na dowolnym tenancie."""

    def __init__(self, path: Path) -> None:
        self._people = _load_map(path)
        self._path = path

    def resolve(self, source_id: str) -> Person | None:
        entry = self._people.get(source_id)
        if entry is None:
            logger.warning(
                "Brak %s w mapie tożsamości %s — pomijam (fail-closed).", source_id, self._path
            )
            return None
        return entry


class GraphIdentityDirectory:
    """Mapa z pliku + weryfikacja członkostwa w zespole przez Graph (nazwa wyświetlana z Graph).

    Członków pobieramy RAZ przy tworzeniu (przebieg trwa sekundy, a lista zmienia się rzadko)
    i trzymamy w pamięci — kilkanaście osób, więc koszt jest żaden, a każdy ``resolve`` bez
    wywołania sieciowego.
    """

    def __init__(self, path: Path, members: dict[str, str]) -> None:
        self._people = _load_map(path)
        self._members = members
        self._path = path

    def resolve(self, source_id: str) -> Person | None:
        entry = self._people.get(source_id)
        if entry is None:
            logger.warning(
                "Brak %s w mapie tożsamości %s — pomijam (fail-closed).", source_id, self._path
            )
            return None
        if entry.aad_user_id not in self._members:
            # Osoba wypisana z zespołu (odejście, zmiana projektu) — nie wysyłamy jej nic,
            # a operator widzi to w logu. Cichy zapis byłby gorszy: arkusz powstałby dla kogoś,
            # kto już nie jest odbiorcą.
            logger.warning(
                "Osoba %s (%s) nie jest członkiem zespołu — pomijam (fail-closed).",
                source_id,
                entry.aad_user_id,
            )
            return None
        # Nazwa z Graph jest ŚWIEŻSZA niż z pliku (zmiana nazwiska), ale jawny wpis wygrywa.
        return entry.model_copy(
            update={"display_name": entry.display_name or self._members[entry.aad_user_id]}
        )


def fetch_team_members(client: Any, team_id: str, token: str) -> dict[str, str]:
    """Pobierz członków zespołu: ``{aad_user_id: display_name}`` (``TeamMember.Read.All``).

    Sync ``httpx.Client`` — ten przebieg jest wsadowy, nie ma pętli async do nieblokowania.
    Stronicowanie po ``@odata.nextLink`` z twardym capem: lista zespołu to kilkanaście osób,
    więc dziesięć stron to zapas ponad potrzebę i backstop przed pętlą.
    """
    members: dict[str, str] = {}
    url: str | None = f"{_GRAPH}/teams/{team_id}/members"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    for _ in range(10):
        if not url:
            break
        response = client.get(url, headers=headers)
        response.raise_for_status()
        body = response.json()
        for item in body.get("value", []):
            if not isinstance(item, dict):
                continue
            user_id = str(item.get("userId") or "")
            if user_id:
                members[user_id] = str(item.get("displayName") or "")
        url = body.get("@odata.nextLink")
    logger.info("Zespół %s: %d członków z Graph.", team_id, len(members))
    return members


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

    Fail-closed pilnował dotąd wyłącznie osi „brak wpisu". Oś „ten sam identyfikator u dwóch
    osób" była otwarta, a to właśnie ona jest nieodwracalna: skopiowany w YAML-u blok bez
    podmiany ``jira_user`` sprawia, że arkusz drugiej osoby ma w KAŻDEJ komórce cudze konto,
    więc jej tydzień wjeżdża do Jiry na cudze nazwisko — create-only, bez usuwania z poziomu
    narzędzi. Ten sam ``aad_user_id`` wysyła komuś cudzą tabelę godzin.

    Strażniki ``assert_single_person`` tego nie łapią: sprawdzają JEDNORODNOŚĆ zestawienia,
    a oba zestawienia są wewnętrznie spójne — po prostu wskazują na złą osobę.
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
