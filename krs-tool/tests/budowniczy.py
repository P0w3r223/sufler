"""Budowniczy odpisów syntetycznych.

Wolno z niego korzystać do ćwiczenia kodu i **nie wolno się na niego powoływać jako na
dowód**. Odpis stąd mówi wyłącznie o tym, co sami napisaliśmy; `tests/fixtures/odpis_traits.yaml`
przyjmuje twierdzenia tylko o plikach dostarczonych przez operatora, a test tego pilnuje.

Każdy zbudowany odpis niesie klucz `_syntetyczny`, którego rejestr nigdy nie wystawi — karta
z takiego pliku nosi widoczny znacznik i nie da się jej pomylić z prawdziwą.
"""

from __future__ import annotations

from typing import Any

# Oba zapisy okresu, jakie rejestr stosuje. Trzymamy je tutaj, bo to jedyna własność odpisu,
# którą rozpoznanie zaobserwowało na dwóch niezależnych próbkach — reszta kształtu to nasze
# założenia (docs/pomiary.md).
OKRES_KROPKOWY = "OD 01.01.2023 DO 31.12.2023"
OKRES_SLOWNY = "OD 1 STYCZNIA 2000 ROKU DO 31 GRUDNIA 2000 ROKU"
OKRES_NIEZNANY = "ZA ROK OBROTOWY 2019"


def wzmianka(data_zlozenia: str, okres: str) -> dict[str, str]:
    return {"dataZlozenia": data_zlozenia, "zaOkresOdDo": okres}


def zbuduj_odpis(
    *,
    numer: str = "0000123456",
    nazwa: str = "PRZYKLADOWA SPOLKA Z OGRANICZONA ODPOWIEDZIALNOSCIA",
    forma_prawna: str = "SPÓŁKA Z OGRANICZONĄ ODPOWIEDZIALNOŚCIĄ",
    rejestr: str = "P",
    stan_z_dnia: str = "10.09.2026",
    nip: str | None = "5252248481",
    regon: str | None = "146123456",
    dzien_konczacy_rok: str | None = "31.12",
    sprawozdania: tuple[dict[str, str], ...] = (),
    dzial4: Any = None,
    dzial5: Any = None,
    dzial6: Any = None,
    bez_dzialu: int | None = None,
) -> dict[str, Any]:
    """Odpis syntetyczny w kształcie, jaki zakłada `odpis/czytanie.py`."""
    identyfikatory: dict[str, str] = {}
    if nip is not None:
        identyfikatory["nip"] = nip
    if regon is not None:
        identyfikatory["regon"] = regon

    dzial3: dict[str, Any] = {
        "przedmiotDzialalnosci": [],
        "wzmiankiOZlozonychDokumentach": {
            "wzmiankaOZlozeniuRocznegoSprawozdaniaFinansowego": list(sprawozdania),
        },
    }
    if dzien_konczacy_rok is not None:
        dzial3["informacjaODniuKonczacymRokObrotowy"] = dzien_konczacy_rok

    dane: dict[str, Any] = {
        "dzial1": {
            "danePodmiotu": {
                "nazwa": nazwa,
                "formaPrawna": forma_prawna,
                "identyfikatory": identyfikatory,
            },
            "siedzibaIAdres": {"miejscowosc": "POZNAŃ"},
        },
        "dzial2": {
            "reprezentacja": [
                {"imiona": "JAN", "nazwisko": "KOWALSKI", "pesel": "80010112345"},
            ],
            "organNadzoru": [],
            "prokurenci": [],
        },
        "dzial3": dzial3,
        "dzial4": dzial4,
        "dzial5": dzial5,
        "dzial6": dzial6,
    }
    if bez_dzialu is not None:
        dane.pop(f"dzial{bez_dzialu}", None)

    return {
        "_syntetyczny": True,
        "odpis": {
            "naglowekA": {
                "numerKRS": numer,
                "rejestr": rejestr,
                "stanZDnia": stan_z_dnia,
                "dataOstatniegoWpisu": "27.08.2026",
                "numerOstatniegoWpisu": 42,
            },
            "dane": dane,
        },
    }
