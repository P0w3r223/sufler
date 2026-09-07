"""Godziny ciszy — kiedy usługa nie pisze do pracowników.

Czysta arytmetyka kalendarza (wstrzykiwany ``teraz``), bez I/O i bez znajomości obiegu. Trzy pytania,
na które odpowiada, i każde ma innego odbiorcę:

* ``wolno_pisac`` — czy TERAZ wolno się odezwać (pętla nasłuchu, przebieg tygodniowy, szew wysyłki);
* ``najblizsza_dozwolona`` — do kiedy spać, żeby obudzić się dokładnie po ciszy (pętla usługi);
* ``cisza_pomiedzy`` — ile ciszy minęło między dwiema chwilami (okno łaski nadrabiania).

Trzecia funkcja jest tu z konkretnego powodu, nie dla kompletności: bez niej cisza **zjadałaby okno
łaski**. Usługa wstająca po awarii w piątek o 21:00, przy oknie łaski 6 h od terminu 16:00, miałaby
nadrobić przebieg do 22:00 — ale przez całe te godziny nie wolno jej pisać, więc nadrobienie
przepadałoby po cichu i CAŁY ZESPÓŁ nie dostawałby w tym tygodniu prośby o grafik. Cisza ma
przesuwać nadrabianie, a nie je unieważniać (`plan-rozwoju.md` B5).

Granica okna wypada zawsze o pełnej godzinie, więc porównujemy godzinę zegara ściennego w strefie
zespołu. Dryf DST (raz na pół roku okno jest o godzinę krótsze albo dłuższe) świadomie ignorujemy:
to reguła uprzejmości, nie termin, od którego cokolwiek zależy nieodwracalnie.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from powiadomienia_teams.config import OknoCiszy


class CiszaWstrzymalaPrzebieg(RuntimeError):
    """Przebieg NIE zaczął się, bo trwają godziny ciszy. Odmowa świadoma, nie awaria.

    Istnieje, bo pusty wynik jest nierozróżnialny od sukcesu. ``run_once`` odmawiające z powodu
    ciszy zwracało ``[]`` — dokładnie to samo, co „nikomu nie brakuje grafiku" — więc pętla
    odhaczała termin jako obsłużony (``last_run_term``) i dedup pomijał go już na zawsze. Przy
    ``RUN_HOUR`` wpadającym w okno ciszy (konfiguracja legalna) CAŁY ZESPÓŁ nie dostawał prośby
    w ŻADNYM tygodniu, a jedynym śladem była linia w logu kontenera. Bramka w pętli tego nie
    zamykała: przebieg zaplanowany biegnie za pętlą wewnętrzną, czyli poniżej bramki, a wtedy
    „nie wolno mi pisać" i „nie było czego robić" nadal wyglądają na granicy modułu tak samo.

    Nie mylić z ``wysylka.CiszaError``. Tamten znaczy „punkt wysyłki został osiągnięty BEZ
    bramki", czyli błąd w kodzie i sygnał, że szew przeciekł. Ten znaczy „bramka zadziałała
    zgodnie z regułą" — wołający ma odłożyć pracę, a nie zgłaszać awarii.

    Baza ``RuntimeError`` mimo zdania „nie awaria" jest wyborem, nie przeoczeniem: wszystkie
    sterujące wyjątki tej warstwy (``PrzebiegPrzekroczylCzasError``, ``AuthExpiredError``,
    ``CiszaError``) dziedziczą tak samo, a jednorodność bazy jest tu warta więcej niż sygnał
    z nazwy nadklasy — o kategorii i tak rozstrzyga jawna klauzula u każdego z czterech wołających.
    W całym repozytorium nie ma ani jednego ``except RuntimeError``, więc nic tego nie połknie;
    gdyby kiedyś powstał, ten wyjątek musi być wymieniony przed nim.
    """

    def __init__(self, dozwolona_od: datetime) -> None:
        super().__init__(
            "Godziny ciszy — przebieg nie został wykonany; najbliższa dozwolona chwila: "
            f"{dozwolona_od.isoformat()}"
        )
        self.dozwolona_od = dozwolona_od


def wolno_pisac(teraz: datetime, okno: OknoCiszy) -> bool:
    """Czy o tej godzinie wolno napisać do pracownika. Okno wyłączone (równe godziny) → zawsze wolno.

    Bez osobnej gałęzi na okno wyłączone: przy ``od_h == do_h`` pierwszy warunek nigdy nie jest
    spełniony, więc odpowiedź brzmi „wolno" sama z siebie. Sonda mutacyjna pokazała, że przy trzech
    gałęziach zamiana ``<`` na ``<=`` była zmianą NIEROZRÓŻNIALNĄ testem — czyli jedna z nich nie
    niosła żadnej decyzji. Dwie gałęzie i jedno porównanie są tu całą regułą.
    """
    godzina = teraz.astimezone(okno.tz).hour
    if okno.od_h <= okno.do_h:  # okno w obrębie jednej doby (np. 1–5) albo wyłączone (od == do)
        return not (okno.od_h <= godzina < okno.do_h)
    return not (godzina >= okno.od_h or godzina < okno.do_h)  # okno przez północ, np. 20–7


def najblizsza_dozwolona(teraz: datetime, okno: OknoCiszy) -> datetime:
    """Pierwsza chwila od ``teraz``, w której wolno pisać (albo ``teraz``, jeśli już wolno).

    Zwraca początek godziny ``do_h`` w strefie zespołu. Wołający używa tego jako sufitu drzemki:
    bez niego pętla budziłaby się po ciszy z opóźnieniem do godziny (backoff odpytywania rośnie do
    ``poll_max_interval_s``), czyli odpowiedź napisana o 07:05 czekałaby do 08:00.
    """
    if wolno_pisac(teraz, okno):
        return teraz
    lokalnie = teraz.astimezone(okno.tz)
    dzien = lokalnie.date()
    if lokalnie.hour >= okno.do_h:
        dzien = dzien + timedelta(days=1)
    return datetime.combine(dzien, time(okno.do_h), tzinfo=okno.tz)


def cisza_pomiedzy(od: datetime, do: datetime, okno: OknoCiszy) -> timedelta:
    """Ile czasu z przedziału ``[od, do)`` przypada na godziny ciszy.

    Liczymy przez wypisanie konkretnych przedziałów ciszy dla każdej doby w zakresie i przecięcie
    ich z ``[od, do)`` — zamiast arytmetyki na godzinach, która przy oknie przez północ i przy
    zakresie dłuższym niż doba zaczyna się mylić.

    Wynik nigdy nie jest ujemny i pilnuje tego ``max(timedelta(0), …)`` przy każdym przecięciu, nie
    warunek na wejściu — ten jest tylko szybkim wyjściem dla zakresu odwróconego albo pustego
    (wołający liczy ``teraz - termin``, a zegar hosta bywa cofany ręcznie).
    """
    if okno.wylaczone or do <= od:
        return timedelta(0)
    razem = timedelta(0)
    # Zaczynamy dobę wcześniej, bo okno przez północ przypisane do dnia D sięga w dzień D+1
    # i przedział mógłby zacząć się już w jego drugiej połowie.
    dzien = od.astimezone(okno.tz).date() - timedelta(days=1)
    koniec_zakresu = do.astimezone(okno.tz).date()
    while dzien <= koniec_zakresu:
        poczatek = datetime.combine(dzien, time(okno.od_h), tzinfo=okno.tz)
        zakonczenie = datetime.combine(
            dzien + (timedelta(days=1) if okno.od_h > okno.do_h else timedelta(0)),
            time(okno.do_h),
            tzinfo=okno.tz,
        )
        razem += max(timedelta(0), min(do, zakonczenie) - max(od, poczatek))
        dzien += timedelta(days=1)
    return razem
