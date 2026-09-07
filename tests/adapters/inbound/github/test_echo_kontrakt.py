"""Kontrakt między DWOMA modułami: klucz zapisany przez drzwi zapisu to klucz, którego szuka
poller.

Strażnik pętli self-ping (ADR 0071 decyzja 6) stoi na jednym założeniu: echo zostawione przez
``GithubWriteService`` da się odnaleźć po ``(external_id, rodzaj)`` wyliczonym z payloadu, który
poller dostaje z GitHuba. Oba klucze powstają w INNYCH modułach, z innych struktur, i nic ich ze
sobą nie wiąże poza tym, że przypadkiem czytają to samo pole odpowiedzi API.

**Cicha zmiana po którejkolwiek stronie wyłączyłaby strażnika bez jednego czerwonego testu** —
i to jest dokładnie ta klasa wady, którą ten projekt ściga: zabezpieczenie, którego awaria nie ma
objawu. Bot zacząłby komentować własne zdarzenia, a jedynym śladem byłaby pętla na kanale.

Sondy niżej porównują klucze **WYLICZONE PRZEZ OBIE STRONY**, a nie przepisane do asercji. Test
z wpisanym na sztywno ``"101"`` przechodziłby po zmianie, która ten kontrakt łamie.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from workmate.adapters.inbound.github import selection
from workmate.core.application.events import EventService
from workmate.core.application.github import GithubWriteService

_ZAPIS = (
    Path(__file__).resolve().parents[4] / "src" / "workmate" / "core" / "application" / "github.py"
)


class _Magazyn:
    """Atrapa ``EventStore`` notująca, co drzwi zapisu odłożyły jako echo."""

    def __init__(self) -> None:
        self.rows: list[Any] = []

    def exists(self, source: str, external_id: str, kind: str) -> bool:
        return False

    def append(self, event):
        self.rows.append(event)
        return event

    def recent(self, **_kw):
        return []

    def read_since(self, *_a, **_kw):
        return []


class _Klient:
    """Atrapa GitHub REST (write) — odpowiedzi w kształcie, jaki oddaje GitHub."""

    def __init__(self, *, numer: int = 101, komentarz_id: int = 9) -> None:
        self._numer = numer
        self._komentarz_id = komentarz_id

    def create_issue(self, owner, repo, title, body, labels):
        return {
            "number": self._numer,
            "html_url": f"http://gh/{self._numer}",
            "created_at": "2026-07-15T12:00:00Z",
        }

    def create_comment(self, owner, repo, issue_number, body):
        return {
            "id": self._komentarz_id,
            "html_url": f"http://gh/c/{self._komentarz_id}",
            "created_at": "2026-07-15T12:05:00Z",
        }


def _echo(wywolaj) -> Any:
    magazyn = _Magazyn()
    service = GithubWriteService(
        _Klient(), owner="biap", repo="workmate", events=EventService(magazyn)
    )
    wywolaj(service)
    assert len(magazyn.rows) == 1, "drzwi zapisu nie zostawiły echa"
    return magazyn.rows[0]


def _issue_z_githuba(numer: int) -> dict[str, Any]:
    return {
        "number": numer,
        "title": "Prośba z Teams",
        "body": "treść",
        "html_url": f"http://gh/{numer}",
        "user": {"login": "bot"},
        "created_at": "2026-07-15T12:00:00Z",
    }


def _komentarz_z_githuba(komentarz_id: int) -> dict[str, Any]:
    return {
        "id": komentarz_id,
        "body": "treść",
        "html_url": f"http://gh/c/{komentarz_id}",
        "user": {"login": "bot"},
        "issue_url": "http://api/repos/o/r/issues/101",
        "created_at": "2026-07-15T12:05:00Z",
    }


def test_klucz_echa_zgloszenia_zgadza_sie_z_kluczem_mappera():
    """Obie strony liczą klucz z ``number`` tej samej odpowiedzi — sonda tego nie zakłada, tylko
    porównuje wyniki obu wyliczeń."""
    echo = _echo(lambda s: s.create_issue("Prośba z Teams", "treść"))
    z_pollera = selection.map_issue(_issue_z_githuba(101))

    assert z_pollera is not None
    assert echo.external_id == z_pollera.external_id
    assert selection._ECHO_KINDS[z_pollera.kind] == echo.kind


def test_klucz_echa_komentarza_zgadza_sie_z_kluczem_mappera():
    echo = _echo(lambda s: s.create_comment(101, "treść"))
    z_pollera = selection.map_comment(_komentarz_z_githuba(9))

    assert z_pollera is not None
    assert echo.external_id == z_pollera.external_id
    assert selection._ECHO_KINDS[z_pollera.kind] == echo.kind


def _rodzaje_echa_drzwi_zapisu() -> set[str]:
    """Rodzaje echa, jakie emituje ``GithubWriteService`` — z AST, nie z listy.

    Iterujemy po RZECZY CHRONIONEJ (ADR 0073): zdolnościach zapisu, a nie po tabeli, która ma je
    pokrywać. Nowa droga zapisu zostawiająca echo zerwie sondę niżej, nawet jeśli nikt nie
    pomyśli o strażniku pętli — a to jest jedyny moment, w którym ktoś o nim pomyśli.
    """
    drzewo = ast.parse(_ZAPIS.read_text(encoding="utf-8"), filename=str(_ZAPIS))
    rodzaje: set[str] = set()
    for wezel in ast.walk(drzewo):
        if isinstance(wezel, ast.Call):
            for kw in wezel.keywords:
                if (
                    kw.arg == "kind"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    rodzaje.add(kw.value.value)
    return rodzaje


def test_kazdy_rodzaj_echa_drzwi_zapisu_jest_w_tabeli_straznika():
    """Rodzaj echa, którego tabela nie zna, to zdolność zapisu bez strażnika pętli.

    Sonda ma własną kontrolę pozytywną: pusty wynik ekstraktora przechodziłby asercję o zawieraniu
    w milczeniu, więc najpierw pytamy, czy w ogóle coś znalazł.
    """
    rodzaje = _rodzaje_echa_drzwi_zapisu()

    assert rodzaje, "ekstraktor nie znalazł żadnego echa — sonda oślepła, nie kod wyzdrowiał"
    assert rodzaje <= set(selection._ECHO_KINDS.values())


def test_tabela_strazniku_nie_wymyslila_rodzaju_ktorego_nikt_nie_pisze():
    """Komplet w drugą stronę: wpis bez zdolności zapisu to tabela, która przestała opisywać kod
    — a przy strażniku pętli to znaczy „pomijamy zdarzenia z powodu, który nie istnieje"."""
    assert set(selection._ECHO_KINDS.values()) <= _rodzaje_echa_drzwi_zapisu()
