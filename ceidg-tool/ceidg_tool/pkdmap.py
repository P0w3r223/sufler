"""Tablica przejścia PKD 2007 → 2025 — moduł czysty (ADR-0012).

Po co istnieje: rejestr trzyma **przy rekordzie** jeden rocznik klasyfikacji (`rokPkd`), a filtr
`pkd` dopasowuje kod tak, jak go zapisano. Kod z PKD 2025 nie sięga więc rekordów jeszcze
nieprzeniesionych, a okres przejściowy trwa do 31.12.2026. Zmierzone 2026-09-07
(`docs/decisions.md`): 25,2 % rejestru jest nieosiągalne żadnym kodem z PKD 2025, a zapytanie
o fryzjerów kodem `9621Z` sięga 17 % fryzjerów — bez błędu gdziekolwiek po drodze.

Czego ten moduł **nie** robi: nie decyduje. Rozszerzenie bywa niejednoznaczne — `9602Z`
„Fryzjerstwo i pozostałe zabiegi kosmetyczne" prowadzi zarówno do `9621Z`, jak i `9622Z`, więc
dołożenie go do zapytania o fryzjerów wciąga też kosmetyczki. Rekord z kodem `9602Z` nie niesie
informacji, po której stronie podziału stoi, więc żaden mechanizm tego nie rozstrzygnie. Moduł
tylko nazywa sytuację; wybór należy do operatora i zapada w `ui/flow.py`.

Podział na rozszerzenia czyste i niejednoznaczne jest **liczony z danych** przy wczytaniu, a nie
zapisany w pliku. Dzięki temu „55 czystych, 209 niejednoznacznych" jest własnością tablicy,
sprawdzalną przez przebudowę, a nie liczbą, którą ktoś kiedyś wpisał i która mogła się rozjechać.

Wczytywanie naśladuje `assistant/pkd.load_pkd`: czysta funkcja z podmienialną ścieżką, żeby testy
mogły podstawić tablicę trzyelementową zamiast pełnej.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import yaml

from .criteria import normalize_pkd
from .errors import ConfigError

PKD_MAP_RESOURCE = resources.files("ceidg_tool").joinpath("data", "pkd2007_2025.yaml")
DEFAULT_PKD_MAP_PATH = Path(str(PKD_MAP_RESOURCE))

# Data, po której tablica przestaje być potrzebna — koniec okresu przejściowego z rozporządzenia
# (Dz.U. 2024 poz. 1936). Trzymana tu, a nie tylko w nagłówku pliku, żeby dało się o nią oprzeć
# test i żeby usunięcie całego mechanizmu miało jedno oczywiste miejsce startu.
KONIEC_PRZEJSCIA = "2026-12-31"


@dataclass(frozen=True)
class Poprzednik:
    """Kod PKD 2007, który trzeba dołożyć, żeby sięgnąć rekordów nieprzeniesionych."""

    kod: str
    nazwa: str
    # Inne kody PKD 2025, do których ten sam kod 2007 również prowadzi — czyli branże, które
    # wpadną do wyniku razem z naszą.
    rowniez: tuple[tuple[str, str], ...] = ()
    # Dzisiejsze znaczenie tego samego łańcucha, gdy kod nadal istnieje w PKD 2025 — wtedy
    # dokładając go, bierzemy też firmy, które mają go **jako kod z 2025**. To druga postać
    # niejednoznaczności i celowo osobne pole: na ekranie czyta się inaczej niż „prowadzi też
    # do innego kodu", a zlanie obu w jedną listę pokazywało ten sam kod dwa razy.
    dzis: str = ""

    @property
    def czysty(self) -> bool:
        """Czy dołożenie tego kodu nie wciąga cudzej branży — w żadnej z dwóch postaci."""
        return not self.rowniez and not self.dzis


@dataclass(frozen=True)
class Rozszerzenie:
    """Wynik rozszerzenia zestawu kodów 2025 o poprzedników z 2007."""

    kody_2007: tuple[str, ...]
    czyste: tuple[Poprzednik, ...]
    niejednoznaczne: tuple[Poprzednik, ...]

    def is_empty(self) -> bool:
        return not self.kody_2007

    @property
    def wymaga_pytania(self) -> bool:
        """Czy operator musi wybrać — czyli czy cokolwiek wciąga cudzą branżę."""
        return bool(self.niejednoznaczne)


class TablicaPkd:
    """Wczytana tablica przejścia. Niezmienna, bez wejścia-wyjścia po konstrukcji."""

    def __init__(
        self,
        poprzednicy: Mapping[str, tuple[str, ...]],
        nazwy_2007: Mapping[str, str],
        nazwy_2025: Mapping[str, str],
        zywe_2025: Iterable[str] = (),
    ) -> None:
        self._poprzednicy = dict(poprzednicy)
        self._nazwy_2007 = dict(nazwy_2007)
        self._nazwy_2025 = dict(nazwy_2025)
        # Kody 2007, które w PKD 2025 nadal istnieją, ale znaczą co innego — dokładając taki
        # kod, bierzemy też jego dzisiejszą branżę. To druga postać niejednoznaczności;
        # pierwsza to kod prowadzący do kilku kodów 2025 naraz.
        self._zywe_2025 = frozenset(zywe_2025)
        # Rozgałęzienie: do ilu kodów 2025 prowadzi dany kod 2007. To jedyne źródło podziału
        # na czyste i niejednoznaczne — liczone, nie deklarowane.
        rozgalezienie: dict[str, list[str]] = {}
        for nowy, stare in sorted(self._poprzednicy.items()):
            for stary in stare:
                rozgalezienie.setdefault(stary, []).append(nowy)
        self._rozgalezienie = {k: tuple(v) for k, v in rozgalezienie.items()}

    def __len__(self) -> int:
        return len(self._poprzednicy)

    def nazwa_2007(self, kod: str) -> str | None:
        return self._nazwy_2007.get(kod)

    def poprzednicy(self, kod_2025: str) -> tuple[str, ...]:
        """Kody 2007, z których wywodzi się dany kod 2025 — puste, gdy kod jest nowy."""
        return self._poprzednicy.get(kod_2025, ())

    def nazwa_2025(self, kod: str) -> str | None:
        return self._nazwy_2025.get(kod)

    def rozszerz(self, kody_2025: Iterable[str]) -> Rozszerzenie:
        """Poprzednicy z 2007 dla podanych kodów 2025, z podziałem na czyste i niejednoznaczne.

        Kody spoza tablicy przechodzą bez śladu — to nie błąd, tylko najczęstszy przypadek:
        464 z 728 podklas nie zmieniły się na tyle, żeby cokolwiek dokładać.
        """
        wybrane = tuple(dict.fromkeys(kody_2025))
        widziane: dict[str, Poprzednik] = {}
        for kod in wybrane:
            for stary in self._poprzednicy.get(kod, ()):
                if stary in widziane or stary in wybrane:
                    # `stary in wybrane`: kod, o który operator poprosił wprost, nie jest
                    # „dołożeniem" — `pkd=` i tak go niesie. Bez tego warunku zapytanie
                    # o `{9313Z, 8551Z}` dawało dwa warianty o **identycznym** URL-u, drugi
                    # `count` na populację nieodróżnialną od pierwszej i pytanie o wybór,
                    # który niczego nie zmienia. To ten sam argument co `stary == nowy`
                    # w generatorze, tylko na poziomie zapytania (audyt 2026-09-07).
                    continue
                # „Również" liczymy względem **wybranego zestawu**: jeśli operator poprosił
                # i o fryzjerstwo, i o kosmetykę, to `9602Z` nie wciąga niczego ponadto,
                # więc rozszerzenie jest dla niego czyste. Ta sama tablica, inna sytuacja.
                obce = [c for c in self._rozgalezienie.get(stary, ()) if c not in wybrane]
                # Kod dokładany bywa **sam** żywym kodem PKD 2025 o innym znaczeniu, więc wciąga
                # własną dzisiejszą branżę. Bez tego `9313Z` (kluby fitness) dostawałoby `8551Z`
                # jako rozszerzenie „czyste", a razem z nim dzisiejszą „Pozostałą edukację
                # sportową" — po cichu.
                dzis = ""
                if stary in self._zywe_2025 and stary not in wybrane:
                    dzis = self._nazwy_2025.get(stary, "")
                widziane[stary] = Poprzednik(
                    kod=stary,
                    nazwa=self._nazwy_2007.get(stary, ""),
                    rowniez=tuple((c, self._nazwy_2025.get(c, "")) for c in dict.fromkeys(obce)),
                    dzis=dzis,
                )
        wszystkie = tuple(sorted(widziane))
        return Rozszerzenie(
            kody_2007=wszystkie,
            czyste=tuple(widziane[k] for k in wszystkie if widziane[k].czysty),
            niejednoznaczne=tuple(widziane[k] for k in wszystkie if not widziane[k].czysty),
        )


def _mapa(raw: object, klucz: str, source: Path) -> dict[str, object]:
    wartosc = raw.get(klucz) if isinstance(raw, dict) else None
    if not isinstance(wartosc, dict):
        raise ConfigError(
            f"Tablica przejścia {source}: brakuje sekcji {klucz!r} albo nie jest mapą."
        )
    return wartosc


def _kanoniczny(kod: object, source: Path, gdzie: str) -> str:
    """Kod w postaci kanonicznej albo `ConfigError` — nigdy `ValueError` na zewnątrz.

    Ten sam powód co w `assistant/pkd.load_pkd`: `normalize_pkd` rzuca `ValueError`, który nie
    jest `CeidgError`, więc `cli.py` go nie łapie i operator dostaje ślad stosu zamiast zdania.
    Uszkodzenie tego pliku najpewniej wygląda właśnie tak — sekcja `J` albo klasa `62.01`.
    """
    tekst = str(kod).strip()
    try:
        canonical = normalize_pkd(tekst)
    except ValueError as exc:
        raise ConfigError(
            f"Tablica przejścia {source} ({gdzie}): {tekst!r} nie jest kodem podklasy PKD ({exc})."
        ) from exc
    if canonical != tekst:
        raise ConfigError(
            f"Tablica przejścia {source} ({gdzie}): {tekst!r} nie jest w postaci kanonicznej "
            f"(oczekiwano {canonical!r})."
        )
    return canonical


def load_pkd_map(path: Path | None = None) -> TablicaPkd:
    """Wczytuje tablicę przejścia. Uszkodzenie pada od razu i głośno, nie trzy ekrany później."""
    source = path or DEFAULT_PKD_MAP_PATH
    if not source.is_file():
        raise ConfigError(
            f"Brak tablicy przejścia PKD ({source}). Zbuduj ją z oficjalnego klucza GUS: "
            "PYTHONUTF8=1 python scripts/build_pkd_transition.py PKD/KluczePKD_2007_2025.xlsx "
            "(źródło: https://klasyfikacje.stat.gov.pl/Pkd2025)."
        )
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"Nie można wczytać tablicy przejścia {source}: {exc}") from exc

    surowi = _mapa(raw, "poprzednicy", source)
    nazwy_2007 = {
        _kanoniczny(k, source, "nazwy_2007"): str(v).strip()
        for k, v in _mapa(raw, "nazwy_2007", source).items()
    }
    nazwy_2025 = {
        _kanoniczny(k, source, "nazwy_2025"): str(v).strip()
        for k, v in _mapa(raw, "nazwy_2025", source).items()
    }

    poprzednicy: dict[str, tuple[str, ...]] = {}
    for klucz, wartosc in surowi.items():
        nowy = _kanoniczny(klucz, source, "poprzednicy")
        if not isinstance(wartosc, list) or not wartosc:
            raise ConfigError(
                f"Tablica przejścia {source}: kod {nowy} ma pustą albo nielistową "
                "listę poprzedników."
            )
        stare = tuple(_kanoniczny(k, source, f"poprzednicy[{nowy}]") for k in wartosc)
        for stary in stare:
            # Kod bez nazwy nie może trafić na ekran potwierdzenia, a ekran jest jedyną kontrolą
            # operatora nad tym, czy dostał właściwą branżę. Lepiej paść tu niż pokazać goły kod.
            if not nazwy_2007.get(stary):
                raise ConfigError(
                    f"Tablica przejścia {source}: kod PKD 2007 {stary} nie ma nazwy, "
                    "a ekran potwierdzenia pokazuje nazwy. Przebuduj tablicę ze źródła GUS."
                )
        poprzednicy[nowy] = stare

    if not poprzednicy:
        raise ConfigError(f"Tablica przejścia {source} nie ma ani jednego poprzednika.")

    # Nazwy 2025 sprawdzamy tak samo jak 2007, bo trafiają na ten sam ekran: kod z „obejmuje
    # też" bez nazwy czyta się jako „9622Z " i znosi jedyną kontrolę, jaką ma operator nad tym,
    # czy dostał właściwą branżę. Do przeglądu 2026-09-07 strażnik był tu jednostronny.
    zywe_raw = raw.get("zywe_2025") if isinstance(raw, dict) else None
    zywe = tuple(
        _kanoniczny(k, source, "zywe_2025")
        for k in (zywe_raw if isinstance(zywe_raw, list) else [])
    )
    potrzebne_2025 = set(poprzednicy) | set(zywe)
    bez_nazwy = sorted(k for k in potrzebne_2025 if not nazwy_2025.get(k))
    if bez_nazwy:
        raise ConfigError(
            f"Tablica przejścia {source}: kody PKD 2025 bez nazwy ({', '.join(bez_nazwy[:5])}). "
            "Ekran potwierdzenia pokazuje nazwy. Przebuduj tablicę ze źródła GUS."
        )
    return TablicaPkd(poprzednicy, nazwy_2007, nazwy_2025, zywe)
