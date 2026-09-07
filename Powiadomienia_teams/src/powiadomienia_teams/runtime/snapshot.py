"""Grafik zespołu odczytany CO NAJWYŻEJ RAZ na przebieg nasłuchu — jedno źródło dla wszystkich
ścieżek, które przed decyzją muszą zajrzeć do Shifts, **łącznie z narzędziami modelu** (N35).

Odczyt jest tu drogi i niepodzielny: ``read_shifts``/``read_time_off`` pobierają CAŁĄ kolekcję
zespołu i filtrują po stronie klienta, bo ``$filter`` na tym endpoincie nie daje się użyć bez
ryzyka cichego zgubienia wpisów (uzasadnienie w docstringu ``GraphClient.read_shifts``). Bez
wspólnego snapshotu przebieg z dwudziestoma potwierdzeniami robił czterdzieści pełnych pobrań,
każde TUŻ PRZED nieodwracalnym zapisem — czyli usługa sama wywoływała dławienie 429 w najgorszym
możliwym momencie, po czym degradowała do zapisu bez weryfikacji.

Zakres życia to JEDEN przebieg ``poll_replies`` i ani chwili dłużej. Cache międzyprzebiegowy byłby
czymś jakościowo innym: sprawdzenie świeżości grafiku ma sens wyłącznie wtedy, gdy dane są świeże,
a jego jedynym zadaniem jest wyłapać uzupełnienie, które nastąpiło od czasu wysłania prośby.

**Wyjątek, jeden i nazwany (ADR 0009):** krok 1.5 nasłuchu — wykrywanie, że pracownik uzupełnił
grafik SAM — korzysta z ``runtime.pamiec_grafiku``, która wyniki przechowuje między przebiegami.
Tam stawka jest inna: nieświeży wynik opóźnia podziękowanie, a nie dubluje wpisy w grafiku
klienta. Ścieżka ZAPISU zostaje przy tym module bez zmian, a ``znane()`` niżej istnieje po to,
żeby dane pobrane przez nią trafiały do tamtej pamięci za darmo. Kierunek jest jednostronny:
snapshot → pamięć.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.config import TeamContext
from powiadomienia_teams.domain.models import DaneTygodnia
from powiadomienia_teams.domain.powody import TeamReasons
from powiadomienia_teams.graph.auth import AuthExpiredError
from powiadomienia_teams.graph.client import GraphClient, GraphTruncatedReadError
from powiadomienia_teams.reminders.detect import off_weekdays_by_member

logger = logging.getLogger(__name__)

_UTC = timezone.utc


class SnapshotGrafiku:
    """Grafik zespołu per tydzień, pobrany najwyżej raz w cyklu życia tego obiektu.

    Klucz to ``week_start`` (ISO poniedziałek), bo odczyt i tak zwraca cały zespół — dwie osoby
    z tego samego tygodnia dzielą jedno pobranie. ``None`` znaczy „odczyt padł", czyli brak
    dowodu; wołający rozstrzyga, co z tym zrobić, i te rozstrzygnięcia są RÓŻNE (wygaszanie nie
    zamyka nikogo bez dowodu, zapis świadomie degraduje do wersji bez weryfikacji).

    **Nieudany odczyt też jest zapamiętywany** — jedno rozstrzygnięcie na tydzień na przebieg
    (samo pobranie ma jedno ponowienie, patrz ``_odczytaj``). Ponawianie go
    per pending przywracałoby dokładnie tę amplifikację, dla której ta klasa powstała, a przyczyna
    awarii (sieć, dławienie) jest właściwością przebiegu, nie konkretnej osoby. Ponowień w obrębie
    jednego żądania pilnuje już ``GraphClient``.

    Cena: dane widziane przez zapis mogą być o przebieg starsze niż w wersji „odczyt tuż przed
    POST-em" — sekundy do minut wobec okna 48 h, którego ten mechanizm pilnuje. Świadoma wymiana,
    bo alternatywą jest ryzyko 429 dokładnie w chwili nieodwracalnego zapisu.
    """

    def __init__(
        self,
        client: GraphClient,
        ctx: TeamContext,
        tz: ZoneInfo,
        *,
        ostrzegaj: Callable[[str, str], None] | None = None,
    ) -> None:
        self._client = client
        self._ctx = ctx
        self._tz = tz
        self._pamiec: dict[str, DaneTygodnia | None] = {}
        self._uciete: dict[str, GraphTruncatedReadError] = {}
        # Powody czasu wolnego zespołu — jedno pobranie na CAŁY przebieg. Do 0.2.13 czytnik
        # narzędzia modelu dociągał je sam, a żył jedną porcję wiadomości, więc rachunek szedł
        # per WIADOMOŚĆ. Lista zmienia się raz na miesiące, a odczyt jest tani — ale jest
        # to żądanie do Graph i ma się liczyć jak wszystkie inne: od liczby osób, nie od tego,
        # ile kto napisze.
        self._powody: TeamReasons | None = None
        # Kanał do CZŁOWIEKA, wstrzykiwany jak w ``GraphClient`` — ten moduł nie może zależeć od
        # `runtime.operator`, bo ten importuje konfigurację i alerty, a snapshot ma zostać
        # narzędziem odczytu. Wywołanie następuje NAJWYŻEJ RAZ NA TYDZIEŃ (patrz ``_zaalarmowane``),
        # więc alert nie ma jak się zapętlić na liczbie potwierdzeń.
        self._ostrzegaj = ostrzegaj
        self._zaalarmowane: set[str] = set()

    def dla_tygodnia(
        self, week_start: str, *, alert_przy_porazce: bool = True
    ) -> DaneTygodnia | None:
        """Dane tygodnia — z pamięci albo z Graph. ``None``, gdy odczytu nie udało się wykonać.

        ``alert_przy_porazce=False`` mówi „ten odczyt nie poprzedza zapisu". Ustawiają je DWAJ
        wołający spoza ścieżki zapisu: czytnik narzędzia modelu
        (`agent/odczyt.py::SnapshotGrafikReader`, dla obu zakresów — interpretacja wiadomości
        kończy się prośbą o „tak", a nie zapisem) oraz wykrywanie samouzupełnienia
        (`runtime/listener.py::poll_replies`, krok 1.5 — dotyczy osób milczących). Treść alertu
        obiecuje
        operatorowi, że *potwierdzenia z tego tygodnia zostaną w tym cyklu zapisane bez
        weryfikacji*, i każe ręcznie obejrzeć grafiki — w przebiegu, w którym nikt nie odpowiedział
        „tak", byłoby to ostrzeżenie przed szkodą, która się nie wydarzyła. Alert mówiący o
        szkodzie,
        której nie było, uczy operatora ignorować kanał, którym przychodzą te prawdziwe.

        **Alert jest odłożony od DANYCH, celowo.** Nieudany odczyt pamiętamy raz na tydzień
        (jedno rozstrzygnięcie na przebieg), ale ostrzeżenie odpalamy przy pierwszym dostępie
        Z PRAWEM DO ALERTU. To rozdzielenie jest tym, co pozwala czytnikowi milczeć bez utraty
        czegokolwiek: gdy w tym samym przebiegu ścieżka zapisu sięgnie po ten tydzień, dostanie
        wynik z pamięci i **wtedy** odpali ostrzeżenie. Bez niego cichy odczyt narzędzia zapisywałby
        ``None`` bezgłośnie, a operator nigdy nie dowiedziałby się, że potwierdzenia poszły bez
        sprawdzenia grafiku.
        """
        if week_start in self._uciete:
            # Ucięcie na limicie stron jest TRWAŁE: kolekcja nie skurczy się w trakcie przebiegu,
            # więc drugie pobranie zwróciłoby to samo, płacąc pięćdziesięcioma stronami za wynik,
            # który już znamy. Wyjątek leci dalej, żeby fail-closed obowiązywał każdego wołającego.
            raise self._uciete[week_start]
        if week_start not in self._pamiec:
            self._pamiec[week_start] = self._odczytaj(week_start)
        dane = self._pamiec[week_start]
        if dane is None and alert_przy_porazce and week_start not in self._zaalarmowane:
            self._zaalarmowane.add(week_start)
            self._ostrzez_o_zapisie_bez_weryfikacji(week_start)
        return dane

    def dla_tygodni(
        self, week_starts: Iterable[str], *, alert_przy_porazce: bool = True
    ) -> dict[str, DaneTygodnia | None]:
        """Dane wielu tygodni naraz — dla ścieżek oceniających całą grupę osób jednocześnie.

        ``alert_przy_porazce`` przechodzi w dół, bo inaczej ten wariant obchodził rozstrzygnięcie
        z ``dla_tygodnia``: wykrywanie samouzupełnienia (krok 1.5) dotyczy z definicji osób
        MILCZĄCYCH, więc alert obiecujący „potwierdzenia z tego tygodnia zostaną zapisane BEZ
        sprawdzenia" mówił o szkodzie, która nie mogła się wydarzyć — nikt nie powiedział „tak"
        i nic nie zostanie zapisane. Ten sam alert był fałszywy Z KONSTRUKCJI przy
        ``--proba-nasluchu``, gdzie klient ma odcięte metody piszące.
        """
        return {
            ws: self.dla_tygodnia(ws, alert_przy_porazce=alert_przy_porazce) for ws in week_starts
        }

    def znane(self) -> dict[str, DaneTygodnia]:
        """Tygodnie odczytane w tym przebiegu POMYŚLNIE — bez porażek i bez nowych pobrań.

        Wyłącznie do zasilenia ``runtime.pamiec_grafiku`` (ADR 0009): dane, za które ścieżka zapisu
        już zapłaciła, mają być dostępne krokowi 1.5 za darmo. Czysty akcesor — nie dotyka Graph
        i nie zmienia stanu, więc nie ma jak zamienić się w drugą drogę odczytu.
        """
        return {ws: dane for ws, dane in self._pamiec.items() if dane is not None}

    def powody_zespolu(self) -> TeamReasons:
        """Aktywne powody czasu wolnego zespołu — pobrane RAZ na przebieg.

        Wyjątki lecą NIEZMIENIONE i celowo nie są pamiętane. Pamiętanie porażki zamieniłoby jedno
        mrugnięcie sieci w przebieg, w którym żadna odpowiedź nie potrafi rozstrzygnąć urlopu —
        a to nie jest awaria tygodnia (jak przy ``_odczytaj``), tylko pojedynczego żądania, które
        następna wiadomość może powtórzyć bez kosztu pięćdziesięciu stron.
        """
        if self._powody is None:
            self._powody = self._client.list_time_off_reasons(self._ctx.team_id)
        return self._powody

    def _pobierz(self, week_start: str) -> DaneTygodnia:
        """Jedno realne pobranie tygodnia z Graph. Wyjątki lecą do ``_odczytaj``."""
        monday = datetime.fromisoformat(week_start).replace(tzinfo=self._tz)  # lokalna północ
        end = monday + timedelta(days=7)
        shifts = self._client.read_shifts(
            self._ctx.team_id, monday.astimezone(_UTC), end.astimezone(_UTC)
        )
        time_off = self._client.read_time_off(
            self._ctx.team_id, monday.astimezone(_UTC), end.astimezone(_UTC)
        )
        return DaneTygodnia(
            zmiany=shifts,
            wolne_dni=off_weekdays_by_member(time_off, monday, self._tz),
            wolne=time_off,
            poniedzialek=monday,
        )

    def _odczytaj(self, week_start: str) -> DaneTygodnia | None:
        """Pobranie z JEDNYM ponowieniem; ``None`` dopiero po dwóch nieudanych próbach.

        Ponowienie jest tu, bo cena porażki urosła razem z zasięgiem tej klasy. ``GraphClient``
        ponawia WYŁĄCZNIE dławienie 429 — pojedyncze 500, zerwane połączenie albo timeout nie są
        ponawiane wcale. A odkąd wynik jest wspólny dla całego przebiegu, jedno takie mrugnięcie
        wyłączało sprawdzenie świeżości nie jednej osobie, lecz KAŻDEMU potwierdzeniu w tym cyklu
        — czyli promień jednej chwilowej awarii sięgał całego tygodnia zapisów.

        Dwie próby, nie trzy: to ma odsiać mrugnięcie, a nie zastępować politykę ponowień klienta.
        Bez odstępu, bo snapshot nie dostaje ``sleep`` — a przy realnej awarii sieci czekanie tuż
        przed nieodwracalnym zapisem jest gorsze niż szybka, jawna degradacja.
        """
        for proba in (1, 2):
            try:
                return self._pobierz(week_start)
            except AuthExpiredError:
                # Utrata sesji dotyczy całej usługi, nie tego tygodnia — i NIE jest zapamiętywana:
                # przebieg i tak się na niej kończy.
                raise
            except GraphTruncatedReadError as blad:
                # Awaria TRWAŁA: kolekcja nie skurczy się między próbami, więc ponowienie kosztuje
                # pięćdziesiąt stron za wynik, który już znamy.
                self._uciete[week_start] = blad
                raise
            except Exception:
                if proba == 1:
                    logger.warning(
                        "Nie udało się odczytać grafiku tygodnia %s — ponawiam raz", week_start
                    )
                    continue
                logger.exception("Nie udało się odczytać grafiku tygodnia %s (Shifts)", week_start)
        return None

    def _ostrzez_o_zapisie_bez_weryfikacji(self, week_start: str) -> None:
        """Powiedz CZŁOWIEKOWI, że potwierdzenia z tego tygodnia pójdą bez sprawdzenia grafiku.

        To NIE jest cichy stan: od tej chwili każde potwierdzenie z tego tygodnia trafi do Shifts
        bez sprawdzenia, czy grafik nie został już uzupełniony — a skutkiem takiego zbiegu jest
        drugi komplet wpisów u klienta, nieodwracalnie, zakończony komunikatem „Zapisałem Twoje
        zmiany". Zgoda na to ryzyko jest świadoma (wstrzymanie zapisu kosztowałoby pracownika
        potwierdzenie przy każdym mrugnięciu sieci), ale świadoma zgoda na nieodwracalną szkodę
        ma dotrzeć do człowieka, a nie zostać w logu.

        Wołane z ``dla_tygodnia``, nie z ``_odczytaj``: alert należy do PYTAJĄCEGO o zapis, a nie
        do chwili nieudanego pobrania — patrz tam.
        """
        if self._ostrzegaj is None:
            return
        self._ostrzegaj(
            "Zapis do grafiku bez sprawdzenia jego aktualności",
            f"Nie udało się odczytać grafiku zespołu na tydzień od {week_start} (dwie próby). "
            "Potwierdzenia z tego tygodnia zostaną w tym cyklu zapisane BEZ sprawdzenia, czy "
            "ktoś nie uzupełnił grafiku w międzyczasie — jeśli uzupełnił, powstanie drugi "
            "komplet wpisów. Sprawdź te grafiki ręcznie.",
        )
