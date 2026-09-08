"""Kształt odpowiedzi modelu — moduł czysty (ADR-0011, decyzja 3).

Model **nie** zwraca `Criteria`. Zwraca `AssistantAnswer`: strukturę pobłażliwą, którą dopiero
nasz kod tłumaczy na `Criteria` przez walidator z `criteria.py`. Powody są dwa. Po pierwsze
`Criteria` jest zamrożone i ma walidatory, które rzucają — schemat wygenerowany z niego stałby się
publicznym kontraktem promptu, a błąd walidacji wracałby jako błąd parsera SDK zamiast jako nasze
zdanie. Po drugie kolejność sprawdzeń ma znaczenie: kod PKD konfrontujemy najpierw ze słownikiem,
żeby komunikat brzmiał „kod 9999Z nie istnieje w PKD 2025", a nie „zły kształt".

Dwie rzeczy są **nieobecne w schemacie** i to jest tu najważniejsze:

- `max_rekordow` — `flow.prepare_fetch` pomija całą gałąź progu 50 tys., gdy limit jest ustawiony
  (pilnuje tego `tests/resilience/test_s9_large_count.py`). Model, który przeczytałby „kilka firm"
  jako `max_rekordow=50`, wyłączyłby scenariusz odporności 9 i nikt by tego nie zauważył.
- cokolwiek transportowego (`page`, `limit`, `base_url`, środowisko, token) — te pola i tak stoją
  poza `Criteria`, a powód zapisany w `docs/design/phase2_core.md` 2026-09-05 brzmiał dokładnie:
  „wewnątrz `Criteria` mógłby je wygenerować asystent fazy 4".

Zgody nie da się poszerzyć nie dlatego, że ktoś tego pilnuje, tylko dlatego, że **nie ma takiego
pola**, a `extra="forbid"` odrzuca próbę dopisania go.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class OgraniczenieKod(StrEnum):
    """Czego API nie potrafi — zbiór zamknięty (ADR-0011, decyzja 6).

    Model wybiera **które** ograniczenia dotyczą pytania; polskie zdanie dla każdego z nich
    układa `ui/texts.py`. Enum zamiast prozy jest tym, co pozwala potwierdzenie asertować
    w testach bez modelu — i tym, co realizuje wymaganie instrukcji, że podsumowanie generuje
    kod, a nie model.
    """

    SPOLKI_W_KRS = "SPOLKI_W_KRS"
    BRAK_DANYCH_FINANSOWYCH = "BRAK_DANYCH_FINANSOWYCH"
    DATA_TYLKO_ROZPOCZECIE = "DATA_TYLKO_ROZPOCZECIE"
    KONTAKTY_OPCJONALNE = "KONTAKTY_OPCJONALNE"
    BRAK_FILTRA_WIELKOSCI = "BRAK_FILTRA_WIELKOSCI"
    BRAK_FILTRA_BRANZY_POZA_PKD = "BRAK_FILTRA_BRANZY_POZA_PKD"
    TYLKO_JDG = "TYLKO_JDG"
    RAPORT_BEZ_WYKRESLONYCH = "RAPORT_BEZ_WYKRESLONYCH"
    # Dodane po przebiegu A5 (2026-09-07): model sam tłumaczy kody z PKD 2007 na 2025 — i robił
    # to **po cichu**. Ktoś, kto od lat wpisuje `62.01.Z`, widział inne kody i nie dowiadywał
    # się, że jego nie ma już w klasyfikacji.
    KOD_PKD_Z_INNEGO_ROCZNIKA = "KOD_PKD_Z_INNEGO_ROCZNIKA"


# Opis **dla modelu**: kiedy użyć którego kodu. Zdanie **dla operatora** stoi w `ui/texts.py` —
# to dwie różne publiczności i dwa różne teksty, i właśnie po to istnieje `texts`. Wspólne jest
# tylko to, że oba zbiory muszą mieć te same klucze co enum, czego pilnuje test.
OGRANICZENIA_DLA_MODELU: dict[OgraniczenieKod, str] = {
    OgraniczenieKod.SPOLKI_W_KRS: "użytkownik pyta o spółki (z o.o., akcyjne, jawne) — te są w KRS",
    OgraniczenieKod.BRAK_DANYCH_FINANSOWYCH: "pada przychód, zysk, obrót albo zatrudnienie",
    OgraniczenieKod.DATA_TYLKO_ROZPOCZECIE: "pada data inna niż data rozpoczęcia działalności",
    OgraniczenieKod.KONTAKTY_OPCJONALNE: "użytkownik prosi o telefon, e-mail albo stronę WWW",
    OgraniczenieKod.BRAK_FILTRA_WIELKOSCI: "pada wielkość firmy albo liczba pracowników",
    OgraniczenieKod.BRAK_FILTRA_BRANZY_POZA_PKD: "branża padła słownie i wyraziłeś ją kodem PKD",
    OgraniczenieKod.TYLKO_JDG: "pytanie sugeruje podmioty inne niż jednoosobowe działalności",
    OgraniczenieKod.RAPORT_BEZ_WYKRESLONYCH: "użytkownik pyta o firmy wykreślone w danym regionie",
    OgraniczenieKod.KOD_PKD_Z_INNEGO_ROCZNIKA: (
        "użytkownik podał kod PKD, którego nie ma w podanej klasyfikacji, a Ty użyłeś "
        "odpowiednika z niej — ustaw to ZAWSZE w takim wypadku"
    ),
}


class AssistantAnswer(BaseModel):
    """Surowa odpowiedź modelu. Pobłażliwa z założenia — surowość jest w `translate.py`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Krotki, nie listy: `frozen=True` na modelu zamraża przypisania do pól, ale nie ich
    # zawartość — `answer.pkd.append(...)` na liście nadal by przeszło, czyli deklaracja
    # niezmienności byłaby obietnicą, której typ nie dotrzymuje. Pydantic i tak przyjmuje
    # z JSON-a listę i konwertuje ją tutaj.
    wojewodztwo: tuple[str, ...] = ()
    powiat: tuple[str, ...] = ()
    gmina: tuple[str, ...] = ()
    miasto: tuple[str, ...] = ()
    ulica: tuple[str, ...] = ()
    kod: tuple[str, ...] = ()
    nazwa: tuple[str, ...] = ()
    imie: tuple[str, ...] = ()
    nazwisko: tuple[str, ...] = ()
    nip: tuple[str, ...] = ()
    regon: tuple[str, ...] = ()
    pkd: tuple[str, ...] = ()
    status: tuple[str, ...] = ()
    data_od: str | None = None
    data_do: str | None = None
    szczegoly: bool = False
    ograniczenia: tuple[OgraniczenieKod, ...] = ()


def _strict(node: Any) -> Any:
    """Domyka schemat: `additionalProperties: false` i wszystkie pola w `required`.

    Pydantic nie produkuje tego kształtu sam — pola z wartością domyślną nie trafiają do
    `required`, a `additionalProperties` w ogóle się nie pojawia. Strukturalne wyjście modelu
    wymaga obu, więc dopisujemy je rekurencyjnie i pilnujemy tego testem: schemat, który
    po cichu przestałby być domknięty, przepuszczałby pola spoza `AssistantAnswer`.
    """
    if isinstance(node, dict):
        # `title` i `description` wypadają celowo. Pydantic wstawia tam nazwy klas i **nasze
        # docstringi**, więc bez tego każde żądanie niosłoby do modelu „(ADR-0011, decyzja 6)"
        # i odniesienia do `ui/texts.py` — a zmiana docstringa po cichu zmieniałaby treść
        # promptu. `default` wypada, bo skoro wszystkie pola są w `required`, wartość domyślna
        # jest sprzecznością na wierzchu schematu.
        out = {
            key: _strict(value)
            for key, value in node.items()
            if key not in ("title", "description", "default")
        }
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
            out["required"] = sorted(out["properties"])
        return out
    if isinstance(node, list):
        return [_strict(item) for item in node]
    return node


def json_schema() -> dict[str, Any]:
    """Schemat do `output_config.format` — domknięty, deterministyczny, bez sieci."""
    return dict(_strict(AssistantAnswer.model_json_schema()))
