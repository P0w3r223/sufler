"""Kanoniczna postać identyfikatora wpisu — moduł czysty, bez sieci i bez bazy.

Rejestr zwraca **jeden** identyfikator w **dwóch** pisowniach: `/firmy` i `/firma` wielkimi
literami, `/zmiana` małymi (zmierzone, `docs/decisions.md`). Program traktował pisownię jak
tożsamość, więc `aktualizuj` zapisywał każdą zmienioną firmę dwa razy — raz jako pustą
zaślepkę przypiętą do runu, raz jako komplet danych przypięty do niczego — a `stale_detail_ids`
nie mógł trafić w cache ani razu. Nocny przebieg z 2026-09-08 kupił 13 401 rekordów za 2 681
żądań i nie pokazał żadnego (ADR-0013).

Stąd niezmiennik: **`id` wpisu jest wartością, nie napisem.** Dwa rekordy opisują ten sam
wpis wtedy i tylko wtedy, gdy ich identyfikatory są równe po kanonizacji; kanonizacja ma
jedno miejsce (ten moduł), stosuje się ją na wejściu (`client._records_of`), a `KanonicznyId`
sprawia, że mypy strict pyta o nią za nas przy każdym przejściu do bazy.

Wielkie litery, bo taką pisownię rejestr sam produkuje na obu endpointach niosących dane —
narzędzie nigdy nie wysyła zapisu, którego API by nie zwróciło.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Final, NewType

KanonicznyId = NewType("KanonicznyId", str)

GUID_WPISU = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
"""Kształt identyfikatora wpisu: 8-4-4-4-12 i **wyłącznie szesnastkowo**.

Ta ostatnia część jest nośna, nie ozdobna. Identyfikator z `/raporty` ma ten sam kształt
(`F3Aj3APe-9rud-A9rR-IId9-jMe3FedeR3e9`), ale nie jest szesnastkowy i jest istotny co do
wielkości liter, bo wchodzi wprost do adresu pobrania archiwum. Identyfikatory wierszy
raportu (`NIP:…`, `REGON:…`, `HASH:<małe hex>`) też nie są GUID-ami. Rozluźnienie tego
wzorca do `[0-9A-Za-z]` zepsułoby pobieranie raportów i dorobiło wierszom raportu drugą
tożsamość — czyli ten sam defekt, tylko na drugim źródle.
"""


def kanoniczny_id(value: str) -> KanonicznyId:
    """Identyfikator wpisu w postaci kanonicznej; cokolwiek innego przechodzi bez zmian."""
    return KanonicznyId(value.upper() if GUID_WPISU.fullmatch(value) else value)


def kanoniczne_id(values: Iterable[str]) -> list[KanonicznyId]:
    """Kanonizuje sekwencję identyfikatorów, zachowując kolejność i powtórzenia."""
    return [kanoniczny_id(v) for v in values]


PREFIKS_TRESCI: Final = "HASH:"
"""Prefiks tożsamości liczonej z treści. Stała, bo czyta ją także `pipeline`, żeby odróżnić
sklejenie po NIP-ie od sklejenia po treści — a prefiks wpisany tam z ręki rozjechałby się
z tym modułem bez żadnego obserwatora."""

POLA_TOZSAMOSCI_RAPORTU: Final = ("nazwa", "nazwisko", "imię", "data rozpoczęcia")
"""Pola, z których liczy się tożsamość wiersza raportu bez NIP i bez REGON (ADR-0016).

Lista jest **jawna i zamknięta**, a nie „wszystkie kolumny poza `Lp.`". Status, kody PKD,
telefon i adres zmieniają się w trakcie życia firmy, więc tożsamość liczona ze wszystkiego
nadawałaby ten sam wpis na nowo przy każdej zwykłej aktualizacji — czyli ten sam defekt,
tylko o poziom rzadszy i trudniejszy do zauważenia, bo do wyzwolenia potrzebuje realnej
zmiany w rejestrze.

Adresu tu nie ma i to jest wynik pomiaru, nie oszczędność: na 315 wierszach bez NIP i REGON
(`probe_out/raport_sample.zip`, 287 256 wierszy) te cztery pola dają **zero kolizji**, a
dołożenie pełnego adresu nie zmienia ani jednej — przy wypełnieniu kodu pocztowego 61 %,
numeru budynku 69 % i numeru lokalu 23 %. Adres uzależniłby tożsamość od tego, jak starannie
ktoś wypełnił formularz."""


def id_z_tresci(
    *,
    nazwa: str | None,
    nazwisko: str | None,
    imie: str | None,
    data_rozpoczecia: str | None,
) -> KanonicznyId:
    """Tożsamość wiersza, któremu rejestr nie nadał numeru — stała między pobraniami.

    Do 2026-09-09 skrót liczył się ze **wszystkich** kolumn wiersza CSV, a więc i z `Lp.` —
    numeru porządkowego w konkretnym pobraniu. Kolejność w rejestrze zmienia się z dnia na
    dzień, więc ten sam przedsiębiorca dostawał w każdym archiwum **nową tożsamość**: baza
    zbierała duplikaty, których żaden `ON CONFLICT` nie scalał, a porównanie dwóch pobrań
    meldowało zmianę tam, gdzie nic się nie zmieniło. To jest defekt ADR-0013 przeniesiony
    na drugie źródło (audyt 2026-09-08, A9; 315 wierszy na pobranie).

    Funkcja bierze **wartości**, nie wiersz CSV, i sama je normalizuje. Powód jest
    praktyczny: ewentualna przyszła migracja liczy tożsamość z `firma.list_json`, gdzie
    `_drop_none` usunął już pola puste — gdyby normalizacja siedziała po stronie wołającego,
    wejście i migracja miałyby dwa różne pojęcia „pustego" i policzyłyby dwa różne skróty."""
    czesci = [(wartosc or "").strip() for wartosc in (nazwa, nazwisko, imie, data_rozpoczecia)]
    # Granica pól nie może zależeć od ich treści. `"|".join(...)` wyglądał niewinnie, ale `|`
    # jest legalnym znakiem w nazwie z rejestru, a nazwy z rejestru są **wrogim wejściem**
    # (CLAUDE.md): `("A|B", "C", …)` i `("A", "B|C", …)` dawały ten sam skrót. ADR-0016 mierzył
    # zero kolizji na **krotce** czterech pól, a kod liczył skrót ze sklejonego napisu — to nie
    # jest to samo twierdzenie, i akurat ta różnica jest sterowalna przez wpisującego.
    surowe = json.dumps(czesci, ensure_ascii=False, separators=(",", ":"))
    skrot = hashlib.sha256(surowe.encode("utf-8")).hexdigest()[:24]
    return KanonicznyId(f"{PREFIKS_TRESCI}{skrot}")
