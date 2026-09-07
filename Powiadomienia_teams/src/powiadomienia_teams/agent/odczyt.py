"""Implementacja ``tools.GrafikReader`` czytająca ze SNAPSHOTU przebiegu — **zero własnego I/O**.

Wydzielone z ``app``, żeby ``interpreter`` i ``tools`` pozostały czyste (testy podstawiają atrapę
spełniającą Protocol, bez sieci). Zawężenie do JEDNEJ osoby i JEDNEGO tygodnia jest w tej klasie,
nie w wywołaniu narzędzia — model nie ma jak go rozszerzyć, bo schematy narzędzi nie mają pola
na identyfikator pracownika.

Od 0.2.14 (N35) narzędzia modelu nie mają własnej drogi do Graph: cały odczyt idzie przez
``runtime.snapshot.SnapshotGrafiku``, ten sam, z którego korzysta sprawdzenie świeżości przed
zapisem. Dwa źródła prawdy o tym samym grafiku w obrębie jednego przebiegu znaczyły, że ta sama
odpowiedź pracownika mogła dać różny wynik zależnie od chwili odczytu.

Utrata sesji (``AuthExpiredError``) propaguje NIEZMIENIONA: dotyczy całej usługi, nie tej jednej
odpowiedzi, i ma własną obsługę w ``app``. Każda inna awaria odczytu — łącznie z odczytem uciętym
na limicie stron — staje się ``tools.OdczytNiedostepny``, czyli „nie wiadomo", nigdy „pusto".
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from powiadomienia_teams.agent.kalendarz import PELNE_NAZWY_DNI
from powiadomienia_teams.agent.schema import POWODY_WOLNEGO
from powiadomienia_teams.agent.tools import OdczytNiedostepny
from powiadomienia_teams.domain.models import DaneTygodnia, Shift
from powiadomienia_teams.domain.powody import TeamReasons, display_name, normalize
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.client import GraphTruncatedReadError
from powiadomienia_teams.reminders.detect import off_reason_by_weekday

logger = logging.getLogger(__name__)

_TRYB_Z_KOLORU = {"blue": "zdalnie", "green": "stacjonarnie"}


def opisz_zmiany(shifts: Iterable[Shift], tz: ZoneInfo) -> list[dict[str, Any]]:
    """Zmiany → kształt, który widzi model: nazwa dnia, godziny lokalne, tryb pracy słowem.

    Ten sam kształt dla gotowca i dla stanu zapisanego w Shifts — model porównuje dwie listy
    o identycznych polach, zamiast tłumaczyć w głowie ``weekday`` na dzień i ``theme`` na tryb.

    ``konczy_sie`` jest podawane ZAWSZE, także gdy równe ``dzien``. Kształt jednolity jest tu
    ważniejszy niż kilka zaoszczędzonych tokenów: model nigdy nie musi rozstrzygać, co znaczy brak
    klucza, a zmiana nocna („piątek 22:00 → sobota 06:00") przestaje wyglądać jak literówka, którą
    należałoby poprawić. To korekta OPISU ŚWIATA zamiast dopisywania modelowi reguły — dzień
    zakończenia liczy kod, który zna strefę zespołu, a nie model.
    """
    return [
        {
            "dzien": PELNE_NAZWY_DNI[s.start.astimezone(tz).weekday()],
            "start": f"{s.start.astimezone(tz):%H:%M}",
            "end": f"{s.end.astimezone(tz):%H:%M}",
            "konczy_sie": PELNE_NAZWY_DNI[s.end.astimezone(tz).weekday()],
            "tryb": _TRYB_Z_KOLORU.get(s.theme or "green", "stacjonarnie"),
        }
        for s in sorted(shifts, key=lambda s: s.start)
    ]


def opisz_uzgodnione_wolne(wpisy: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rozstrzygnięty czas wolny → kształt, który widzi model: ``[{dzien, powod}]``.

    Stan trzyma ``{weekday, reason_id, reason_name}`` — identyfikator powodu w Shifts jest dla
    modelu bezużyteczny, a numer dnia jest gorszy niż nazwa (patrz nagłówek ``agent.schema``).
    Podajemy FAKTYCZNĄ nazwę powodu z tenanta, bo to ją pracownik zobaczył w prośbie
    o potwierdzenie — model ma potwierdzać to samo, co przeczytał człowiek.
    """
    opis: list[dict[str, Any]] = []
    for wpis in wpisy:
        weekday = wpis.get("weekday")
        if isinstance(weekday, int) and 0 <= weekday <= 6:
            opis.append(
                {
                    "dzien": PELNE_NAZWY_DNI[weekday],
                    "powod": str(wpis.get("reason_name") or "nieobecność"),
                }
            )
    return opis


class ZrodloTygodnia(Protocol):
    """To, czego czytnik potrzebuje od snapshotu przebiegu — celowo dwie metody, nie cała klasa.

    Protocol, a nie import ``runtime.snapshot``: ``runtime`` importuje ``agent``, więc import
    w drugą stronę zamknąłby cykl między pakietami (pilnuje tego ``tests/test_zaleznosci.py``).
    Typ danych (``DaneTygodnia``) mieszka w ``domain``, dokąd wolno sięgać obu warstwom — i to jest
    cała cena tego rozwiązania.

    **Kontrakt WYJĄTKÓW jest częścią tego Protokołu, nie szczegółem implementacji.** Wolno rzucić
    ``AuthExpiredError`` (dotyczy całej usługi) i ``GraphTruncatedReadError`` (awaria trwała,
    fail-closed dla zapisu — N19). Każdy inny wyjątek czytnik zamienia na ``OdczytNiedostepny``,
    bo z granicy narzędzi nie ma prawa wyjść nic innego (**N23**).
    """

    def dla_tygodnia(
        self, week_start: str, *, alert_przy_porazce: bool = True
    ) -> DaneTygodnia | None:
        """Dane tygodnia albo ``None``, gdy odczytu nie udało się wykonać."""

    def powody_zespolu(self) -> TeamReasons:
        """Aktywne powody czasu wolnego zespołu."""


class SnapshotGrafikReader:
    """Odczyt grafiku JEDNEJ osoby dla narzędzia ``shifts_read`` — **wyłącznie ze snapshotu** (N35).

    Do 0.2.13 ta klasa trzymała własnego ``GraphClient`` i czytała Shifts sama, obok snapshotu
    przebiegu. Dwa źródła prawdy o tym samym grafiku w obrębie jednego przebiegu znaczyły, że ta
    sama odpowiedź pracownika mogła dać różny wynik zależnie od tego, czy dane zmieniły się między
    jednym odczytem a drugim — a warstwa agenta wymagała ``httpx.MockTransport`` zamiast atrapy
    snapshotu, żeby cokolwiek przetestować.

    **Czym to NIE jest: sufitem przeciw niezaufanemu wejściu.** Czytnik memoizował odczyt per
    tydzień od 0.2.12 i żył jedną porcję wiadomości, więc liczba pobrań była już niezależna od
    tego, co pracownik napisze. Zysk jest proporcjonalny do LICZBY OSÓB w przebiegu: dwadzieścia
    osób w tym samym tygodniu to dziś jedno pobranie zamiast dwudziestu.

    Zawężenie do jednej osoby i jednego tygodnia zostaje TU, nie w wywołaniu narzędzia — schematy
    narzędzi nie mają pola na identyfikator pracownika, więc model nie ma jak go rozszerzyć,
    mimo że snapshot niesie dane całego zespołu.
    """

    def __init__(
        self,
        zrodlo: ZrodloTygodnia,
        *,
        member_id: str,
        week_start: date,
        tz: ZoneInfo,
    ) -> None:
        self._zrodlo = zrodlo
        self._member_id = member_id
        self._week_start = week_start
        self._tz = tz

    def _klucz(self, tydzien: str) -> str:
        """Klucz snapshotu (ISO poniedziałku) dla „docelowy" albo „poprzedni"."""
        return (self._week_start - timedelta(days=7 if tydzien == "poprzedni" else 0)).isoformat()

    def _dane(self, tydzien: str) -> DaneTygodnia:
        """Dane tygodnia ze snapshotu — każda porażka zamienia się w ``OdczytNiedostepny``.

        **Adapter wyjątku jest tu obowiązkowy, nie kosmetyczny.** ``snapshot.dla_tygodnia`` rzuca
        ``GraphTruncatedReadError``, żeby fail-closed obowiązywał każdego wołającego (N19) — i to
        jest właściwe zachowanie na ścieżce ZAPISU, gdzie „mam połowę danych" musi wstrzymać wpis.
        Tutaj ten sam wyjątek wyszedłby z ``tools.wykonaj()``, czyli złamał N23: przerwałby obsługę
        CAŁEJ odpowiedzi, a że watermark rośnie dopiero po sukcesie (N11), ta sama wiadomość
        wracałaby w każdym ticku — bez końca i bez śladu dla pracownika.

        Wstrzymanie zapisu NIE znika przez to z systemu: ``listener._odsiej_juz_zapisane`` woła
        snapshot BEZPOŚREDNIO, a snapshot pamięta ucięcie i rzuca je ponownie. Model dostaje więc
        „nie wiadomo", a zapis nadal się nie odbywa — dwie różne odpowiedzi na tę samą awarię,
        każda właściwa dla swojej granicy.
        """
        try:
            dane = self._zrodlo.dla_tygodnia(
                self._klucz(tydzien),
                # ŻADEN odczyt tego czytnika nie poprzedza zapisu — także dla tygodnia docelowego.
                # Interpretacja wiadomości kończy się prośbą o „tak", a zapis idzie dopiero
                # w kolejnym ticku, na świeżym snapshocie. Alert obiecuje operatorowi, że
                # „potwierdzenia z tego tygodnia zostaną W TYM CYKLU zapisane bez sprawdzenia"
                # i każe ręcznie obejrzeć grafiki — w przebiegu, w którym nikt nie odpowiedział
                # „tak", byłoby to ostrzeżenie przed szkodą, która się nie wydarzyła.
                #
                # Nic przez to nie ginie: alert jest odłożony od DANYCH (patrz
                # ``SnapshotGrafiku.dla_tygodnia``), więc gdy w tym samym przebiegu ścieżka zapisu
                # sięgnie po ten tydzień, dostanie wynik z pamięci i **wtedy** odpali ostrzeżenie.
                alert_przy_porazce=False,
            )
        except AuthExpiredError:
            raise  # utrata sesji dotyczy całej usługi, nie tej jednej odpowiedzi
        except GraphTruncatedReadError as blad:
            logger.warning(
                "Odczyt grafiku ucięty na limicie stron — narzędzie zwraca »nie wiadomo«"
            )
            raise OdczytNiedostepny(str(blad)) from blad
        except Exception as blad:
            # Siatka domykająca, o tej samej szerokości co przed D2. Poprzedni czytnik miał
            # `except Exception` przy każdym wywołaniu Graph; zawężenie jej do dwóch typów
            # postawiłoby **N23 na braku wyjątku, nie na kontrakcie**. `ZrodloTygodnia` jest
            # Protokołem — pierwsza nowa klasa błędu w snapshocie albo pierwsza inna implementacja
            # wnosi wtedy regresję po cichu, a jej objawem jest wiadomość wracająca w każdym ticku.
            logger.warning("Nieoczekiwana awaria źródła grafiku dla narzędzia: %s", blad)
            raise OdczytNiedostepny(str(blad)) from blad
        if dane is None:
            raise OdczytNiedostepny(f"snapshot nie ma grafiku tygodnia {tydzien}")
        return dane

    def powody_zespolu(self) -> TeamReasons:
        """Powody czasu wolnego zespołu — jedno pobranie na PRZEBIEG, nie na wiadomość.

        Publiczne, bo ta sama lista jest potrzebna po zakończeniu pętli modelu, przy rozstrzyganiu
        powodów do zapisu (``listener._interpret_and_confirm`` → ``resolve_time_off``).
        """
        try:
            return self._zrodlo.powody_zespolu()
        except AuthExpiredError:
            raise
        except Exception as blad:
            logger.warning("Nie udało się odczytać powodów czasu wolnego: %s", blad)
            raise OdczytNiedostepny(str(blad)) from blad

    def zmiany(self, tydzien: str) -> list[dict[str, Any]]:
        return opisz_zmiany(
            (s for s in self._dane(tydzien).zmiany if s.user_id == self._member_id), self._tz
        )

    def wolne(self, tydzien: str) -> list[dict[str, Any]]:
        dane = self._dane(tydzien)
        # Powody dociągamy TU, a nie liczymy na to, że model zawołał wcześniej `action="powody"`.
        # Bez tego każdy istniejący dzień wolny wracał opisany jako „nieobecność" niezależnie od
        # faktycznego powodu — wynik narzędzia był cicho nieprawdziwy, czyli dokładnie ta klasa
        # błędu, którą `OdczytNiedostepny` eliminuje dla awarii. Same DNI są wiarygodne nawet bez
        # powodów, więc ich niedostępność degraduje etykietę, a nie cały odczyt.
        try:
            nazwy_powodow = self.powody_zespolu().names
        except OdczytNiedostepny:
            logger.warning("Brak powodów czasu wolnego — dni wolne bez nazwy powodu")
            nazwy_powodow = {}
        dni = off_reason_by_weekday(dane.wolne, self._member_id, dane.poniedzialek)
        return [
            {"dzien": PELNE_NAZWY_DNI[d], "powod": nazwy_powodow.get(dni[d], "nieobecność")}
            for d in sorted(dni)
        ]

    def powody(self) -> list[dict[str, Any]]:
        """Które kanoniczne powody czasu wolnego zespół FAKTYCZNIE ma.

        Model dostaje prawdę, a nie listę z promptu: dotąd mógł zaproponować „chorobowe" w zespole,
        który takiego powodu nie ma, a ``TeamReasons.resolve`` po cichu podmieniał go na inny
        istniejący — pracownik potwierdzał zwolnienie, a w grafiku lądowała „Nieobecność".
        """
        powody = self.powody_zespolu()
        return [
            {
                "kanoniczny": kanoniczny,
                "nazwa_w_zespole": powody.names.get(
                    powody.by_name.get(normalize(display_name(kanoniczny)), ""), ""
                ),
                "dostepny": normalize(display_name(kanoniczny)) in powody.by_name,
            }
            for kanoniczny in POWODY_WOLNEGO
        ]
