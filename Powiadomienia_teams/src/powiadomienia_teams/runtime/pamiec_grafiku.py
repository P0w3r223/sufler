"""Pamięć grafiku PRZEŻYWAJĄCA przebieg — wyłącznie dla wykrywania samouzupełnienia (ADR 0009).

``runtime.snapshot`` czyta grafik najwyżej raz na PRZEBIEG i to się nie zmienia: sprawdzenie
świeżości tuż przed nieodwracalnym zapisem ma sens tylko wtedy, gdy dane są świeże. Ten moduł
obsługuje drugą ścieżkę, o zupełnie innej stawce — krok 1.5 nasłuchu, czyli zaglądanie do Shifts
po to, żeby MILCZĄCEMU pracownikowi podziękować zamiast dalej go nagabywać.

Różnica stawek jest cała: pomyłka ścieżki zapisu daje DRUGI komplet wpisów w grafiku klienta,
pomyłka tej ścieżki daje podziękowanie o kilka godzin późniejsze.

**Ile to oszczędza.** Odczyt grafiku pobiera całą kolekcję zespołu (u klienta trzy strony, ~2000
zmian, około czterech sekund) i szedł co obieg nasłuchu, całą dobę, dopóki jakakolwiek rozmowa
była otwarta. Między piątkowym przebiegiem a poniedziałkowym terminem to około 61 pełnych pobrań;
przy sześciogodzinnym TTL zostaje ich około dziesięciu.

**Bezpieczeństwo NIE zależy od wartości TTL** i to jest w tym module najważniejsze zdanie. Wpis,
którego termin mija w TYM obiegu, dostaje odczyt świeży (parametr ``odswiez``) — bo tylko dla
niego nieświeży wynik miałby konsekwencję nieodwracalną: pracownik, który uzupełnił grafik sam,
dostałby „nie dostałem odpowiedzi" i temat zamknięty terminalnie. Dla wszystkich pozostałych
najgorsze, co robi nieświeży wynik, to opóźnienie podziękowania o jeden obieg — kierunek, który
`listener` krok 1.5 już dziś akceptuje.

Porażek NIE zapamiętujemy. Zapamiętana porażka na przebieg to jedno rozstrzygnięcie (i tak działa
``SnapshotGrafiku``); zapamiętana na sześć godzin to wyłączony krok 1.5 na sześć godzin.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta

from powiadomienia_teams.domain.models import DaneTygodnia

logger = logging.getLogger(__name__)

#: Jak długo wynik odczytu grafiku wolno uznawać za dość świeży dla kroku 1.5.
#:
#: STAŁA, nie klucz konfiguracji, i to jest decyzja z ADR 0009. Trzy powody: `deploy/env.example`
#: jest jedynym utrzymywanym źródłem prawdy o konfiguracji i każdy klucz kosztuje tam opis oraz
#: wiersz w tabeli operatora; po regule ``odswiez`` wartość nie wpływa na bezpieczeństwo, tylko na
#: opóźnienie podziękowania; a operator nie widzi kosztu odczytu, więc nie ma jak tej liczby
#: sensownie wybrać. Sześć godzin przy odstępie nasłuchu dochodzącym do godziny znaczy „najwyżej
#: raz na sześć obiegów".
_TTL_S = 6 * 3600


class PamiecSamouzupelnien:
    """Wyniki odczytu grafiku per tydzień, ważne przez ``ttl_s`` — dla kroku 1.5 i tylko dla niego.

    Klasa NIE ma metod ``dla_tygodnia``/``dla_tygodni`` i to jest własność konstrukcyjna, nie
    przeoczenie: pod tymi nazwami woła się ``SnapshotGrafiku`` na ścieżce ZAPISU, więc podstawienie
    tej klasy tam nie przechodzi przez `mypy`. Strażnik ``tests/test_zrodlo_swiezosci.py``
    domyka to samo od strony składni.
    """

    def __init__(self, *, ttl_s: int = _TTL_S) -> None:
        self._ttl = timedelta(seconds=ttl_s)
        self._dane: dict[str, tuple[datetime, DaneTygodnia]] = {}

    def przyjmij(self, swieze: Mapping[str, DaneTygodnia], *, teraz: datetime) -> None:
        """Dołóż wyniki, które ktoś już pobrał w tym przebiegu — najczęściej ścieżka zapisu.

        Dane płyną WYŁĄCZNIE w tę stronę: snapshot → pamięć. Drogi w drugą stronę nie ma i nie
        powinno być, bo to ona zamieniłaby ten cache w źródło świeżości dla nieodwracalnego zapisu.
        """
        for week_start, dane in swieze.items():
            self._dane[week_start] = (teraz, dane)

    def dla_samouzupelnienia(
        self,
        week_starts: Iterable[str],
        *,
        teraz: datetime,
        pobierz: Callable[[set[str]], Mapping[str, DaneTygodnia | None]],
        odswiez: Iterable[str] = (),
    ) -> dict[str, DaneTygodnia | None]:
        """Dane tygodni dla kroku 1.5 — z pamięci, gdy świeże; inaczej przez ``pobierz``.

        ``pobierz`` jest wstrzykiwane, a nie budowane tutaj, z dwóch powodów naraz: ten moduł nie
        ma znać ani ``GraphClient``, ani konfiguracji, a wołający i tak trzyma snapshot przebiegu —
        dzięki czemu pudło pamięci NIE oznacza dodatkowego żądania dla tygodnia, który ścieżka
        zapisu pobrała w tym samym obiegu.

        ``odswiez`` wymusza pobranie mimo świeżego wpisu. Tam trafiają tygodnie wpisów, którym
        termin mija w tym obiegu — patrz docstring modułu.
        """
        wymuszone = set(odswiez)
        wszystkie = set(week_starts)
        do_pobrania = {ws for ws in wszystkie if ws in wymuszone or not self._swieze(ws, teraz)}
        wynik: dict[str, DaneTygodnia | None] = {
            ws: self._dane[ws][1] for ws in wszystkie - do_pobrania
        }
        if do_pobrania:
            pobrane = pobierz(do_pobrania)
            self.przyjmij({ws: d for ws, d in pobrane.items() if d is not None}, teraz=teraz)
            wynik.update(pobrane)
        if wynik and not do_pobrania:
            logger.debug("Krok 1.5: grafik %d tygodni z pamięci, zero pobrań", len(wynik))
        return wynik

    def _swieze(self, week_start: str, teraz: datetime) -> bool:
        wpis = self._dane.get(week_start)
        return wpis is not None and (teraz - wpis[0]) < self._ttl
