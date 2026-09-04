"""Ogranicznik czasu na CAŁY przebieg — jedyny, jaki obejmuje więcej niż jedno żądanie.

Budżety istniejące niżej liczą się NA ŻĄDANIE: ``GraphClient`` czeka na ``Retry-After`` do
``_MAX_RETRY_BUDGET_S``, a ``_get_all`` wykonuje takich żądań do ``_MAX_PAGES``. Iloczyn tych dwóch
liczb to kilkanaście godzin w JEDNYM odczycie i nic tego nie przerywa.

Healthcheck tego nie złapie i nie jest to jego wina: czekanie na ``Retry-After`` jest ŻYCIEM usługi,
nie zawisem, więc puls bije zgodnie z projektem (patrz ``cli`` — klient dostaje ``spij_z_pulsem``
jako ``sleep``). Proces naprawdę żyje. Tyle że tydzień przepada w milczeniu.

Moduł nie zna ani Graph, ani pętli usługi: dostaje zegar i limit, oddaje jedno pytanie — „czy
jeszcze wolno". Dzięki temu ``graph`` nie musi wiedzieć nic o przebiegach, a ``service`` nie musi
wiedzieć nic o stronicowaniu.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta


class PrzebiegPrzekroczylCzasError(RuntimeError):
    """Przebieg przekroczył dopuszczalny czas i został przerwany w połowie.

    Błąd TRWAŁY z definicji: skoro przebieg nie zmieścił się w limicie, powtórzenie go od zera tym
    bardziej się nie zmieści. Ponawianie należy do okna łaski, nie do pętli ponowień.
    """


class BudzetPrzebiegu:
    """Zegar odliczający dla jednego przebiegu; poza przebiegiem nie ogranicza niczego.

    Jeden obiekt na proces, przestawiany przy każdym wejściu w przebieg. Stan jest mutowalny
    świadomie: ``sprawdz`` wstrzykujemy do ``GraphClient`` RAZ, przy budowie klienta, a klient żyje
    tyle co proces — więc okno musi dać się przestawiać pod nim, bez odtwarzania klienta.
    """

    def __init__(self, limit_s: int, teraz: Callable[[], datetime]) -> None:
        self._limit_s = limit_s
        self._teraz = teraz
        self._koniec: datetime | None = None
        self._opis = ""

    @property
    def czynny(self) -> bool:
        """Czy limit jest w ogóle włączony (``limit_s <= 0`` wyłącza go całkowicie)."""
        return self._limit_s > 0

    @contextmanager
    def na_czas(self, opis: str) -> Iterator[None]:
        """Otwórz okno czasowe na czas trwania bloku; po wyjściu limit przestaje obowiązywać.

        Okno zamykamy w ``finally``, żeby przerwany przebieg nie zostawił po sobie deadline'u,
        który wywracałby następny — przy trwałej awarii kolejne podejście startowałoby wtedy
        z zegarem już wyczerpanym i padało, zanim cokolwiek zrobi.
        """
        if not self.czynny:
            yield
            return
        self._koniec = self._teraz() + timedelta(seconds=self._limit_s)
        self._opis = opis
        try:
            yield
        finally:
            self._koniec = None
            self._opis = ""

    def sprawdz(self, za_ile_s: float = 0.0) -> None:
        """Przerwij przebieg, jeśli okno się zamknęło — albo zamknie się w planowanym czekaniu.

        ``za_ile_s`` to długość przerwy, którą wywołujący ZAMIERZA dopiero rozpocząć. Bez tego
        parametru limit byłby prawdziwy wyłącznie na papierze: pojedyncze czekanie na
        ``Retry-After`` trwa do ``_MAX_RETRY_BUDGET_S``, więc przebieg mieszczący się w oknie
        o sekundę i tak wychodziłby poza nie o kwadrans. Przerwanie PRZED czekaniem zamienia
        „limit plus jedno dławienie" w „limit".

        Poza oknem (między przebiegami) nie robi nic — sprawdzenie jest wstrzyknięte do klienta
        na stałe, a klient bywa używany także tam, gdzie żadnego przebiegu nie ma.
        """
        if self._koniec is None:
            return
        teraz = self._teraz()
        if teraz >= self._koniec:
            przekroczenie = int((teraz - self._koniec).total_seconds())
            raise PrzebiegPrzekroczylCzasError(
                f"{self._opis}: przekroczono limit {self._limit_s} s (o {przekroczenie} s) — "
                f"przerywam. Najczęstsza przyczyna to długotrwałe dławienie Graph albo kolekcja "
                f"grafiku na tyle duża, że jej odczyt nie mieści się w oknie."
            )
        if za_ile_s <= 0:
            return
        pozostalo = (self._koniec - teraz).total_seconds()
        if za_ile_s > pozostalo:
            raise PrzebiegPrzekroczylCzasError(
                f"{self._opis}: do limitu {self._limit_s} s zostało {int(pozostalo)} s, a Graph "
                f"każe czekać {int(za_ile_s)} s — przerywam zamiast przekraczać limit o różnicę."
            )
