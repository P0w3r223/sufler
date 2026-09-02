"""Dostawa plików ze skrzynki nadawczej rozmowy.

Decyzja: ADR 0009 paczki wdrożeniowej `infra-docker-workmate`
(`docs/decyzje/0009-konsolidacja-powierzchni-narzedziowej.md`).

Po zakończeniu tury drzwi zaglądają do ``outputs/`` w katalogu roboczym rozmowy i wysyłają to,
co model tam zostawił. Serwis zależy wyłącznie od portów (``OutboxRepository`` + wstrzyknięty
``send``), więc reguła ``core ↛ adapters`` zostaje, a to, CZYM plik jedzie do rozmówcy, nie jest
tu wiedzą.

Reguła sprzątania rozróżnia dwa rodzaje niepowodzenia, bo mają przeciwne właściwe zachowania:

- **odrzucenie trwałe** — rozszerzenie spoza białej listy, plik ponad limit, ponad limit liczby
  (ale tylko dla plików ŚWIEŻYCH: nadmiar pozycji już ponawianych jest odkładany, nie kasowany —
  limit liczby jest granicą jednej tury, nie werdyktem o treści), a także wysyłka odrzucona
  trwale (``PermanentDeliveryError``: 4xx z Graph, zniknięty root wątku).
  Plik ZNIKA ze skrzynki wraz z podaniem powodu. Zostawienie go zrobiłoby zatrutą
  wiadomość: ten sam komunikat doklejałby się do każdej kolejnej odpowiedzi w tej rozmowie, aż
  ktoś ręcznie wejdzie na wolumen. Nic się przy tym nie traci — skrzynka jest katalogiem
  PRZESYŁKOWYM, a materiał źródłowy leży w katalogu roboczym piętro wyżej;
- **awaria przejściowa** — sieć, 5xx, limit żądań. Plik ZOSTAJE, więc następna tura ponowi.
  Tu ubytek byłby realny: treść powstała, a odbiorca jej nie zobaczył. **Z dwoma sufitami**:
  po trzeciej nieudanej PRÓBIE oraz po dziesiątej TURZE przetrzymywania pozycja przechodzi
  w odrzucenie trwałe. Ta reguła świadomie łamie zdanie powyżej, bo bez sufitu plik trwale
  niewysyłalny kosztowałby dwa żądania Graph w KAŻDEJ turze tej rozmowy, bez końca, doklejając
  „spróbuję ponownie" do każdej odpowiedzi. Uzasadnienie „materiał źródłowy leży piętro wyżej"
  jest przy tym założeniem o zachowaniu modelu, nie własnością systemu — nikt nie wymusza,
  że kopia została w katalogu roboczym.

  Sufity są DWA, bo pozycja może zostać zatrzymana, nie zużywając próby: wypada za okno tury
  (limit liczby plików) albo za budżet czasu, a wtedy nikt jej nie wysyłał i naliczenie próby
  byłoby kłamstwem. Sam sufit prób zostawiał więc taką pozycję na wolumenie w nieskończoność —
  patrz ``_MAX_CARRIED_TURNS``.

Odczyt z dysku dotyka WYŁĄCZNIE pozycji, które przeszły kontrolę metadanych — zawartość skrzynki
dyktuje model z powłoką, a proces drzwi obsługuje wszystkie kanały naraz.

**Dostawa ma budżet czasu, bo biegnie w tej samej ścieżce co odpowiedź tury.** Rozmówca widzi
tekst dopiero, gdy wysyłka się skończy, a pięć plików po timeoucie klienta HTTP oznaczałoby
minuty ciszy po turze, która już się udała. Pozycje ponad budżet zostają w skrzynce i jadą przy
następnej wiadomości — opóźnienie jest ograniczone, a nic się nie gubi. Nie zmienia to
KOLEJNOŚCI: załączniki lądują w wątku przed tekstem, który je zapowiada, bo tekst wysyła poller
dopiero po powrocie z respondera. Odwrócenie tego wymaga zmiany kontraktu ``Responder``
(dziś: wiadomość → tekst) na wszystkich czworgu drzwiach i jest osobną decyzją.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from workmate.core.domain.workspace import safe_filename
from workmate.core.errors import WriteError
from workmate.core.ports.document import FILE_REPLY_FORMATS
from workmate.core.ports.outbox import (
    Deliverable,
    OutboxEntry,
    OutboxReadError,
    OutboxRepository,
    PermanentDeliveryError,
)

# Biała lista rozszerzeń skrzynki = ta sama, co narzędzia ``reply_with_file`` (ADR 0026).
# Jedno źródło: obie drogi kończą się załącznikiem w tym samym wątku, więc rozjazd oznaczałby,
# że format wolno wysłać jedną drogą, a drugą nie — bez powodu, który dałoby się wytłumaczyć.
_ALLOWED_EXT = frozenset(FILE_REPLY_FORMATS)

# Ile odrzuceń wymieniamy z nazwy w komunikacie. Reszta idzie zbiorczo: komunikat dokleja się do
# odpowiedzi, a wiadomość Teams ma sufit rozmiaru — trzysta zdań o odrzuconych plikach mogłoby
# sprawić, że rozmówca nie dostanie NICZEGO, choć pliki zostały już sprzątnięte.
_MAX_NAMED_REJECTIONS = 3

# Po ilu nieudanych próbach pozycja przechodzi w odrzucenie trwałe. Sufit istnieje po to, żeby
# jeden plik niewysyłalny nie blokował czoła kolejki w nieskończoność: pozycje z próbami schodzą
# na koniec okna, ale bez sufitu i tak wracałyby przy każdej turze.
_MAX_SEND_ATTEMPTS = 3

# Po ilu TURACH przetrzymywania pozycja przechodzi w odrzucenie trwałe — niezależnie od tego,
# czy kiedykolwiek doszło do próby wysyłki.
#
# To osobny licznik od ``_MAX_SEND_ATTEMPTS``, bo mierzy inną rzecz i sam sufit prób tej dziury
# nie zamyka: próbę zużywa WYŁĄCZNIE pozycja, która weszła do okna tury. ``_okno_tury`` rezerwuje
# ponowieniom najwyżej ``limit - 1`` miejsc, dopóki jest co świeżego wysłać, więc przy
# ``|ours| >= limit`` co najmniej jedna ponawiana za każdym razem wypada za okno i nie zużywa
# NICZEGO. Przy utrzymującej się awarii wysyłki napływ (jeden plik na turę) przewyższa wtedy
# drenaż i zbiór rośnie bez końca — na wolumenie brudnopisu WSPÓLNYM dla wszystkich rozmów.
# To samo dotyczy pozycji odkładanych budżetem czasu, którym sufit prób świadomie nie nalicza.
#
# Wyżej niż sufit prób, żeby w zwykłej ścieżce (pozycja wchodzi do okna i zawodzi) to dalej ten
# sufit orzekał i dawał swój dokładniejszy powód. Ten działa jako ostatnia zapora dla pozycji,
# która nigdy nie dostała szansy.
_MAX_CARRIED_TURNS = 10

logger = logging.getLogger(__name__)


def _nazwa_na_drucie(item: Deliverable) -> str:
    """Nazwa wysyłkowa ``<slug>-<skrót treści>.<ext>``; ``WriteError`` gdy nazwa jest nie do sluga.

    Sam slug NIE wystarcza, bo dostawa wgrywa plik na dysk KANAŁU — wspólny dla wszystkich wątków
    — a upload jest nadpisujący-po-ścieżce (ADR 0026). ``outputs/raport.md`` z wątku B nadpisywał
    plik wątku A, więc załącznik wiszący pod wiadomością A zaczynał serwować dokument z rozmowy B:
    naraz utrata treści i ujawnienie jej między wątkami.

    Sufiks to 8 znaków ``sha256`` TREŚCI, dokładnie jak w ``tools._safe_doc_name`` (jeden wzorzec
    dla obu dróg załącznika). Skrót treści, a nie losowość ani czas, bo ponowienie po awarii
    przejściowej wysyła TE SAME bajty i ma trafić w tę samą ścieżkę — inaczej każda nieudana próba
    zostawiałaby na dysku kanału kolejną kopię.
    """
    slug = safe_filename(item.name, allowed_ext=_ALLOWED_EXT)
    stem, _, ext = slug.rpartition(".")
    return f"{stem}-{hashlib.sha256(item.content).hexdigest()[:8]}.{ext}"


def _okno_tury(
    entries: Sequence[OutboxEntry], ours: frozenset[str], limit: int
) -> list[OutboxEntry]:
    """Uszereguj pozycje tak, żeby limit liczby plików nie zjadał ani ponowień, ani nowej treści.

    Dwie klasy strat wykluczały się nawzajem i obie były realne:

    - **Sam alfabet** kasował pozycje zatrzymane do ponowienia — wystarczyło, żeby nazwa
      sortowała się za plikami nowej tury. Obietnica „spróbuję ponownie" kończyła się cichym
      usunięciem treści.
    - **Bezwzględny priorytet ponowień** kasuje z kolei świeże pliki, które model właśnie
      wytworzył — przy trwającej awarii wysyłki nowa treść nie dociera do rozmówcy ani razu,
      a ponowienia i tak zginą na suficie prób.

    Stąd rezerwacja: ponowienia biorą najwyżej ``limit - 1`` miejsc, o ile w ogóle jest co
    świeżego wysłać. Gdy świeżych nie ma, okno należy do nich w całości — rezerwowanie miejsca
    dla nikogo byłoby stratą trzeciego rodzaju.

    ``limit == 1`` to przypadek graniczny, w którym rezerwacja ``limit - 1`` daje zero miejsc,
    więc rozstrzyga sama KOLEJNOŚĆ. Pierwszeństwo ma tu ŚWIEŻY plik: pozycja ponawiana czeka
    jedną turę dłużej i wraca (ma jeszcze próby oraz sufit tur przetrzymywania), a świeża
    wypchnięta za okno jest KASOWANA razem z pracą, którą model właśnie wykonał — czyli dokładnie
    strata z drugiego punktu wyżej, tyle że wpuszczona bokiem przez warunek graniczny. Domyślne
    ``5`` nie jest tu bramką: ``WORKMATE_TEAMS_GRAPH_OUTBOX_MAX_FILES`` ustawia operator.

    Liczba prób do WYBORU okna nie wchodzi (steruje wyłącznie porządkiem wysyłki), więc pozycja
    ponawiana nie traci miejsca przez to, że już raz zawiodła.
    """
    ponawiane = sorted((e for e in entries if e.name in ours), key=lambda e: e.name)
    swieze = sorted((e for e in entries if e.name not in ours), key=lambda e: e.name)
    if not swieze:
        return ponawiane
    if limit <= 1:
        return [*swieze, *ponawiane]

    miejsc_na_ponowienia = min(len(ponawiane), limit - 1)
    return [
        *ponawiane[:miejsc_na_ponowienia],
        *swieze,
        *ponawiane[miejsc_na_ponowienia:],
    ]


@dataclass(frozen=True)
class _Niepowodzenie:
    """Dlaczego pozycja nie pojechała: czy trwale, z jakim powodem i NA KTÓRYM kroku.

    ``krok`` (bezokolicznik, „odczytać" albo „wysłać") istnieje wyłącznie dla komunikatu
    sufitu prób. Pozycja niewysłana i pozycja nieprzeczytana schodzą tą samą gałęzią —
    reguła sprzątania jest dla nich wspólna i słusznie — ale zdanie „nie udało się wysłać
    po 3 próbach" o pliku, którego ani razu nie dało się otworzyć, kieruje szukającego
    w stronę kanału zamiast wolumenu. To jedyna informacja, jaką dostaje człowiek.
    """

    trwale: bool
    powod: str
    krok: str


@dataclass(frozen=True)
class OutboxLimits:
    """Granice jednej tury: rozmiar pliku, liczba plików i budżet czasu na całą dostawę."""

    max_file_bytes: int
    max_files_per_turn: int
    max_total_seconds: float = 20.0


@dataclass(frozen=True)
class DeliveryReport:
    """Co poszło, czego nie i dlaczego. ``rejected``/``failed`` niosą pary (nazwa, powód).

    ``delivered`` niesie nazwy NA DRUCIE (ze skrótem treści) — te, które rozmówca widzi pod
    wiadomością. Pozostałe trzy pola niosą nazwy ŹRÓDŁOWE: pliki zostały w skrzynce albo z niej
    zniknęły, więc nazwa wysyłkowa nigdy nie powstała i nie ma jej gdzie zobaczyć.
    """

    delivered: tuple[str, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()
    deferred: tuple[str, ...] = ()

    def is_empty(self) -> bool:
        return not (self.delivered or self.rejected or self.failed or self.deferred)

    def notice(self) -> str:
        """Zdanie doklejane do odpowiedzi; pusty napis, gdy nie było czego dostarczać.

        Odbiorcą jest CZŁOWIEK, nie model — tura już się skończyła, więc komunikat ma pozwolić
        zorientować się, co przyszło i czego zabrakło, a nie sterować kolejnym krokiem agenta.
        """
        parts: list[str] = []
        if self.delivered:
            parts.append("W załączniku: " + ", ".join(self.delivered) + ".")
        for name, reason in self.rejected[:_MAX_NAMED_REJECTIONS]:
            parts.append(f"Nie wysłałem pliku {name} — {reason}.")
        if len(self.rejected) > _MAX_NAMED_REJECTIONS:
            parts.append(f"Pominąłem też {len(self.rejected) - _MAX_NAMED_REJECTIONS} innych.")
        for name, reason in self.failed:
            parts.append(f"Nie udało się wysłać pliku {name} ({reason}) — spróbuję ponownie.")
        if self.deferred:
            parts.append(
                f"Zostało {len(self.deferred)} plików do wysłania — dostarczę je przy "
                "następnej wiadomości."
            )
        return " ".join(parts)


class OutboxDelivery:
    """Zabierz pliki ze skrzynki rozmowy i wyślij je wstrzykniętym ``send``."""

    def __init__(
        self,
        repo: OutboxRepository,
        limits: OutboxLimits,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._repo = repo
        self._limits = limits
        # Nazwy widziane w skrzynce NA POCZĄTKU tury, per katalog rozmowy. Patrz ``snapshot``.
        self._at_start: dict[str, frozenset[str]] = {}
        # Nazwy, które MY zostawiliśmy po nieudanej wysyłce — jedyne wolno dostarczyć mimo
        # obecności w migawce, bo ich pochodzenie znamy.
        self._ours: dict[str, frozenset[str]] = {}
        # Nieudane próby wysyłki per (katalog rozmowy, nazwa). Sterują PORZĄDKIEM w oknie tury
        # (pozycje z próbami schodzą na koniec, więc jeden plik niewysyłalny nie blokuje reszty)
        # i sufitem, po którym pozycja przechodzi w odrzucenie trwałe. Pamięć procesu wystarcza:
        # stan ponawiania (``_ours``) i tak nie przeżywa restartu, więc czasy życia się pokrywają.
        self._attempts: dict[str, dict[str, int]] = {}
        # Ile TUR trzymamy pozycję, licząc także te, w których nie doszło do próby wysyłki
        # (wypadła za okno przez limit liczby albo za budżet czasu). Patrz ``_MAX_CARRIED_TURNS``:
        # bez tego pozycja głodzona przez rezerwację okna nie zużywała niczego i zostawała na
        # wolumenie w nieskończoność. Ten sam czas życia co ``_ours`` — pamięć procesu wystarcza.
        self._carried: dict[str, dict[str, int]] = {}
        # Zegar wstrzykiwany jak w responderze — testy mierzą budżet bez czekania realnego czasu.
        # MONOTONICZNY, bo mierzymy upływ, a nie porę: przestawienie zegara systemowego w środku
        # dostawy nie ma prawa jej urwać ani przedłużyć.
        self._monotonic = monotonic

    def snapshot(self, dirpath: str) -> None:
        """Zapamiętaj zawartość skrzynki PRZED turą — granica pochodzenia plików.

        Rozmowy dzielą JEDEN wolumen brudnopisu, a `cwd` jest konwencją, nie zamknięciem
        (pełny opis: ADR 0010 paczki). Powłoka rozmowy A może więc policzyć katalog rozmowy B,
        założyć w nim ``outputs/`` zwykłym ``mkdir`` i podłożyć plik — bez żadnego dowiązania,
        więc żaden guard na dowiązania tego nie dotyka. Bez migawki kolektor opublikowałby taki
        plik w CUDZYM wątku, firmując treść botem.

        Migawka zamyka to, bo **tury są szeregowane**: poller robi ``await self._handle`` w pętli
        sekwencyjnej, więc powłoka rozmowy A nie biegnie w trakcie tury rozmowy B. Plik podłożony
        wcześniej jest w migawce i nie zostanie wysłany; plik powstały w trakcie tury — zostanie.
        Argument stoi też na tym, że wykonawca zabija grupę procesów po KAŻDYM poleceniu:
        bez tego proces w tle obchodziłby migawkę, pisząc już po niej.
        """
        self._at_start[dirpath] = frozenset(e.name for e in self._repo.list_entries(dirpath))

    def deliver(self, dirpath: str, send: Callable[[Deliverable], None]) -> DeliveryReport:
        """Wyślij zawartość skrzynki ``dirpath``; zwróć raport, nigdy nie podnoś wyjątku wysyłki.

        Wyjątek z ``send`` jest ŁAPANY i zamieniany w pozycję raportu: dostawa jest dodatkiem
        do tury, która już się udała, więc jej awaria nie może zabrać rozmówcy odpowiedzi
        tekstowej. Tak samo ``OutboxReadError`` — pozycji nie da się przeczytać, ale jest, więc
        ma trafić do raportu (i, przy powodzie przejściowym, zostać do ponowienia), a nie zniknąć
        po cichu. Wyjątek z WYPISU skrzynki propaguje: to defekt montażu, nie treści.
        """
        if dirpath not in self._at_start:
            # FAIL-CLOSED. Bez migawki nie umiemy odróżnić pliku od modelu tej rozmowy od
            # podłożonego przez inną, więc nie wysyłamy nic. Wołający ma zawołać ``snapshot``
            # na starcie tury; brak wywołania jest defektem okablowania, nie stanem normalnym.
            logger.error("Dostawa dla %s bez migawki startowej — pomijam całą skrzynkę.", dirpath)
            return DeliveryReport()
        at_start = self._at_start.pop(dirpath)
        ours = self._ours.get(dirpath, frozenset())

        entries = sorted(self._repo.list_entries(dirpath), key=lambda e: e.name)
        foreign = [e for e in entries if e.name in at_start and e.name not in ours]
        if foreign:
            # Poza komunikatem dla rozmówcy: nie spowodował tego i nie ma jak zareagować.
            # Sprzątamy — pozostawienie dawałoby to samo ostrzeżenie przy KAŻDEJ kolejnej turze.
            logger.warning(
                "Skrzynka %s zawierała %d plików sprzed tury (%s) — nie wysyłam ich. "
                "Albo to pozostałość sprzed restartu procesu, albo podłożenie z innej rozmowy.",
                dirpath,
                len(foreign),
                ", ".join(e.name for e in foreign),
            )
            for entry in foreign:
                self._repo.discard(dirpath, entry.name)
        entries = [e for e in entries if e not in foreign]
        if not entries:
            # Skrzynka pusta = nie ma czego przetrzymywać. Zdejmujemy TU wszystkie trzy stany
            # rozmowy, bo zawężanie ``_attempts``/``_carried`` do zatrzymanych leży za tym
            # powrotem: pozycje znikające POZA dostawą (sprzątanie TTL, ``rm`` z powłoki modelu)
            # zostawiały wiek i próby na zawsze, a przeterminowany ``carried=9`` zabijał świeży
            # plik przy pierwszym odłożeniu — komunikatem o dziesięciu turach dla pliku, który
            # istnieje jedną.
            self._ours.pop(dirpath, None)
            self._attempts.pop(dirpath, None)
            self._carried.pop(dirpath, None)
            return DeliveryReport()

        delivered: list[str] = []
        rejected: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []
        deferred: list[str] = []

        kolejnosc = _okno_tury(entries, ours, self._limits.max_files_per_turn)
        for entry in kolejnosc[self._limits.max_files_per_turn :]:
            if entry.name in ours:
                # Pozycja ZOSTAWIONA przez nas po awarii przejściowej (albo odłożona budżetem).
                # Limit liczby plików jest granicą JEDNEJ tury, nie werdyktem o treści, więc
                # nadmiar ponawianych ODKŁADAMY — kasowanie łamałoby regułę z docstringu modułu
                # („przy awarii przejściowej pliku nie wolno usuwać") i zamieniało obietnicę
                # „spróbuję ponownie" w cichą utratę pracy modelu.
                deferred.append(entry.name)
                continue
            rejected.append(
                (entry.name, f"na turę wysyłam najwyżej {self._limits.max_files_per_turn} plików")
            )
            self._repo.discard(dirpath, entry.name)

        attempts = self._attempts.setdefault(dirpath, {})
        # PORZĄDEK w oknie: pozycje z nieudanymi próbami schodzą na koniec, żeby jeden plik
        # niewysyłalny nie zjadał budżetu czasu przed resztą.
        window = sorted(
            kolejnosc[: self._limits.max_files_per_turn],
            key=lambda e: (attempts.get(e.name, 0), e.name),
        )

        deadline = self._monotonic() + self._limits.max_total_seconds
        for entry in window:
            reason = self._rejection(entry)
            if reason is not None:
                rejected.append((entry.name, reason))
                self._repo.discard(dirpath, entry.name)
                continue
            # Budżet sprawdzamy PRZED wysyłką, nie po: dostawa biegnie w tej samej ścieżce co
            # odpowiedź tury, więc pięć plików po timeoucie klienta HTTP wstrzymywałoby rozmówcę
            # minutami. Pozycje ponad budżet zostają w skrzynce i jadą przy następnej wiadomości —
            # opóźnienie jest ograniczone, a nic się nie gubi.
            if self._monotonic() >= deadline:
                deferred.append(entry.name)
                continue
            outcome = self._fetch_and_send(dirpath, entry, send)
            if outcome is None:
                # Plik zniknął między wypisem a odczytem — nie ma czego wysyłać ani sprzątać.
                continue
            if isinstance(outcome, str):
                delivered.append(outcome)
                self._repo.discard(dirpath, entry.name)
            elif outcome.trwale:
                rejected.append((entry.name, outcome.powod))
                self._repo.discard(dirpath, entry.name)
            else:
                tries = attempts.get(entry.name, 0) + 1
                if tries >= _MAX_SEND_ATTEMPTS:
                    # Sufit prób ŁAMIE regułę „awaria przejściowa zostawia plik" — świadomie,
                    # patrz docstring modułu. Powód idzie do ``rejected``, nie ``failed``, żeby
                    # komunikat przestał obiecywać ponowienie, którego już nie będzie.
                    #
                    # Krok bierzemy z niepowodzenia, bo to jedyne zdanie, jakie o tej pozycji
                    # usłyszy człowiek: „nie udało się wysłać" dla pliku, którego ani razu nie
                    # dało się PRZECZYTAĆ, wskazywałoby na kanał zamiast na wolumen.
                    rejected.append(
                        (entry.name, f"nie udało się {outcome.krok} po {tries} próbach")
                    )
                    self._repo.discard(dirpath, entry.name)
                else:
                    attempts[entry.name] = tries
                    failed.append((entry.name, outcome.powod))

        # WIEK pozycji: ile tur ją przetrzymujemy, bez względu na to, czy doszło do próby wysyłki.
        # Naliczany PO pętli, dla wszystkiego, co miałoby zostać — także dla pozycji, która przez
        # cały swój żywot ani razu nie weszła do okna. To jedyna zapora dla tej pozycji.
        carried = self._carried.setdefault(dirpath, {})
        wiek = {name: carried.get(name, 0) + 1 for name in [n for n, _ in failed] + deferred}
        przeterminowane = {name for name, tur in wiek.items() if tur >= _MAX_CARRIED_TURNS}
        for name in sorted(przeterminowane):
            rejected.append((name, f"nie udało się dostarczyć przez {wiek[name]} tur"))
            self._repo.discard(dirpath, name)
        # Pozycję przeterminowaną zdejmujemy też z ``failed``/``deferred`` — inaczej komunikat
        # obiecywałby ponowienie pliku, którego przed chwilą nie stało.
        failed = [(name, powod) for name, powod in failed if name not in przeterminowane]
        deferred = [name for name in deferred if name not in przeterminowane]

        # Zapamiętaj, co ZOSTAWILIŚMY — tylko te nazwy wolno wysłać w kolejnej turze mimo
        # obecności w migawce. Pusty zbiór usuwamy, żeby słownik nie rósł z liczbą rozmów.
        retained = frozenset([name for name, _ in failed] + deferred)
        if retained:
            self._ours[dirpath] = retained
        else:
            self._ours.pop(dirpath, None)
        # Oba liczniki są kluczowane NAZWĄ OD MODELU, a poller chodzi dobami — zawężamy je do
        # pozycji faktycznie zatrzymanych. Bez tego świeży `raport.md` dziedziczyłby próby i wiek
        # po swoim poprzedniku i ginął przy pierwszym spojrzeniu.
        ocalale_wieki = {name: tur for name, tur in wiek.items() if name in retained}
        if ocalale_wieki:
            self._carried[dirpath] = ocalale_wieki
        else:
            self._carried.pop(dirpath, None)
        surviving = {name: tries for name, tries in attempts.items() if name in retained}
        if surviving:
            self._attempts[dirpath] = surviving
        else:
            self._attempts.pop(dirpath, None)
        return DeliveryReport(tuple(delivered), tuple(rejected), tuple(failed), tuple(deferred))

    def _fetch_and_send(
        self, dirpath: str, entry: OutboxEntry, send: Callable[[Deliverable], None]
    ) -> str | _Niepowodzenie | None:
        """Wczytaj pozycję i wyślij ją.

        Zwraca nazwę NA DRUCIE przy powodzeniu, ``_Niepowodzenie`` przy porażce i ``None``
        wyłącznie wtedy, gdy pliku już nie ma. Odczyt i wysyłka stoją razem, bo obie kończą
        się tym samym: albo pozycja pojechała, albo mamy powód do raportu.
        """
        try:
            item = self._repo.read(dirpath, entry.name)
        except OutboxReadError as exc:
            return _Niepowodzenie(exc.permanent, str(exc), krok="odczytać")
        if item is None:
            return None
        return self._send_one(item, send)

    def _send_one(
        self, item: Deliverable, send: Callable[[Deliverable], None]
    ) -> str | _Niepowodzenie:
        """Wyślij pozycję; nazwa NA DRUCIE przy sukcesie, inaczej ``_Niepowodzenie``.

        Zwracamy nazwę wysyłkową, nie źródłową: to ją rozmówca zobaczy pod wiadomością, więc
        „W załączniku: raport.md" przy załączniku ``raport-1a2b3c4d.md`` kazałoby mu szukać
        pliku, którego tam nie ma.
        """
        try:
            na_drucie = _nazwa_na_drucie(item)
            send(replace(item, name=na_drucie))
        except PermanentDeliveryError as exc:
            return _Niepowodzenie(True, str(exc) or "odbiorca odrzucił plik", krok="wysłać")
        except Exception as exc:  # noqa: BLE001 — patrz docstring: raport zamiast wyjątku
            return _Niepowodzenie(False, type(exc).__name__, krok="wysłać")
        return na_drucie

    def _rejection(self, entry: OutboxEntry) -> str | None:
        """Powód odrzucenia trwałego albo ``None``, gdy pozycja nadaje się do wysłania.

        Rozstrzygamy na METADANYCH, przed dotknięciem treści — plik ponad limit ma nie trafić
        do pamięci procesu tylko po to, żeby zaraz zostać odrzuconym.
        """
        if entry.size == 0:
            return "jest pusty"
        if entry.size > self._limits.max_file_bytes:
            limit_kb = self._limits.max_file_bytes // 1024
            return f"przekracza limit {limit_kb} KB"
        try:
            safe_filename(entry.name, allowed_ext=_ALLOWED_EXT)
        except WriteError:
            allowed = ", ".join(sorted(_ALLOWED_EXT))
            return f"ma nazwę spoza dozwolonych (rozszerzenia: {allowed})"
        return None
