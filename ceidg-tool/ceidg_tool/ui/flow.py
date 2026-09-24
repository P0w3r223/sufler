"""Kroki decyzyjne wspólne dla flag CLI, trybu `--tak`, kreatora i asystenta.

Jedno miejsce, w którym zapada kolejność: wznowienie → raport → rocznik PKD → zapytanie
o `count` → tabela kosztów → wybór → ewentualny podział na partie → pobranie → eksport →
podsumowanie (ADR-0008, rozszerzone w ADR-0012). Entry pointy różnią się wyłącznie tym, jaki
`Prompter` podstawią.

Liczba zapytań o `count` — **wszystkie przed zgodą, po niej ani jednego**: jedno na zapytanie,
dwa gdy okres przejściowy PKD daje wybór (po jednym na populację), plus **po jednym na każde
poszerzenie przyjęte przez operatora po zerze trafień** (ADR-0017). To ostatnie jest ograniczone
liczbą filtrów w kryteriach, bo każdy obrót zdejmuje jeden, i jest zapowiedziane na ekranie
(`BRAK_TRAFIEN_KOSZT`); tryb `--tak` nie wydaje ani jednego, bo domyślną odpowiedzią jest wyjście.
Zdanie brzmiało tu „najwyżej dwa" do przeglądu 2026-09-09 — a jest to zdanie, któremu ufa
następna zmiana, i drugi raz z rzędu opisywałoby stan sprzed niej.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from ..assistant import AssistantResult
from ..batching import BatchPlan, plan_batches
from ..criteria import Criteria
from ..errors import CeidgError, ConfigError
from ..estimating import LARGE_COUNT_THRESHOLD, Estimate, estimate
from ..logsetup import LOG_FILE_NAME
from ..pipeline import (
    BatchOutcome,
    BatchResult,
    Deps,
    ExportSummary,
    RunInfo,
    RunResult,
    UpdatePlan,
    choose_report,
    count_hits,
    default_export_path,
    find_resumable,
    output_name,
    plan_update,
    run_batched_fetch,
    run_export,
    run_fetch,
    run_report_fetch,
)
from ..pkddict import load_pkd
from ..pkdmap import Rozszerzenie, TablicaPkd, load_pkd_map
from ..records import Report
from ..reports import filtry_poza_raportem, report_covers, statusy_poza_raportem
from . import prompts, texts
from .prompts import Prompter
from .texts import Block, SummaryInput

# `anuluj` to **nie** to samo co `wyjdz`. `wyjdz` znaczy „obejrzałem koszt i rezygnuję",
# więc kryteria są już rozstrzygnięte i warto pokazać polecenie, które je powtórzy. `anuluj`
# znaczy „nie chcę podejmować tej decyzji" — pada na pytaniu o rocznik PKD, gdzie nic jeszcze
# nie zapadło. Zlanie obu w jedno sprawiało, że kreator pokazywał powtórzenie z wyborem,
# którego operator właśnie odmówił dokonać (audyt 2026-09-07; wtedy było to zapisanie pliku
# zapytania, dziś — wypisanie polecenia, ADR-0022).
Decision = Literal["lista", "szczegoly", "raport", "wznow", "partie", "popraw", "wyjdz", "anuluj"]


class View(Protocol):
    """Wyjście: bloki i komunikaty. Konsola w programie, atrapa w testach."""

    def block(self, block: Block) -> None: ...

    def message(self, text: str) -> None: ...

    def warning(self, text: str) -> None: ...

    def error(self, text: str) -> None: ...


@dataclass(frozen=True)
class FetchPlan:
    """Wszystko, co ustalono przed pobraniem — bez żadnego dodatkowego żądania.

    Próg wędruje w planie, a nie osobnym argumentem `execute`: gdyby wykonanie brało go
    skądinąd, mogłoby dzielić partie inaczej, niż zapowiedziała tabela pokazana operatorowi."""

    criteria: Criteria
    count: int
    threshold: int
    estimate: Estimate | None = None
    report: Report | None = None
    resumable: RunInfo | None = None
    batches: BatchPlan | None = None


def collect_from_description(
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    opis: str,
    today: date | None = None,
) -> Criteria | None:
    """Zdanie po polsku → potwierdzone `Criteria`. `None` znaczy „pytaj po kolei".

    Jedna implementacja dla kreatora i dla `pobierz --opis`, żeby oba wejścia nie mogły się
    rozjechać — ten sam argument, który w ADR-0008 wyprodukował całą warstwę `ui/`.

    Krok stoi **przed** `prepare_fetch`, więc liczba żądań `count`, tabela kosztów i ścieżka
    zgody zostają nietknięte — także runda dopytania, która toczy się w całości nad modelem.
    Operator potwierdza interpretację, zanim wyda się choć jedno żądanie do CEIDG.
    """
    if deps.assistant is None:
        # Powód, gdy go znamy, zamiast domysłu. `load_pkd` potrafi powiedzieć „brak słownika
        # i oto polecenie, które go zbuduje" — zastępowanie tego zdaniem o kluczu było
        # wskazywaniem palcem na rzecz, która akurat działała (przebieg B6).
        #
        # Bez odwrotu do zdania domyślnego: przy zamkniętym zbiorze powodów nie ma już czego
        # się cofać. Para `assistant is None` ⇒ `brak is not None` obowiązuje **po
        # `build_deps(asystent=True)`**, a nie zawsze: wywołanie domyślne zostawia oba `None`
        # legalnie i to jest stan „nieproszony". Tutaj jest on jednak błędem programu, bo
        # `pobierz --opis` i kreator zawsze proszą — więc zdanie niżej wymienia obie przyczyny
        # zamiast wskazywać palcem na jedną z nich.
        if deps.assistant_brak is None:
            raise ConfigError(texts.ASYSTENT_BEZ_POWODU)
        raise ConfigError(texts.assistant_unavailable(deps.assistant_brak))
    dzisiaj = today or datetime.now(tz=UTC).date()
    while True:
        # Zapowiedź **przed** paskiem, bo po jego uruchomieniu nic się już nie wypisze.
        # Bez niej operator patrzy na licznik bez skali i nie wie, czy to sekundy, czy minuty.
        view.message(texts.ASSISTANT_THINKING)
        try:
            wynik = deps.assistant.interpret(opis, dzisiaj=dzisiaj)
        finally:
            # Pasek gaśnie razem z pytaniem do modelu, a nie dopiero na końcu polecenia.
            # Póki żył, żywy `rich` nadpisywał interpretację, pytanie o zatwierdzenie i błąd,
            # gdyby padł — dokładnie ten defekt, który bramka 3 wyłapała 2026-09-06 na ścieżce
            # pobierania. Asystent to szósty kanał postępu i doszedł już po tamtej poprawce.
            deps.events.close()
        if wynik.kryteria.is_empty():
            # Runda dopytania (ADR-0017). Warunkiem jest **pustka w kryteriach**, a nie to,
            # czy model o coś zapytał: gdyby ekran zależał od jego `pytanie`, obietnica
            # „opis bez filtrów nigdy nie kończy się błędem" trzymałaby się zachowania modelu.
            # Bez tej gałęzi operator zatwierdzał pustą interpretację (domyślna odpowiedź:
            # „tak, szukaj"), a `prepare_fetch` odsyłał go do menu, gubiąc opis.
            dalej = _dopytaj(wynik, opis, prompter, view)
            if dalej is None:
                return None
            if isinstance(dalej, Criteria):
                return dalej
            opis = dalej
            continue
        view.block(
            texts.interpretation(
                opis,
                wynik.kryteria.describe(),
                wynik.kody_pkd,
                [str(kod) for kod in wynik.ograniczenia],
            )
        )
        wybor = prompter.ask(prompts.ZATWIERDZ_INTERPRETACJE)
        if wybor == "tak":
            return wynik.kryteria
        if wybor == "popraw":
            opis = prompter.text(prompts.OPIS).strip()
            if not opis:
                return None
            continue
        if wybor == "pytania":
            return None
        raise prompts.CancelledError(texts.ASSISTANT_CANCELLED)


def _pytanie_dopytania(pozycje: Sequence[tuple[str, str]]) -> prompts.Question:
    """Pytanie rundy dopytania — numerowane, jak menu główne kreatora.

    Numery, a nie słowa: pozycje są w większości **zdaniami** przysłanymi przez model, więc
    nie mają krótkiej nazwy, którą dałoby się wpisać w trybie awaryjnym (`input` zamiast
    strzałek)."""
    return prompts.Question(
        id=prompts.DOPYTANIE.id,
        text=prompts.DOPYTANIE.text,
        options=tuple(
            prompts.Option(str(i), etykieta) for i, (_, etykieta) in enumerate(pozycje, start=1)
        ),
        default="1",
        safe_default=prompts.DOPYTANIE.safe_default,
    )


def _dopytaj(
    wynik: AssistantResult, opis: str, prompter: Prompter, view: View
) -> Criteria | str | None:
    """Jedna runda dopytania. Trzy możliwe wyjścia i **żadne z nich nie jest błędem**.

    * `str` — nowy opis, który wraca do modelu (wybrana propozycja albo własne zdanie);
    * `Criteria` — gotowe kryteria z drogi niezależnej od modelu (województwo z listy);
    * `None` — operator sam wybrał pytania po kolei.

    Wyjście do menu idzie wyjątkiem `CancelledError`, tak samo jak przy odmowie zatwierdzenia
    interpretacji — to jedna droga wyjścia dla całej ścieżki asystenta, nie dwie.
    """
    pozycje = texts.clarification_menu(wynik.propozycje)
    view.block(texts.clarification(opis, wynik.pytanie))
    numer = int(prompter.ask(_pytanie_dopytania(pozycje)))
    akcja, etykieta = pozycje[numer - 1]
    if akcja.startswith("opis:"):
        # Propozycja modelu wraca jako **nowy opis**, nie jako gotowe kryteria. Dzięki temu
        # przechodzi tę samą drogę co zdanie operatora: walidator PKD, słownik lokalny
        # i ekran potwierdzenia układany przez kod. Model nie zyskuje krótszej ścieżki
        # do `Criteria` tylko dlatego, że tekst pochodzi od niego.
        return etykieta
    if akcja == "wojewodztwo":
        # Droga bez modelu: szesnaście wartości ze zbioru zamkniętego. Nie wraca do
        # `interpret`, bo nie ma czego tłumaczyć — a żądanie do modelu po to, żeby odczytał
        # nazwę, którą operator właśnie wybrał z listy, byłoby wydatkiem na własną odpowiedź.
        return Criteria(wojewodztwo=(prompter.ask(prompts.WOJEWODZTWO_Z_LISTY),))
    if akcja == "wlasny":
        nowy = prompter.text(prompts.OPIS).strip()
        return nowy or None
    if akcja == "pytania":
        return None
    raise prompts.CancelledError(texts.ASSISTANT_CANCELLED)


def show_first_screen(view: View, deps: Deps, *, version: str) -> None:
    # Wyłączenie flagą czytamy z `Deps`, a nie z kolejnego parametru przeprowadzanego przez
    # `wizard`: powód i tak już tam jest, a parametr obok niego znaczyłby, że dwie rzeczy mówią
    # o jednym stanie i mogą się rozejść.
    brak = deps.assistant_brak
    view.block(
        texts.first_screen(
            deps.settings,
            now=datetime.now(tz=UTC),
            version=version,
            demo=deps.demo,
            bez_asystenta=brak is not None and brak.powod == "WYLACZONY_FLAGA",
        )
    )
    for warning in (*deps.settings.warnings, *deps.warnings, *_sprostowanie_o_asystencie(deps)):
        view.warning(warning)


def _sprostowanie_o_asystencie(deps: Deps) -> tuple[str, ...]:
    """Sprostowanie wiersza §A, gdy klucz jest, a asystent się nie zbudował (ADR-0025, decyzja 3).

    `texts._assistant_destination` rozstrzyga po `settings.anthropic_key`, a dostępność zapada
    dopiero przy budowie — więc przy kluczu obecnym i brakującym słowniku PKD ekran obiecuje
    „wysyłam do api.anthropic.com" o asystencie, którego nie ma. Menu tuż pod spodem mówi już
    prawdę (`pobierz_menu_item(deps.assistant is not None)`), czyli **jeden ekran przeczy
    drugiemu** i tylko jeden z nich się myli.

    Sprostowanie stoi tutaj, a nie w `build_deps`: to jest warstwa, która wypisała obietnicę,
    a `pipeline` nie importuje `ui` i nie ma zacząć — kod powodu jest faktem i należy do
    `pipeline`, zdanie jest tekstem i należy do `texts` (ta sama granica co przy raporcie).

    Cisza przy braku klucza jest zamierzona: to stan normalny (ADR-0011, decyzja 9), ma własny
    wiersz na ekranie i ostrzeganie o nim byłoby szumem przy każdym uruchomieniu narzędzia,
    którego większość używa bez asystenta. Tak samo przy wyłączeniu flagą — nikt nie potrzebuje
    ostrzeżenia o tym, o co sam poprosił."""
    brak = deps.assistant_brak
    if brak is None or brak.powod in ("BRAK_KLUCZA", "WYLACZONY_FLAGA"):
        return ()
    return (texts.assistant_unavailable(brak),)


def _ostrzez_o_nieznanych_pkd(criteria: Criteria, view: View) -> None:
    """Kod spoza obu roczników przestaje być cichym 204 (ADR-0011, znalezisko F8).

    `Criteria` sprawdza sam **kształt**, więc `--pkd 9999Z` przechodzi, API odpowiada 204,
    a operator czyta „Brak firm spełniających kryteria" — nie do odróżnienia od pustego
    rejestru przez kogoś, kto z założenia nie zna API. Dokładnie ta dziura, którą słownik
    zamknął dla modelu i która została otwarta dla flagi; F8 stoi otwarte od 2026-09-07.

    **Ostrzeżenie, nie odmowa** (ADR-0026, decyzja 4). Nasza strona 2007 to klucz przejścia,
    a nie pełna lista PKD 2007, więc „nie ma w obu plikach" nie znaczy „nie istnieje" —
    a fałszywa odmowa zablokowałaby zapytanie, na które rejestr by odpowiedział. Narzędzie nie
    przelicytowuje rejestru na dowodach, których nie ma.

    Jedno zdanie na wywołanie, nie jedno na kod: trzy ostrzeżenia pod rząd uczą je pomijać.

    Słownik wczytywany **tutaj i leniwie**, a nie w `build_deps`: większość wywołań nie ma
    `--pkd` wcale, a ADR-0025 jest właśnie o niepłaceniu za to, czego dana ścieżka nie użyje.
    Brak pliku wyłącza **sprawdzenie**, nie narzędzie — ta sama zasada, którą `pkd_map` trzyma
    w `pipeline`.
    """
    if not criteria.pkd:
        return
    try:
        slownik = load_pkd()
    except CeidgError:
        return
    try:
        tablica: TablicaPkd | None = load_pkd_map()
    except CeidgError:
        tablica = None
    obce = [
        kod
        for kod in criteria.pkd
        if kod not in slownik and (tablica is None or tablica.nazwa_2007(kod) is None)
    ]
    if obce:
        view.warning(texts.nieznane_kody_pkd(tuple(obce)))


def _z_rocznikiem(criteria: Criteria, kody_2007: tuple[str, ...]) -> Criteria:
    """Kopia kryteriów z ustawionym `pkd_2007`, **przepuszczona przez walidację**.

    `model_copy(update=…)` w pydanticu v2 waliduje pominąć — a to `_dedupe` sortuje krotki
    właśnie po to, żeby odcisk palca nie zależał od kolejności wejścia. Bez walidacji kandydat
    szeroki miał kody posortowane wewnątrz każdej połówki, ale nie razem, więc ta sama treść
    wczytana ponownie dawała **inny odcisk** i przerwany przebieg stawał się niewidoczny dla
    wznowienia (audyt 2026-09-07; wtedy „ponownie" znaczyło „z pliku zapytania", dziś —
    z powtórzonego polecenia, ADR-0022).
    """
    return Criteria.model_validate({**criteria.model_dump(), "pkd_2007": kody_2007})


def _kandydaci(
    criteria: Criteria, deps: Deps, wybor: bool | None
) -> tuple[Criteria, Criteria, Rozszerzenie | None]:
    """Buduje parę kandydatów (wąski, szeroki) dla okresu przejściowego PKD. Zero żądań.

    Wąski niesie już rozszerzenia **czyste** — kody 2007 znaczące dokładnie to samo co wybrane,
    więc ich dołożenie nie jest wyborem, tylko naprawą. Szeroki dokłada niejednoznaczne, czyli
    te, które wciągają cudzą branżę; różnica między kandydatami jest dokładnie tym, o co pyta
    `prompts.ROCZNIK_PKD`.

    Zwraca `Rozszerzenie` tylko wtedy, gdy jest o czym mówić — `None` znaczy „nic się nie
    zmienia", czyli najczęstszy przypadek (464 z 728 podklas).
    """
    if criteria.pkd_2007 and wybor is False:
        # Plik zapytania niesie rozszerzenie, a flaga mówi „bez rocznika 2007". Do przeglądu
        # 2026-09-07 wygrywał plik, po cichu: program pobierał populację szerszą, niż operator
        # przed chwilą zażądał, i nie mówił o tym ani słowa. Cisza o poszerzonym zapytaniu to
        # dokładnie ten defekt, dla którego powstał ADR-0012, więc sprzeczność jest błędem,
        # a nie sytuacją do rozstrzygnięcia domyślnie na czyjąś korzyść.
        raise ConfigError(texts.vintage_conflict(criteria.pkd_2007))
    if deps.pkd_map is None or not criteria.pkd or criteria.pkd_2007:
        # Pole już ustawione znaczy wcześniejszy wybór (wznowienie, `--pkd-2007`): powtórzenie
        # przebiegu ma dać ten sam wynik, więc niczego tu nie przeliczamy.
        return criteria, criteria, None
    rozsz = deps.pkd_map.rozszerz(criteria.pkd)
    if rozsz.is_empty():
        return criteria, criteria, None
    if wybor is False:
        # Jawne „bez rocznika 2007" albo przebieg nieinteraktywny. Kryteriów nie ruszamy, ale
        # `Rozszerzenie` **oddajemy**, bo bez niego nie dałoby się powiedzieć operatorowi,
        # czego zapytanie nie obejmuje — a milczenie jest tu całym defektem.
        return criteria, criteria, rozsz
    czyste = tuple(p.kod for p in rozsz.czyste)
    niejedno = tuple(p.kod for p in rozsz.niejednoznaczne)
    waskie = _z_rocznikiem(criteria, czyste) if czyste else criteria
    szerokie = _z_rocznikiem(criteria, (*czyste, *niejedno))
    if wybor is True:
        # `--pkd-2007` wybiera szeroko bez pytania; kandydaci schodzą się do jednego.
        return szerokie, szerokie, rozsz
    return waskie, szerokie, rozsz


def _zapytaj_o_rocznik(
    rozsz: Rozszerzenie,
    waskie: Criteria,
    szerokie: Criteria,
    prompter: Prompter,
    view: View,
    *,
    licznik_waski: int | None = None,
    licznik_szeroki: int | None = None,
) -> Criteria | None:
    """Pokazuje, co dołoży szerszy wybór, i pyta. `None` znaczy „wróć do menu"."""
    view.block(
        texts.vintage_offer(
            [(p.kod, p.nazwa, p.rowniez, p.dzis) for p in rozsz.niejednoznaczne],
            waskie=licznik_waski,
            szerokie=licznik_szeroki,
        )
    )
    odpowiedz = prompter.ask(prompts.ROCZNIK_PKD)
    if odpowiedz == "wyjdz":
        return None
    return szerokie if odpowiedz == "szerokie" else waskie


@dataclass(frozen=True)
class _Kandydaci:
    """Trzy postacie kryteriów, którymi operuje `prepare_fetch`, plus to, co o nich wiadomo.

    Trzy, a nie dwie, bo pod każdą z nich mógł zapisać się przerwany przebieg — patrz
    `_oferta_wznowienia`. `pytac_o_rocznik` jest tu, a nie liczone u wołającego, bo warunek
    zależy od wszystkich trzech pól naraz i przepisany drugi raz rozjechałby się po cichu.
    """

    wejsciowe: Criteria
    waskie: Criteria
    szerokie: Criteria
    rozsz: Rozszerzenie | None
    pytac_o_rocznik: bool


def _przygotuj_kandydatow(
    criteria: Criteria, deps: Deps, view: View, rocznik_2007: bool | None
) -> _Kandydaci:
    """Rozstrzyga okres przejściowy PKD i **pokazuje** operatorowi, co dołożył. Zero żądań."""
    waskie, szerokie, rozsz = _kandydaci(criteria, deps, rocznik_2007)
    if rozsz is not None and rocznik_2007 is False:
        view.warning(texts.vintage_skipped(rozsz.kody_2007))
    elif rozsz is not None:
        # Zastosowane bez pytania, ale **pokazane**: ciche poszerzenie zapytania to ten sam
        # defekt co cicha podmiana kodu przez model — wynik, którego operator nie wytłumaczy.
        # Przy `--pkd-2007` pokazujemy **wszystkie** dołożone kody, także niejednoznaczne:
        # flaga jest zgodą na dołożenie kodów, nie na nieoglądanie tego, co dokładają.
        pokazane = rozsz.czyste if rocznik_2007 is None else (*rozsz.czyste, *rozsz.niejednoznaczne)
        if pokazane:
            view.block(
                texts.vintage_applied([(p.kod, p.nazwa, p.rowniez, p.dzis) for p in pokazane])
            )
    return _Kandydaci(
        wejsciowe=criteria,
        waskie=waskie,
        szerokie=szerokie,
        rozsz=rozsz,
        pytac_o_rocznik=rozsz is not None and rozsz.wymaga_pytania and rocznik_2007 is None,
    )


def _oferta_wznowienia(
    kand: _Kandydaci, deps: Deps, prompter: Prompter, view: View, threshold: int
) -> FetchPlan | None:
    """Szuka przerwanego przebiegu i pyta, czy go dokończyć. `None` znaczy „idziemy dalej".

    Sprawdzamy **wszystkie trzy** postacie kryteriów, bo każda ma własny odcisk palca,
    a przerwany przebieg zapisał się pod tą, która obowiązywała wtedy:
      * `waskie`  — dzisiejsze wejście, z rozszerzeniem czystym, jeśli jakieś jest,
      * `szerokie` — przebieg, w którym ktoś wybrał „szerzej",
      * `wejsciowe` — bez żadnego rozszerzenia: tak zapisuje się `--tak`, `--bez-pkd-2007`
        i każdy przebieg sprzed ADR-0012.
    Trzeciej postaci brakowało do przeglądu 2026-09-07 i nie wymagało to różnicy wersji:
    harmonogram z `--tak` przerwany w połowie był niewidoczny dla ręcznego wejścia z tym samym
    `--pkd`, więc operator zaczynał od zera, mając robotę w bazie. Baza, nie sieć — zero żądań.
    """
    widziane_odciski: set[str] = set()
    for kandydat in (kand.waskie, kand.szerokie, kand.wejsciowe):
        odcisk = kandydat.fingerprint()
        if odcisk in widziane_odciski:
            continue  # ta sama postać kryteriów; drugie zapytanie do bazy niczego nie doda
        widziane_odciski.add(odcisk)
        resumable = find_resumable(kandydat, deps)
        if resumable is None:
            continue
        # Znaleziony kandydat zostaje w ZMIENNEJ LOKALNEJ, a kryteria wołającego są nietknięte
        # aż do przyjęcia oferty. Przypisanie go wcześniej zostawiało po odmowie kryteria przy
        # znalezionej populacji — a reszta przepływu leciała nią, choć ekran zapowiedział co
        # innego. Przy jawnej fladze `--pkd-2007` odmowa wręcz kasowała jej działanie
        # (audyt 2026-09-07).
        view.message(
            texts.resume_offer(
                resumable.run_id, resumable.status, resumable.records_seen, kandydat.describe()
            )
        )
        if prompter.confirm(prompts.WZNOWIC, default=True):
            return FetchPlan(criteria=kandydat, count=0, threshold=threshold, resumable=resumable)
        return None
    return None


def _sciezka_raportu(
    kand: _Kandydaci,
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    source: str,
    threshold: int,
) -> tuple[Decision, FetchPlan] | None:
    """Rozstrzyga, czy pobrać z gotowego raportu. `None` znaczy „idziemy ścieżką API".

    Kryteria bierzemy z `kand.waskie`, a nie osobnym argumentem: para (kryteria, kandydaci)
    podana z zewnątrz dopuszczałaby wywołanie niespójne, a `_Kandydaci` powstało właśnie po to,
    żeby to, co policzone raz, nie było przepisywane drugi raz.
    """
    criteria = kand.waskie
    report: Report | None = None
    if source in ("auto", "raport") and report_covers(criteria):
        report = choose_report(criteria, deps)
        if report is not None:
            view.block(texts.report_offer(report))
            if source == "raport" or prompter.confirm(prompts.UZYC_RAPORTU, default=True):
                if kand.pytac_o_rocznik and kand.rozsz is not None:
                    # Bez liczb, bo tu ich jeszcze nie ma — raport filtrujemy lokalnie, więc
                    # policzenie obu populacji wymagałoby najpierw ściągnięcia archiwum.
                    # Wybór zostaje przy operatorze; nieznana liczba jest lepsza od zmyślonej.
                    wybrane = _zapytaj_o_rocznik(
                        kand.rozsz, kand.waskie, kand.szerokie, prompter, view
                    )
                    if wybrane is None:
                        return "anuluj", FetchPlan(criteria=criteria, count=0, threshold=threshold)
                    criteria = wybrane
                return "raport", FetchPlan(
                    criteria=criteria, count=0, threshold=threshold, report=report
                )
    if source == "raport" and report is None:
        # Nie wyjątek i nie nazwa flagi: powód plus jedno pytanie. Kreator nie ma `--zrodlo`,
        # a operator, który stracił tu kryteria, przepisywał opis od zera — przy czym nie
        # dowiadywał się nawet, **co** poprawić, bo powód nigdy nie padał.
        statusy = statusy_poza_raportem(criteria)
        view.block(texts.report_unavailable(_powod_braku_raportu(criteria, statusy), statusy))
        if not prompter.confirm(prompts.RAPORT_NA_API, default=True):
            return "wyjdz", FetchPlan(criteria=criteria, count=0, threshold=threshold)
    return None


def _policz_populacje(
    kand: _Kandydaci, deps: Deps, prompter: Prompter, view: View
) -> tuple[Criteria, int] | None:
    """Zapytania o `count` — wszystkie tutaj, wszystkie przed zgodą. `None` znaczy „anuluj".

    Kryteria z `kand.waskie`, z tego samego powodu co w `_sciezka_raportu`.
    """
    if not (kand.pytac_o_rocznik and kand.rozsz is not None):
        return kand.waskie, count_hits(kand.waskie, deps)
    # Dwa `count` — po jednym na populację — i oba **przed** zgodą. To jest cała cena
    # przeformułowania niezmiennika: operator dostaje rozmiar tego, co ominie albo czego
    # nabierze, zamiast zdania „wynik może być niepełny", na które nie da się odpowiedzieć.
    licznik_waski = count_hits(kand.waskie, deps)
    licznik_szeroki = count_hits(kand.szerokie, deps)
    if licznik_waski == 0 and licznik_szeroki == 0:
        # Wybór między zerem a zerem nie jest wyborem. Do 2026-09-09 ekran pokazywał
        # „Tylko PKD 2025: 0 firm. Ze starymi kodami: 0 firm" i mimo to pytał, którą
        # z tych dwóch pustych populacji operator woli. Oba liczniki są już wydane —
        # nie da się ich cofnąć — ale pytanie tak, i to ono kosztuje uwagę. Dalej
        # obowiązuje ścieżka zera trafień, która proponuje poszerzenie.
        return kand.waskie, 0
    wybrane = _zapytaj_o_rocznik(
        kand.rozsz,
        kand.waskie,
        kand.szerokie,
        prompter,
        view,
        licznik_waski=licznik_waski,
        licznik_szeroki=licznik_szeroki,
    )
    if wybrane is None:
        return None
    # `wybrane` jest jednym z dwóch obiektów, które właśnie podaliśmy — porównanie
    # tożsamości mówi wprost, którą populację policzono, bez rekonstruowania jej z pola.
    return wybrane, (licznik_szeroki if wybrane is kand.szerokie else licznik_waski)


def prepare_fetch(
    criteria: Criteria,
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    source: str = "auto",
    threshold: int = LARGE_COUNT_THRESHOLD,
    today: date | None = None,
    rocznik_2007: bool | None = None,
) -> tuple[Decision, FetchPlan]:
    """Ustala, co zrobić z kryteriami.

    Zapytania o `count` — **wszystkie przed zgodą, po niej ani jednego**: jedno na zapytanie,
    dwa gdy okres przejściowy PKD daje wybór (ADR-0012, sub-decyzja 4: po jednym na populację),
    plus po jednym na każde poszerzenie przyjęte po zerze trafień (ADR-0017). Pętla zera
    trafień zdejmuje przy każdym obrocie jeden filtr, więc ma skończoną długość, a jej koszt
    stoi na ekranie, zanim operator wybierze. Po decyzji nie pada już żadne; pobranie korzysta
    z policzonego. Osobno dochodzi jedno zapytanie o listę raportów, gdy raport pokrywa
    kryteria.

    `rocznik_2007`: `None` znaczy „zapytaj, jeśli jest o co", `True` i `False` to jawny wybór
    z flagi `--pkd-2007/--bez-pkd-2007` — wtedy nie pytamy i nie liczymy drugi raz.

    Kolejność kroków jest niezmiennikiem (ADR-0008) i dlatego została tu, w jednym czytelnym
    ciągu; każdy krok z osobna mieszka w `_`-funkcji wyżej, razem z powodem, dla którego
    wygląda tak, a nie inaczej.
    """
    if criteria.is_empty():
        raise ConfigError(texts.EMPTY_CRITERIA)
    _ostrzez_o_nieznanych_pkd(criteria, view)

    kand = _przygotuj_kandydatow(criteria, deps, view, rocznik_2007)
    criteria = kand.waskie

    wznowienie = _oferta_wznowienia(kand, deps, prompter, view, threshold)
    if wznowienie is not None:
        return "wznow", wznowienie

    z_raportu = _sciezka_raportu(kand, deps, prompter, view, source=source, threshold=threshold)
    if z_raportu is not None:
        return z_raportu

    policzone = _policz_populacje(kand, deps, prompter, view)
    if policzone is None:
        return "anuluj", FetchPlan(criteria=criteria, count=0, threshold=threshold)
    criteria, count = policzone

    # Pętla, nie ciąg prosty: zero trafień wraca tutaj z jednym filtrem mniej, zamiast kończyć
    # rozmowę. Każdy obrót to jedno zapytanie `count` i jedna decyzja operatora, a lista
    # kandydatów kurczy się o zdjęty filtr, więc pętla ma z definicji skończoną długość.
    while True:
        est = estimate(count, deps.profile, max_rekordow=criteria.max_rekordow)
        view.block(
            texts.cost_table(est, threshold=threshold, capped=criteria.max_rekordow is not None)
        )
        if count:
            break
        propozycje = criteria.poszerzenia()
        view.block(texts.zero_hits(criteria, propozycje))
        wybor = prompter.ask(_pytanie_brak_trafien(propozycje))
        if wybor in ("wyjdz", "popraw"):
            return cast(Decision, wybor), FetchPlan(
                criteria=criteria, count=0, threshold=threshold, estimate=est
            )
        criteria = dict(propozycje)[wybor]
        view.block(texts.criteria_block(criteria))
        count = count_hits(criteria, deps)

    if count > threshold and criteria.max_rekordow is None:
        return _over_threshold(criteria, deps, prompter, view, count, est, threshold, today)

    answer = cast(Decision, prompter.ask(_co_dalej_default(criteria)))
    if answer in ("popraw", "wyjdz"):
        return answer, FetchPlan(criteria=criteria, count=count, threshold=threshold, estimate=est)
    updated = criteria.model_copy(update={"szczegoly": answer == "szczegoly"})
    return answer, FetchPlan(criteria=updated, count=count, threshold=threshold, estimate=est)


def _powod_braku_raportu(criteria: Criteria, statusy: Sequence[str]) -> texts.PowodBrakuRaportu:
    """Kod powodu, dla którego ścieżka raportu odpadła. Zdanie układa `texts`.

    Kolejność sprawdzeń idzie od przyczyny **niezależnej od dnia** do zależnej: liczba
    województw, statusy i filtry bez kolumny w zrzucie wynikają z kryteriów i będą prawdziwe
    jutro tak samo, a brak dzisiejszego zrzutu mija sam. Odwrotna kolejność podpowiadałaby
    „poczekaj do jutra" komuś, kto pyta o dwa województwa."""
    if statusy:
        return "STATUS_SPOZA_RAPORTU"
    if filtry_poza_raportem(criteria):
        return "FILTR_SPOZA_RAPORTU"
    if len(criteria.wojewodztwo) > 1:
        return "WIELE_WOJEWODZTW"
    if not criteria.wojewodztwo:
        return "BRAK_WOJEWODZTWA"
    return "BRAK_DZISIEJSZEGO"


def _pytanie_brak_trafien(propozycje: Sequence[tuple[str, Criteria]]) -> prompts.Question:
    """Pytanie po zerze trafień — pozycje zależą od tego, jakie filtry w ogóle są w kryteriach.

    Bez propozycji (jedyny filtr) zostają dwie drogi i **żadna z nich nie jest ślepa**:
    opisać inaczej albo wrócić do menu. Domyślna zostaje „wyjdź”, żeby `--tak` w harmonogramie
    zachowało dzisiejsze zachowanie."""
    pozycje = texts.zero_hits_menu(propozycje)
    return prompts.Question(
        id=prompts.BRAK_TRAFIEN.id,
        text=prompts.BRAK_TRAFIEN.text,
        options=tuple(prompts.Option(wartosc, etykieta) for wartosc, etykieta in pozycje),
        default=prompts.BRAK_TRAFIEN.default,
        safe_default=prompts.BRAK_TRAFIEN.safe_default,
    )


def _co_dalej_default(criteria: Criteria) -> prompts.Question:
    default = "szczegoly" if criteria.szczegoly else "lista"
    return prompts.Question(
        id=prompts.CO_DALEJ.id,
        text=prompts.CO_DALEJ.text,
        options=prompts.CO_DALEJ.options,
        default=default,
    )


def _over_threshold(
    criteria: Criteria,
    deps: Deps,
    prompter: Prompter,
    view: View,
    count: int,
    est: Estimate,
    threshold: int,
    today: date | None,
) -> tuple[Decision, FetchPlan]:
    """Powyżej progu program nigdy nie startuje sam (uzupelnienie-01.md §C, scenariusz 9)."""
    plan = plan_batches(
        criteria, count, today=today or datetime.now(tz=UTC).date(), threshold=threshold
    )
    estimates = [
        estimate(plan.share, deps.profile, max_rekordow=criteria.max_rekordow) for _ in plan.batches
    ]
    view.block(texts.split_table(plan, estimates, szczegoly=criteria.szczegoly))
    answer = cast(Decision, prompter.ask(prompts.PODZIAL))
    return answer, FetchPlan(
        criteria=criteria, count=count, threshold=threshold, estimate=est, batches=plan
    )


def prepare_update(
    deps: Deps,
    prompter: Prompter,
    view: View,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[bool, UpdatePlan]:
    """Wycena aktualizacji i zgoda na nią — ten sam kształt, co przed pobieraniem.

    Zwraca (czy startować, plan). Kosztuje jedno tanie żądanie na okno pięciodniowe;
    domyślny zakres to jedno okno, więc zwykle jedno żądanie. Bez tego kroku `aktualizuj`
    ruszał od razu i operator dowiadywał się o trzydziestu minutach pracy z paska postępu."""
    plan = plan_update(deps, since=since, until=until)
    view.block(
        texts.update_cost_table(
            count=plan.count,
            requests=plan.requests(deps.profile),
            seconds=plan.seconds(deps.profile),
            since=plan.since,
            until=plan.until,
            windows=plan.windows,
        )
    )
    if plan.count == 0:
        return False, plan
    return prompter.confirm(prompts.AKTUALIZOWAC, default=True), plan


# ----------------------------------------------------------------------------- wykonanie


@dataclass(frozen=True)
class ExecuteResult:
    run_ids: tuple[str, ...]
    records: int
    notes: tuple[str, ...] = ()


def execute(
    decision: Decision, plan: FetchPlan, deps: Deps, view: View, *, force_lock: bool = False
) -> ExecuteResult:
    """Uruchamia decyzję podjętą w `prepare_fetch`. Bez pytań — decyzje zapadły wcześniej.

    Pisze natomiast na ekran: podział na partie melduje każdą z nich i kończy tabelą
    (`_run_batches`). Docstring mówił „bez wypisywania tabel", co było nieprawdą od czasu
    partii — a to jest zdanie, które czyta następna osoba, decydując, gdzie dopisać krok po
    pobraniu.

    `force_lock` przejmuje blokadę bazy po procesie, który nie zdążył jej zwolnić (ubity
    w połowie strony). Domyślnie fałsz — kreator nie ma tej flagi, bo nie ma jak potwierdzić,
    że tamten proces naprawdę nie żyje.

    Niespójny plan zgłaszamy wyjątkiem, a nie `assert` — asercje znikają pod `python -O`."""
    if decision == "wznow":
        if plan.resumable is None:
            raise ConfigError("Brak pobrania do wznowienia.")
        result = run_fetch(
            plan.criteria, deps, resume_run_id=plan.resumable.run_id, force_lock=force_lock
        )
        return _from_run(result)
    if decision == "raport":
        if plan.report is None:
            raise ConfigError("Brak raportu do pobrania.")
        result = run_report_fetch(plan.criteria, deps, plan.report, force_lock=force_lock)
        return _from_run(result)
    if decision == "partie":
        if plan.batches is None:
            raise ConfigError("Brak planu partii — powtórz wybór kryteriów.")
        return _run_batches(
            plan.batches, deps, view, threshold=plan.threshold, force_lock=force_lock
        )
    if decision in ("lista", "szczegoly"):
        result = run_fetch(
            plan.criteria, deps, known_count=plan.count or None, force_lock=force_lock
        )
        return _from_run(result)
    raise ConfigError(f"Decyzja {decision!r} nie uruchamia pobierania.")


def _from_run(result: RunResult) -> ExecuteResult:
    # `unresolved` musi mieć czytelnika także tutaj. Liczony był od początku, ale czytało go
    # wyłącznie podsumowanie `aktualizuj` — więc operator, któremu `pobierz --szczegoly`
    # zgubiłby część wpisów, nie zobaczyłby nic. Cichy ubytek na jednej ścieżce, wykryty na
    # drugiej, to ten sam defekt, a nie inny.
    notes = [texts.unresolved_note(result.unresolved)] if result.unresolved else []
    return ExecuteResult(run_ids=(result.run_id,), records=result.records, notes=tuple(notes))


def _run_batches(
    plan: BatchPlan, deps: Deps, view: View, *, threshold: int, force_lock: bool = False
) -> ExecuteResult:
    rows: list[tuple[str, str, int, int]] = []
    total = len(plan.batches)

    def on_batch(outcome: BatchOutcome) -> None:
        rows.append((outcome.label, outcome.status, outcome.count, outcome.records))
        view.message(f"Partia {len(rows)}/{total} — {outcome.label}: {outcome.status}.")

    result: BatchResult = run_batched_fetch(
        plan, deps, threshold=threshold, on_batch=on_batch, force_lock=force_lock
    )
    view.block(texts.batches_table(rows))
    notes: list[str] = []
    if result.missing:
        notes.append(texts.batches_shortfall(result.counted, result.expected, result.missing))
    if result.surplus:
        notes.append(texts.batches_surplus(result.counted, result.expected, result.surplus))
    return ExecuteResult(run_ids=result.run_ids, records=result.records, notes=tuple(notes))


# ----------------------------------------------------------------------------- eksport


def export_and_report(
    run_ids: Sequence[str],
    deps: Deps,
    view: View,
    *,
    out: Path | None = None,
    name_from: Criteria | None = None,
    cel: str | None = None,
    formats: Sequence[str] = ("xlsx",),
    notes: Sequence[str] = (),
) -> ExportSummary:
    """Eksport z bazy plus podsumowanie końcowe — te same zdania w każdym wejściu.

    `name_from` nadaje nazwę pliku z pierwotnych kryteriów; przy pobraniu w partiach
    nazwa z pierwszego runu opisywałaby tylko pierwszą partię."""
    if not run_ids:
        # Pobranie w partiach, w którym każda partia okazała się pusta, nie zakłada żadnego
        # runu. Bez tej furtki nazwa pliku sięgnęłaby po `run_ids[0]` i wysypała się.
        view.message("Żadna partia nie zwróciła rekordów — nie ma czego eksportować.")
        return ExportSummary(
            paths=(), records=0, by_status={}, with_phone=0, with_email=0, sheets=()
        )
    dest = _destination(run_ids, deps, out, name_from)
    summary = run_export(list(run_ids), dest, deps, cel_pobrania=cel, formats=formats)
    view.block(
        texts.summary_table(
            SummaryInput(
                paths=summary.paths,
                records=summary.records,
                by_status=summary.by_status,
                with_phone=summary.with_phone,
                with_email=summary.with_email,
                bez_kontaktow=summary.bez_kontaktow,
                tryb_szczegoly=summary.tryb_szczegoly,
                sheets=summary.sheets,
                kind=summary.kind,
                run_ids=summary.run_ids,
                statuses=summary.statuses,
                log_path=deps.settings.log_dir / LOG_FILE_NAME,
                extra=tuple(("uwaga", note) for note in notes),
            )
        )
    )
    return summary


def _destination(
    run_ids: Sequence[str], deps: Deps, out: Path | None, name_from: Criteria | None
) -> Path:
    if out is not None:
        return out
    if name_from is not None:
        moment = datetime.now(tz=UTC)
        return deps.settings.output_dir / output_name(
            name_from, deps.settings.environment, moment, demo=deps.demo
        )
    return default_export_path(deps, run_ids[0])
