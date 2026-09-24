"""Interfejs wiersza poleceń (typer): flagi i wejście do kreatora.

Cienki adapter — decyzje i teksty należą do `ui` (ADR-0008, reguła granic 9), żeby flagi,
tryb `--tak` i kreator pokazywały dosłownie to samo.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Final, NoReturn
from urllib.parse import urlsplit

import typer
from pydantic import ValidationError

from . import __version__, jsonout
from .apiprofile import load_profile
from .assistant import Asystent
from .clock import Clock, SystemClock
from .config import (
    ENV_DATA_DIR,
    KEYRING_ASSISTANT_USERNAME,
    KEYRING_USERNAME,
    Settings,
    default_data_dir,
    delete_token_from_keyring,
    load_settings,
    safe_filename,
    store_token_in_keyring,
)
from .console import ConsoleEvents, LineEvents
from .criteria import Criteria, bledy_po_polsku
from .demo import TOKEN_DEMO, Demo, zbuduj_demo
from .errors import CeidgError, ConfigError
from .logsetup import close_file_handlers, get_logger, setup_logging
from .normalizer import wiersz_do_json
from .pipeline import (
    Deps,
    ExportSummary,
    build_deps,
    list_resumable,
    lookup_nip,
    purge_report_files,
    run_fetch,
    run_update,
)
from .pkddict import load_pkd
from .pkdmap import TablicaPkd, load_pkd_map
from .pkdszukaj import DOMYSLNY_LIMIT, jako_dane, szukaj
from .progress import Events, LicznikZadan
from .richtext import make_console
from .ui import flow, texts, wizard
from .ui.prompts import (
    OverridePrompter,
    Prompter,
    confirm_line,
    interactive_available,
    is_yes,
    make_prompter,
)
from .ui.render import ConsoleView
from .ui.wynik import Blad, Status, Uwaga, Wynik

app = typer.Typer(
    help="Pobieranie danych JDG z API v3 CEIDG do Excela.",
    no_args_is_help=False,
    add_completion=False,
    rich_markup_mode="rich",
)
token_app = typer.Typer(help="Zarządzanie tokenem w systemowym magazynie haseł.")
app.add_typer(token_app, name="token")
# Jedna konsola całego programu i **na stderr** (ADR-0024, decyzja 2). Ekrany, ostrzeżenia,
# błędy i pasek postępu idą tym samym kanałem co dotąd — tylko innym strumieniem — a stdout
# zostaje wolny dla koperty JSON. Odbiorca zdarzeń dostaje tę samą konsolę, więc pasek jest
# na stderr z konstrukcji, a nie dlatego, że ktoś o tym pamiętał w siedmiu miejscach.
#
# Dla człowieka przy terminalu to zmiana niewidoczna: `rich` opróżnia bufor przy każdym `print`,
# więc przeplot zdań zostaje. Widzi ją ten, kto przekierowuje **sam stdout** do pliku i czyta
# tam błąd — a takiego wołającego to narzędzie dopiero teraz tworzy.
console = make_console(stderr=True)
view = ConsoleView(console)
log = get_logger("cli")

EnvOpt = Annotated[
    str | None, typer.Option("--srodowisko", "-s", help="test (domyślnie) albo prod")
]
ProdOpt = Annotated[
    bool, typer.Option("--produkcja", help="Zgoda na środowisko produkcyjne (dane osobowe)")
]
YesOpt = Annotated[bool, typer.Option("--tak", "-y", help="Tryb nieinteraktywny: decyzje domyślne")]
ForceOpt = Annotated[
    bool,
    typer.Option(
        "--force",
        help="Przejmij blokadę bazy po procesie, który jej nie zwolnił (np. został ubity)",
    ),
]
DemoOpt = Annotated[
    bool,
    typer.Option(
        "--demo",
        help="Pokaz bez rejestru: dane syntetyczne, zero żądań do CEIDG, własny katalog danych",
    ),
]


def _as_utc(value: str | None) -> datetime | None:
    """Data ISO z linii poleceń jako moment UTC; `None` zostaje `None`.

    Jedno miejsce na dwie flagi (`--od`, `--do`), bo naiwny `datetime` przy porównaniu
    ze świadomym strefy podnosi `TypeError` — a to jest para, w której łatwo dodać drugą
    flagę i zapomnieć o strefie."""
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _koniec_zakresu_zmian(value: str | None, clock: Clock) -> datetime | None:
    """`--do` jako moment UTC, z odmową dla przyszłości.

    „Teraz" bierze się z tego samego zegara, co `pipeline.update_range`. Z osobnego
    `datetime.now()` powstawały **dwa różne teraz** w jednym poleceniu, więc przy
    wstrzykniętym zegarze ta bramka przepuszczała datę, którą pipeline widział jeszcze
    jako przyszłą — czyli wpuszczała dokładnie ten stan, przed którym broni.

    Zakres zmian kończący się w przyszłości psuje dwie rzeczy naraz i obie po cichu.
    `set_watermark` zapisuje koniec domkniętego okna, więc znacznik w przyszłości kazałby
    **następnemu** przebiegowi zacząć od tamtego momentu i pominąć wszystko, co zmieni się
    w międzyczasie. A próg świeżości szczegółów to koniec okna, więc leżący w przyszłości
    znaczyłby „szczegół musi pochodzić z przyszłości" i każdy przebieg kupowałby wszystko
    od nowa — ten sam rachunek, który zamyka ADR-0013.

    Odmowa, a nie ciche przycięcie: przycięcie zamieniłoby `--od jutro --do pojutrze`
    w pusty zakres i komunikat „nic się nie zmieniło", czyli odpowiedź na pytanie, którego
    nikt nie zadał. `/zmiana` nie zgłasza zmian, które się nie wydarzyły."""
    koniec = _as_utc(value)
    if koniec is not None and koniec > datetime.fromtimestamp(clock.wall(), tz=UTC):
        raise ConfigError(
            f"--do wskazuje przyszłość ({koniec.date().isoformat()}). Rejestr zgłasza tylko "
            "zmiany, które już nastąpiły; podaj datę nie późniejszą niż dziś."
        )
    return koniec


def dni_retencji(starsze_niz: int | None, domyslne: int) -> int:
    """Ile dni zostawić przy `wyczysc`. Zero znaczy „usuń wszystko", a nie „użyj domyślnych".

    Osobna funkcja, bo to jedyny sposób, żeby ten wybór dało się sprawdzić testem **bez
    uruchamiania `wyczysc`** — polecenia, które kasuje dane osobowe i którego nie chcemy
    wołać ani w suicie, ani przy diagnozie.

    Defekt (audyt 2026-09-08, A2): było `starsze_niz or domyslne`, więc `--starsze-niz 0`
    zamieniało się w 30 dni. Operator prosił o skasowanie danych osobowych, dostawał
    komunikat o powodzeniu i zostawał z miesiącem wpisów o przedsiębiorcach. Że zero jest
    tu sensowną odpowiedzią, dowodzi ta sama komenda kilkanaście linijek dalej, przekazując
    `days=0` do `purge_report_files` w gałęzi `--wszystko`; poprawny idiom (`is not None`)
    stoi w `_criteria_from_options` przy `maks`. Flaga nie miała **żadnego** testu.
    """
    return starsze_niz if starsze_niz is not None else domyslne


def _confirm(question: str, *, default: bool = False) -> bool:
    """Potwierdzenie po polsku, w tym samym rozumieniu co w kreatorze.

    `typer.confirm` przyjmuje wyłącznie `y`/`n`, więc operator, który na polskie pytanie
    odpowiadał „tak", dostawał je z powrotem. Te dwa potwierdzenia zostają poza protokołem
    `Prompter` (ADR-0008: zgoda na produkcję zapada przed powstaniem warstwy `ui`), ale
    rozumienie odpowiedzi jest wspólne — inaczej „tak" znaczyłoby co innego w kreatorze
    i co innego w wierszu poleceń.
    """
    try:
        answer = typer.prompt(
            confirm_line(question, default=default), default="", show_default=False
        )
    except typer.Abort as exc:
        # Ctrl+C i koniec wejścia dawały angielskie „Aborted." i kod 1; kreator na tę samą
        # sytuację odpowiada po polsku i kodem 3, więc niech oba wejścia mówią to samo.
        raise ConfigError("Przerwano pytanie — nic nie zostało zrobione.") from exc
    return is_yes(answer, default=default)


def _events(*, quiet: bool = False) -> ConsoleEvents | LineEvents:
    """Pasek postępu na terminalu, wiersze wszędzie indziej (ADR-0024, decyzja 4).

    Wybór po `console.is_terminal`, czyli po tej samej własności, na którą patrzy sam `rich`
    (`live.py:269`) — a **nie** po `--wynik json`. Gdyby zależał od flagi, jedyną ciszę, jaką
    ten program jeszcze ma, zachowaliby wszyscy, którzy agentami nie są. Animowany pasek
    zostaje tam, gdzie działa.

    Mowa o terminalu **na stderr**, bo tam jest konsola programu: wiersze dostaje harmonogram,
    kontener CI i podproces agenta. `pobierz > log.txt` przy terminalu zabiera stdout i paska
    nie dotyka — zdanie mówiące inaczej stało tu do 2026-09-24 (przegląd kodu).

    `quiet` znaczy to samo w obu: licznik żądań rośnie, postęp się nie pokazuje.
    """
    if console.is_terminal:
        return ConsoleEvents(console, quiet=quiet)
    return LineEvents(console, quiet=quiet)


def _zglos_blad(exc: CeidgError) -> None:
    """Komunikat na stderr i do logu — bez wychodzenia.

    Odłączone od `_fail`, bo `WynikPolecenia` musi zgłosić błąd, a potem jeszcze **wypisać
    kopertę**; gdyby zgłoszenie samo wychodziło, koperta nie miałaby jak powstać i wołający
    dostałby pusty stdout z kodem 3 — czyli dokładnie to zakończenie, które ADR-0024 nazywa
    najgorszym z możliwych.
    """
    view.error(str(exc))
    log.error("%s: %s", type(exc).__name__, exc)


def _fail(exc: CeidgError) -> NoReturn:
    _zglos_blad(exc)
    raise typer.Exit(code=exc.exit_code)


# Kod dla wyjątku spoza taksonomii `errors.py`. Jedynka, czyli „nieodwracalny w tym
# uruchomieniu", bo nic innego o nim nie wiadomo — a `ResumableError` zaprosiłby harmonogram
# do ponawiania czegoś, co nikt nie rozpoznał.
KOD_NIEOCZEKIWANY: Final = 1


class WynikPolecenia:
    """Koperta budowana w trakcie polecenia i **jedyne** miejsce klasyfikujące zakończenie.

    Menedżer kontekstu, nie dekorator: polecenie musi mieć do czego dołożyć swoje pola w środku
    biegu (`koperta.ustaw(...)`), a dekorator dostaje tylko wartość zwróconą. Jedno miejsce,
    a nie ten sam czterodrożny rozbiór przepisany do siedmiu poleceń, bo przepisany rozbiór
    rozjeżdża się cicho — i rozjeżdża się właśnie na tej gałęzi, której nikt nie testuje.

    Cztery zakończenia, z których trzy łatwo pomylić:

    * **`typer.Exit` nie jest błędem.** Polecenia podnoszą je na drogach **udanych**; `finally`
      zamieniający `raise typer.Exit(code=0)` w `blad` to defekt, który sam się prosi.
    * **`CeidgError`** — komunikat jak dotąd, status `blad`, kod z taksonomii `errors.py`.
    * **`KeyboardInterrupt`** przechodzi nietknięty: 130 należy do `run()` poza `app()`,
      a koperta pisana w tym miejscu zabierałaby kod, który wołający właśnie dostaje.
    * **Cokolwiek innego** — status `blad`, koperta wypisana, a wyjątek **puszczony dalej**,
      żeby ślad stosu doszedł. Nic nie jest połykane.

    Kod wyjścia jest liczony, **wpisany do koperty** i dopiero potem podniesiony, więc numer
    wydrukowany i numer rzeczywisty nie mają jak się rozjechać.
    """

    def __init__(self, polecenie: str, *, json: bool, demo: bool = False) -> None:
        self.polecenie = polecenie
        self.json = json
        # Znacznik pokazu musi być znany **od początku**, a nie dopiero z `ustaw`: awaria
        # zwykle wypada, zanim polecenie zdąży cokolwiek ustawić, a wtedy koperta brała
        # `demo` z domyślnej dataklasy i meldowała `false` na przebiegu z pokazu. ADR-0014
        # wymaga pięciu znaczników **łącznie**, a agent przypisujący nieudany pokaz produkcji
        # to jest dokładnie ta pomyłka, której mają razem zapobiegać (przegląd kodu 2026-09-24).
        self.demo = demo
        self._wynik: Wynik | None = None
        self._licznik: LicznikZadan | None = None

    def ustaw(self, wynik: Wynik) -> None:
        self._wynik = wynik

    def licz_zadania(self, licznik: LicznikZadan) -> None:
        """Skąd wziąć `zapytania` — odczytane przy wyjściu, nie przy `ustaw`.

        Bez tego przebieg przerwany **przed** zbudowaniem wyniku meldowałby zero żądań, choć
        wydał trzydzieści; a to jest właśnie ta koperta, na której wołający opiera decyzję
        o ponowieniu. Liczba jest brana z tego samego licznika, który napędzał oznaki życia,
        więc nie ma jak powstać druga.
        """
        self._licznik = licznik

    def __enter__(self) -> WynikPolecenia:
        return self

    def __exit__(self, typ: object, exc: BaseException | None, slad: object) -> bool:
        if isinstance(exc, KeyboardInterrupt):
            return False
        nieoczekiwany = exc is not None and not isinstance(exc, CeidgError | typer.Exit)
        if isinstance(exc, CeidgError):
            _zglos_blad(exc)
            self._wynik = self._blad(exc, exc.exit_code)
        elif nieoczekiwany and exc is not None:
            self._wynik = self._blad(exc, KOD_NIEOCZEKIWANY)
        wynik = self._wynik or Wynik(polecenie=self.polecenie, status="ok")
        if self._licznik is not None:
            wynik = replace(wynik, zapytania=self._licznik.requests)
        if self.json:
            jsonout.wypisz(wynik.koperta())
        if nieoczekiwany:
            return False
        # Pod flagą kod pochodzi **wyłącznie** z koperty, więc własny kod `typer.Exit`
        # podniesionego w środku bloku zostałby zignorowany. Dziś nieosiągalne: żaden blok
        # `WynikPolecenia` nie zawiera `_fail` ani `typer.Exit`, a `flow`, `prompts`,
        # `pipeline` i `wizard` typera nie importują. Zapisane komentarzem, a nie zostawione
        # w prozie klasy, bo to jest **warunek wstępny**, a nie opis (przegląd kodu
        # 2026-09-24): pierwszy `typer.Exit(2)` wewnątrz bloku wyjdzie kodem ze statusu.
        kod = wynik.kod_wyjscia if self.json else _kod_bez_koperty(exc)
        if kod:
            raise typer.Exit(code=kod)
        return True

    def _blad(self, exc: BaseException, kod: int) -> Wynik:
        """Zachowuje **wszystko**, co polecenie zdążyło ustawić, i dokłada błąd.

        Przebieg przerwany po trzydziestu żądaniach kosztował trzydzieści żądań, a taki, który
        zdążył zapisać skoroszyt i poległ przy zamykaniu, ma ten skoroszyt na dysku. Koperta
        błędu jest jedynym miejscem, z którego wołający się o tym dowie.

        `replace`, a nie jedenaście pól przepisanych z ręki. Pierwsza wersja przepisywała je po
        kolei i przeżywała mutację kasującą `kryteria`, `run_ids`, `rekordy`, `pliki`, `uwagi`
        i `dodatki` — testy pilnowały trzech pól z dziewięciu (przegląd testów, 2026-09-24).
        Zamknięcie strukturalne bije tu kolejną asercję: nowe pole `Wynik` jest przenoszone
        z konstrukcji, więc lista nie ma jak się rozjechać z definicją.
        """
        blad = Blad(typ=type(exc).__name__, komunikat=str(exc), kod=kod)
        if self._wynik is None:
            return Wynik(polecenie=self.polecenie, status="blad", demo=self.demo, blad=blad)
        return replace(self._wynik, status="blad", blad=blad)


WYNIK_EKRAN: Final = "ekran"
WYNIK_JSON: Final = "json"
TRYBY_WYNIKU: Final = (WYNIK_EKRAN, WYNIK_JSON)

WynikOpt = Annotated[
    str,
    typer.Option("--wynik", help="ekran (domyślnie) albo json: koperta JSON na stdout"),
]


# Które polecenia umieją zbudować kopertę (ADR-0024, decyzja 6). Tabela, a **nie** „te, które
# mają flagę": flaga jest przy każdym poleceniu po to, żeby na `kreator --wynik json`
# odpowiedziało nasze zdanie i kod 3, a nie angielskie „No such option" i kod 2 od typera.
# Deklaracja dla każdego polecenia z osobna, bo nowe ma **wybrać**, a nie odziedziczyć
# domyślną — tę samą sztuczkę kompletności niesie `BUDUJE_ASYSTENTA` (ADR-0025).
KOPERTA_OBSLUGIWANA: dict[str, bool] = {
    "pobierz": True,
    "aktualizuj": True,
    "wznow": True,
    "runy": True,
    "raporty": True,
    "sprawdz-nip": True,
    "szukaj-pkd": True,
    # Kreator jest rozmową — maszynowy wołający nie ma się czym w nim posłużyć.
    "kreator": False,
    # Poświadczenia i katalog danych, nie pobieranie. `token zapisz` wczytuje token z ukrytego
    # wejścia, więc koperta opisywałaby czynność, której agent i tak nie wykona.
    "token": False,
    "sprawdz-token": False,
    "wyczysc": False,
    # Następny oczywisty kandydat, zostawiony poza listą wyłącznie dlatego, że nikt o niego
    # nie poprosił — a nie dlatego, że coś stoi na przeszkodzie (ADR-0024, decyzja 6).
    "eksportuj": False,
}


def _tryb_json(polecenie: str, wynik: str, *, tak: bool | None = None) -> bool:
    """Czy pisać kopertę — i trzy odmowy, wszystkie `ConfigError` z kodem 3.

    Wszystkie z tego samego powodu: **zignorowanie flagi jest tym, przez co maszynowy wołający
    kończy na parsowaniu ludzkiego ekranu.** Nieznana wartość, polecenie bez koperty i brak
    `--tak` różnią się przyczyną, nie skutkiem.

    `tak=None` znaczy „to polecenie o nic nie pyta" (`runy`, `szukaj-pkd`) i wtedy trzecia
    odmowa nie obowiązuje — żądanie flagi, która nie ma czego rozstrzygać, byłoby ceremonią.

    Odmowy padają **przed** wejściem w `WynikPolecenia` i to jest zamierzone: skoro nie wiadomo,
    o jakie wyjście prosił wołający, nie ma czego wypisywać. Zostaje ludzka droga — zdanie na
    stderr i kod 3 — czyli ta sama, którą dostaje każdy inny zły argument.
    """
    wartosc = wynik.strip().lower()
    if wartosc not in TRYBY_WYNIKU:
        # `_fail`, a nie `raise`: to wywołanie stoi w nagłówku `with`, czyli **przed**
        # `__enter__`, więc menedżera jeszcze nie ma i nikt by tego wyjątku nie złapał —
        # operator dostawał kod 1 i ślad stosu zamiast zdania i kodu 3. Znalezione testem
        # pokrycia dróg, nie przeglądem: ta gałąź wygląda na obsłużoną.
        _fail(ConfigError(texts.wynik_nieznany(wynik, TRYBY_WYNIKU)))
    if wartosc != WYNIK_JSON:
        return False
    if not KOPERTA_OBSLUGIWANA.get(polecenie, False):
        obslugiwane = [nazwa for nazwa, umie in KOPERTA_OBSLUGIWANA.items() if umie]
        _fail(ConfigError(texts.wynik_bez_koperty(polecenie, obslugiwane)))
    if tak is False:
        _fail(ConfigError(texts.WYNIK_JSON_WYMAGA_TAK))
    return True


def _kod_bez_koperty(exc: BaseException | None) -> int:
    """Kod wyjścia dla drogi ludzkiej — **nie** ten z koperty, i to jest decyzja 3B.

    Kod 4 przy zerze trafień obowiązuje wyłącznie pod `--wynik json`, bo to flaga jest
    deklaracją wołającego. Harmonogram, który dziś traktuje niezerowy kod jako „sprawdź co
    się stało", zacząłby bez tego alarmować w dniu, w którym rejestr zwyczajnie nie miał nic.
    """
    if isinstance(exc, CeidgError):
        return exc.exit_code
    if isinstance(exc, typer.Exit):
        return int(exc.exit_code)
    return 0


def _settings_demo(environment: str | None, prod: bool) -> Settings:
    """Ustawienia trybu demo — bez `.env`, bez keyringu, we własnym katalogu danych.

    Żadnej nowej furtki do poświadczeń tu nie ma i nie potrzeba: `load_settings` **od zawsze**
    przyjmuje `env_file=None`, `use_keyring=False` i `token=`. Do dziś korzystały z tego tylko
    testy. Bez tego demo uruchomione w katalogu repozytorium wczytałoby prawdziwy `CEIDG_TOKEN`
    z `.env` (`DEFAULT_ENV_FILE` jest względne wobec katalogu roboczego), a ten token niesie
    PESEL w payloadzie.

    Osobny katalog danych to **znacznik numer cztery** z ADR-0014 i jedyny, który chroni coś
    poza czytelnością: baza demo nie może dotknąć bazy produkcyjnej, bo to inny plik.
    """
    if prod or (environment or "").strip().lower() == "prod":
        raise ConfigError(
            "Tryb demo nie łączy się z produkcją. Demo odpowiada z syntetycznego rejestru, "
            "więc `--produkcja` nie miałoby czego dotyczyć — a razem z nim łatwo pomylić, "
            "co jest na ekranie."
        )
    # Katalog demo wisi pod tym samym korzeniem, co katalog roboczy narzędzia, więc
    # `CEIDG_DATA_DIR` nadal działa. Zignorowanie tej zmiennej odbierałoby operatorowi
    # jedyny sposób, żeby powiedzieć „pracuj tutaj", i kazało pokazowi pisać do katalogu
    # z prawdziwymi danymi.
    korzen = os.environ.get(ENV_DATA_DIR, "").strip()
    baza = Path(korzen) if korzen else default_data_dir()
    settings = load_settings(
        env_file=None,
        # `environ` zostaje prawdziwe, żeby `ANTHROPIC_API_KEY` dało się podać do przełącznika
        # „asystent na żywo". Tokenu CEIDG to nie wpuszcza: `token=` ma pierwszeństwo przed
        # zmienną środowiskową i przed keyringiem, więc łańcuch nigdy po niego nie sięga.
        use_keyring=False,
        token=TOKEN_DEMO,
        environment="test",
        data_dir=baza / "demo",
    )
    setup_logging(settings.log_dir)
    return settings


def _demo_deps(
    settings: Settings,
    events: Events | None = None,
    *,
    asystent: Asystent = "nieproszony",
) -> tuple[Deps, Demo]:
    """`Deps` mówiące do syntetycznego rejestru zamiast do CEIDG.

    Podstawienie zachodzi **tutaj**, w korzeniu kompozycji, a nie w `build_deps`: gałąź trybu
    demo w środku `build_deps` słusznie zapala na czerwono
    `tests/resilience/test_egress_allowlist.py`, który pilnuje, że produkcja buduje klienta
    przez `build_http_client(transport=None)`. Reguła granic 11 zostaje nietknięta, bo klient
    i tak powstaje w `httpclient`, tylko z transportem, który nie otwiera gniazda.
    """
    profil = load_profile(settings.environment, settings.profile_path)
    host = urlsplit(profil.base_url).hostname or ""
    demo = zbuduj_demo(zegar=SystemClock(), host=host)
    # `asystent` przechodzi dalej, bo inaczej pokaz rozjechałby się z produkcją dokładnie
    # w tej jednej rzeczy, o którą chodzi w tym kroku.
    deps = build_deps(
        settings,
        events=events,
        clock=demo.zegar,
        http=demo.klient,
        asystent=asystent,
    )
    deps.demo = True
    return deps, demo


def _settings(environment: str | None, prod: bool, yes: bool) -> Settings:
    if environment and environment.strip().lower() == "prod" and not prod:
        if yes:
            raise ConfigError("Środowisko produkcyjne wymaga jawnej flagi --produkcja.")
        prod = _confirm(texts.CONFIRM_PROD)
    settings = load_settings(environment=environment, prod_consent=prod)
    setup_logging(settings.log_dir)
    for warning in settings.warnings:
        view.warning(warning)
    return settings


def _banner(settings: Settings, *, demo: bool = False, bez_asystenta: bool = False) -> None:
    """Pierwszy ekran — ta sama treść co w kreatorze (uzupelnienie-01.md §A)."""
    view.block(
        texts.first_screen(
            settings,
            now=datetime.now(tz=UTC),
            version=__version__,
            demo=demo,
            bez_asystenta=bez_asystenta,
        )
    )


def _prompter(tak: bool, overrides: dict[str, str] | None = None) -> Prompter:
    # `stdout`, mimo że ekrany przeniosły się na stderr (ADR-0024). To nie jest przeoczenie:
    # pytanie rysuje `questionary` albo wbudowany `input`, a obie te drogi piszą na stdout
    # i nie przechodzą przez konsolę programu. Gdyby to zamienić na stderr, `pobierz > plik`
    # przy terminalu na stderr uznałby się za interaktywny i zadał pytanie, którego treść
    # wylądowałaby w pliku — operator czekałby przed pustym ekranem.
    interactive = interactive_available(
        stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=tak
    )
    base = make_prompter(yes=tak, interactive=interactive)
    return OverridePrompter(base, overrides) if overrides else base


def _sanitised(out: Path | None, deps: Deps) -> Path | None:
    """§B uzupelnienie-01.md: pliki wynikowe tylko w katalogu wyniki/, nazwa oczyszczona.

    Prefiks `DEMO_` doklejamy także tutaj. Nazwa podana przez operatora omijała znacznik
    numer trzy z ADR-0014, a to jest **ten** plik, który po pokazie najłatwiej wysłać dalej:
    ktoś go świadomie nazwał, więc traktuje go jak swój."""
    if out is None:
        return None
    prefiks = "DEMO_" if deps.demo else ""
    return deps.settings.output_dir / safe_filename(prefiks + out.stem, ".xlsx")


OpisOpt = Annotated[
    str | None,
    typer.Option("--opis", help="opisz zapytanie zdaniem; wymaga klucza asystenta"),
]
BezAsystentaOpt = Annotated[
    bool,
    typer.Option("--bez-asystenta", help="Nie buduj asystenta i nie pytaj o opis zdaniem"),
]


def _criteria_from_options(
    pola: Mapping[str, Sequence[str]] | None = None,
    *,
    od: str | None = None,
    do: str | None = None,
    szczegoly: bool = False,
    maks: int | None = None,
) -> Criteria:
    """Kryteria z flag wiersza poleceń — jedyne wejście CLI od czasu wycofania pliku YAML.

    Pola listowe przychodzą **słownikiem**, a nie jako kolejne argumenty pozycyjne. Powód jest
    mechaniczny: przy dziewięciu polach wywołania testowe wyglądały jak `plik, [], [], [], [],
    [], [], [], [], [], None, None, False, None`, więc przestawienie dwóch sąsiadów było
    niewidoczne dla czytającego i dla mypy — obie listy mają ten sam typ. Przy siedemnastu
    polach (2026-09-10) byłoby to już tylko kwestią czasu. Nazwy w słowniku są nazwami pól
    `Criteria`, więc literówka wywraca się na `extra="forbid"`, a nie po cichu gubi filtr.

    `szczegoly` przestało być trójstanowe razem z plikiem (ADR-0022). Trzeci stan istniał po to,
    żeby `--lista` umiało **wyłączyć** `szczegoly: true` zapisane w pliku — bez pliku nie ma
    wartości spod spodu, więc „nie podano" i „podano nie" prowadzą do tego samego wyniku, a
    `bool | None` byłoby rozróżnieniem bez odbiorcy. Naprawa A6 z audytu 2026-09-08 zniknęła
    razem ze swoim przedmiotem; ślad po niej stoi w `tests/test_flagi_trojstanowe.py`.
    """
    try:
        return Criteria.model_validate(
            {
                **{nazwa: tuple(wartosci) for nazwa, wartosci in (pola or {}).items()},
                # Daty idą do walidatora **napisem**, zamiast przez `date.fromisoformat` tutaj.
                # Ta funkcja rzuca „Invalid isoformat string: '2020-13-01'" — po angielsku
                # i o klasie, której operator nie zna; pydantic rozpoznaje ten sam błąd jako
                # `date_from_datetime_parsing`, a `bledy_po_polsku` ma dla niego zdanie
                # „to nie jest data w postaci RRRR-MM-DD (na przykład 2020-01-31)".
                "data_od": od or None,
                "data_do": do or None,
                "szczegoly": szczegoly,
                "max_rekordow": maks,
            }
        )
    except ValidationError as exc:
        # Ta sama funkcja, co w kreatorze i u asystenta. Do 2026-09-10 stał tu surowy zrzut
        # pydantica — z `[type=value_error, input_value=…]` i odnośnikiem do errors.pydantic.dev
        # — czyli jedyne z czterech wejść do `Criteria`, które nadal mówiło do operatora
        # językiem biblioteki. Przy ośmiu nowych flagach adresowych to jest ta ścieżka, którą
        # literówka w kodzie pocztowym pokonuje najczęściej.
        raise ConfigError(f"Niepoprawne kryteria:\n{bledy_po_polsku(exc)}") from exc


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    bez_asystenta: BezAsystentaOpt = False,
) -> None:
    """Bez polecenia uruchamia kreator; poza terminalem pokazuje pomoc."""
    if ctx.invoked_subcommand is not None:
        return
    if not interactive_available(
        stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=False
    ):
        # `typer` w trybie `rich` drukuje pomoc sam i zwraca pusty napis; drukujemy tylko
        # wtedy, gdy jednak coś zwrócił, żeby nie dokładać pustego wiersza ani nie zgubić
        # pomocy, gdyby formatowanie kiedyś wróciło do zwykłego `click`.
        help_text = ctx.get_help()
        if help_text:
            view.message(help_text)
        return
    # Flaga przechodzi razem z dwiema pozostałymi: `python -m ceidg_tool` bez podpolecenia jest
    # udokumentowanym wejściem do kreatora (README, CLAUDE.md), a kreator jest jedyną ścieżką
    # budującą asystenta **zawsze** — czyli tą, na której wyłącznik jest najbardziej potrzebny.
    kreator(srodowisko=srodowisko, produkcja=produkcja, bez_asystenta=bez_asystenta)


@app.command()
def kreator(
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    demo: DemoOpt = False,
    bez_asystenta: BezAsystentaOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Prowadzi krok po kroku: pierwszy ekran, menu, kryteria, koszty, wynik."""
    _tryb_json("kreator", wynik)
    try:
        settings = (
            _settings_demo(srodowisko, produkcja)
            if demo
            else _settings(srodowisko, produkcja, False)
        )
        if not interactive_available(
            stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=False
        ):
            raise ConfigError(
                "Kreator wymaga terminala. W harmonogramie użyj `pobierz` z flagami "
                "kryteriów i `--tak` — kreator wypisuje gotowe polecenie po zebraniu kryteriów."
            )
        events = _events()
        # Kreator pyta o opis zdaniem w menu, więc asystent jest tu na ścieżce zawsze —
        # chyba że operator go wyłączył, i wtedy pierwszy ekran ma to pokazać.
        tryb_asystenta: Asystent = "wylaczony" if bez_asystenta else "buduj"
        deps = (
            _demo_deps(settings, events, asystent=tryb_asystenta)[0]
            if demo
            else build_deps(settings, events=events, asystent=tryb_asystenta)
        )
        for warning in deps.warnings:
            view.warning(warning)
        try:
            code = wizard.run_wizard(deps, _prompter(False), view, version=__version__)
        finally:
            events.close()
            deps.store.close()
        raise typer.Exit(code=code)
    except CeidgError as exc:
        _fail(exc)


@app.command("szukaj-pkd")
def szukaj_pkd(
    zapytanie: Annotated[
        str, typer.Argument(help="kod (np. 6210B albo 62.10.B) albo fragment nazwy (np. fryzjer)")
    ],
    wszystkie: Annotated[
        bool, typer.Option("--wszystkie", help=f"bez limitu {DOMYSLNY_LIMIT} wierszy")
    ] = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Szuka kodów PKD w słowniku dołączonym do programu. Zero zapytań do rejestru, bez tokenu."""
    with WynikPolecenia("szukaj-pkd", json=_tryb_json("szukaj-pkd", wynik)) as koperta:
        # Bez `_settings()`, bez `build_deps`, bez bazy: dwa pliki YAML i czysta funkcja.
        # `_settings` rozwiązuje token i potrafi się wywalić — a szukanie w pliku, który leży
        # w pakiecie, nie ma prawa wymagać poświadczenia. To jest też pierwsze polecenie, które
        # ktoś uruchamia, konfigurując narzędzie (ADR-0026, decyzja 5).
        slownik = load_pkd()
        try:
            tablica: TablicaPkd | None = load_pkd_map()
        except CeidgError:
            # Brak tablicy przejścia wyłącza **kolumnę**, a nie polecenie — tak samo jak
            # `deps.pkd_map` w `build_deps`. Kody 2025 są nadal do znalezienia.
            tablica = None
        znalezione = szukaj(zapytanie, slownik, tablica, limit=limit_wierszy(wszystkie))
        view.block(texts.pkd_search(znalezione))
        # Zero trafień to `brak_trafien`, czyli kod 4 pod flagą i 0 bez niej. Dla tego
        # polecenia pusty wynik bywa **poprawną odpowiedzią** („takiej branży nie ma
        # w klasyfikacji"), więc rozróżnia go status, a nie sama obecność koperty.
        koperta.ustaw(
            Wynik(
                polecenie="szukaj-pkd",
                status="ok" if znalezione.trafienia else "brak_trafien",
                dodatki=jako_dane(znalezione),
            )
        )


def limit_wierszy(wszystkie: bool) -> int | None:
    """`None` znaczy „bez sufitu". Osobna funkcja, żeby dało się to sprawdzić bez CLI."""
    return None if wszystkie else DOMYSLNY_LIMIT


@app.command("sprawdz-nip")
def sprawdz_nip(
    nip: Annotated[str, typer.Argument(help="NIP firmy, 10 cyfr")],
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
    demo: DemoOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Sprawdza jedną firmę po NIP: suma kontrolna lokalnie, potem dwa zapytania. Pod
    `--wynik json` koperta niesie dane jednej osoby — imię, nazwisko, adres, telefon."""
    # Docstring zostaje **jednowierszowy**, bo typer drukuje go operatorowi jako `--help` —
    # historia zmiany na tym ekranie to opis błędu, którego operator nie umie już wywołać.
    # Sprawdzone: wielowierszowa wersja wyszła na ekran w całości, z gwiazdkami markdownu.
    #
    # Tryb pokazu doszedł tu 2026-09-10 i był potrzebny bardziej niż gdziekolwiek indziej: to
    # jedyne polecenie, którego wynikiem jest karta jednej osoby — imię, nazwisko, adres,
    # telefon — więc pokazanie tej drogi wymagało produkcji, czyli czyichś danych osobowych na
    # ekranie. Lukę ukrywał kreator, który miał tę samą drogę w pokazie od początku, więc droga
    # *była* pokryta, tylko z jednego z dwóch wejść.
    with WynikPolecenia(
        "sprawdz-nip", json=_tryb_json("sprawdz-nip", wynik, tak=tak), demo=demo
    ) as koperta:
        settings = (
            _settings_demo(srodowisko, produkcja) if demo else _settings(srodowisko, produkcja, tak)
        )
        _banner(settings, demo=demo)
        events = _events(quiet=True)
        koperta.licz_zadania(events)
        deps = _demo_deps(settings, events)[0] if demo else build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            lookup = lookup_nip(nip, deps)
            view.block(texts.firm_card(lookup.record, nip=lookup.nip))
            pliki: tuple[Path, ...] = ()
            if lookup.record is not None and out is not None:
                pliki = _export_after(deps, lookup.run_id, out, None, "xlsx").paths
            koperta.ustaw(
                Wynik(
                    polecenie="sprawdz-nip",
                    # Brak trafienia to `brak_trafien`, nie błąd: NIP o poprawnej sumie
                    # kontrolnej, którego rejestr nie zna, jest **odpowiedzią**. Literówka
                    # kończy się wcześniej, na `ConfigError` z `lookup_nip`.
                    status="ok" if lookup.record is not None else "brak_trafien",
                    demo=deps.demo,
                    srodowisko=settings.environment,
                    run_ids=(lookup.run_id,),
                    rekordy=1 if lookup.record is not None else 0,
                    pliki=pliki,
                    # Sam wiersz `Firmy`, bez arkuszy powiązanych: to jest ta sama zawartość,
                    # którą operator widzi na karcie. Pole niesie dane jednej osoby — imię,
                    # nazwisko, adres, telefon — i przechowanie koperty należy do wołającego
                    # (ADR-0024, tabela ryzyk); narzędzie nie zapisuje jej na dysk.
                    #
                    # Przez `wiersz_do_json`, bo wiersz niesie `date` — a `sprawdz-nip` to
                    # jedyne polecenie, którego **produktem** jest rekord, więc bez konwersji
                    # wywracało się na każdym trafieniu (przegląd kodu 2026-09-24).
                    dodatki={
                        "firma": (
                            wiersz_do_json(lookup.record.firmy)
                            if lookup.record is not None
                            else None
                        )
                    },
                )
            )
        finally:
            events.close()
            deps.store.close()


@app.command()
def pobierz(
    wojewodztwo: Annotated[
        list[str] | None, typer.Option("--wojewodztwo", "-w", help="np. podlaskie")
    ] = None,
    miasto: Annotated[list[str] | None, typer.Option("--miasto", "-m")] = None,
    powiat: Annotated[list[str] | None, typer.Option("--powiat")] = None,
    gmina: Annotated[list[str] | None, typer.Option("--gmina")] = None,
    ulica: Annotated[
        list[str] | None, typer.Option("--ulica", help="rejestr zapisuje ją różnie: „ul. Polna”")
    ] = None,
    budynek: Annotated[
        list[str] | None, typer.Option("--budynek", help="numer nieruchomości, np. 12A")
    ] = None,
    lokal: Annotated[list[str] | None, typer.Option("--lokal", help="numer lokalu")] = None,
    kod: Annotated[list[str] | None, typer.Option("--kod", help="kod pocztowy, np. 15-333")] = None,
    nazwa: Annotated[list[str] | None, typer.Option("--nazwa", "-n", help="fragment nazwy")] = None,
    imie: Annotated[
        list[str] | None, typer.Option("--imie", help="imię przedsiębiorcy, nie nazwa firmy")
    ] = None,
    nazwisko: Annotated[
        list[str] | None, typer.Option("--nazwisko", help="nazwisko przedsiębiorcy")
    ] = None,
    nip: Annotated[list[str] | None, typer.Option("--nip")] = None,
    regon: Annotated[list[str] | None, typer.Option("--regon")] = None,
    nip_sc: Annotated[
        list[str] | None, typer.Option("--nip-sc", help="NIP spółki cywilnej, nie przedsiębiorcy")
    ] = None,
    regon_sc: Annotated[
        list[str] | None, typer.Option("--regon-sc", help="REGON spółki cywilnej")
    ] = None,
    pkd: Annotated[list[str] | None, typer.Option("--pkd", help="np. 62.10.B")] = None,
    status: Annotated[
        list[str] | None, typer.Option("--status", help="AKTYWNY, ZAWIESZONY, …")
    ] = None,
    od: Annotated[str | None, typer.Option("--od", help="data rozpoczęcia od (YYYY-MM-DD)")] = None,
    do: Annotated[str | None, typer.Option("--do", help="data rozpoczęcia do (YYYY-MM-DD)")] = None,
    szczegoly: Annotated[
        bool, typer.Option("--szczegoly/--lista", help="pełne szczegóły firm")
    ] = False,
    maks: Annotated[int | None, typer.Option("--maks", help="maksymalna liczba rekordów")] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", "-o", help="nazwa pliku .xlsx (zawsze w katalogu wyniki/)"),
    ] = None,
    cel: Annotated[
        str | None, typer.Option("--cel", help="cel pobrania (trafia do Metadane)")
    ] = None,
    zrodlo: Annotated[str, typer.Option("--zrodlo", help="auto | api | raport")] = "auto",
    formaty: Annotated[str, typer.Option("--format", help="xlsx,csv,jsonl")] = "xlsx",
    partie: Annotated[
        bool,
        typer.Option(
            "--partie", help="Zgoda na podział na partie po dacie, gdy trafień jest za dużo"
        ),
    ] = False,
    pkd_2007: Annotated[
        bool | None,
        typer.Option(
            "--pkd-2007/--bez-pkd-2007",
            help="Szukać też po odpowiednikach z PKD 2007 (okres przejściowy do 31.12.2026)",
        ),
    ] = None,
    opis: OpisOpt = None,
    bez_asystenta: BezAsystentaOpt = False,
    force: ForceOpt = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
    demo: DemoOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Pobiera firmy według kryteriów i zapisuje skoroszyt Excel."""
    with WynikPolecenia(
        "pobierz", json=_tryb_json("pobierz", wynik, tak=tak), demo=demo
    ) as koperta:
        # Obie pary flag nie do spełnienia — odrzucane **przed** `_settings()`, bo obie są
        # rozstrzygnięte już na poziomie wiersza poleceń. Po `_settings` brakujący token dałby
        # **inne** zdanie na to samo wywołanie, czyli komunikat o drugiej w kolejności przyczynie.
        if opis is not None and bez_asystenta:
            raise ConfigError(texts.OPIS_BEZ_ASYSTENTA)
        # `--opis --tak` stało do 2026-09-23 **pod** `build_deps` — czyli program budował
        # asystenta (955 ms, import SDK, klient z poświadczeniem do api.anthropic.com,
        # `_wycisz_sdk()` w `os.environ`) i dopiero potem odmawiał. Przebieg B5 zamknął tę samą
        # pomyłkę o warstwę niżej: nie wydawaj na dowiedzenie się rzeczy wiadomej od razu.
        # Trafiało to w najgorszą możliwą klasę wywołań — harmonogram z kluczem w `.env`,
        # chodzący bez nadzoru. Znalezisko z przeglądu kodu części A.
        if opis is not None and tak:
            raise ConfigError(texts.ASSISTANT_NEEDS_A_HUMAN)
        settings = (
            _settings_demo(srodowisko, produkcja) if demo else _settings(srodowisko, produkcja, tak)
        )
        _banner(settings, demo=demo, bez_asystenta=bez_asystenta)
        criteria = _criteria_from_options(
            {
                "wojewodztwo": wojewodztwo or [],
                "powiat": powiat or [],
                "gmina": gmina or [],
                "miasto": miasto or [],
                "ulica": ulica or [],
                "budynek": budynek or [],
                "lokal": lokal or [],
                "kod": kod or [],
                "nazwa": nazwa or [],
                "imie": imie or [],
                "nazwisko": nazwisko or [],
                "nip": nip or [],
                "regon": regon or [],
                "nip_sc": nip_sc or [],
                "regon_sc": regon_sc or [],
                "pkd": pkd or [],
                "status": status or [],
            },
            od=od,
            do=do,
            szczegoly=szczegoly,
            maks=maks,
        )
        if zrodlo not in KNOWN_SOURCES:
            raise ConfigError(f"Nieznane źródło {zrodlo!r}. Dozwolone: auto, api, raport.")
        _parse_formats(formaty)  # walidacja przed pobraniem, nie po nim
        events = _events()
        koperta.licz_zadania(events)
        # Asystent tylko dla `--opis`: reszta ścieżek `pobierz` nie ma jak go użyć, a jego
        # budowa to import SDK, słownik, prompt i klient HTTP do drugiego hosta (ADR-0025).
        # `if opis`, nie `opis is not None`: `--opis ""` przechodzi drugie odrzucenie wyżej,
        # a `if opis:` niżej i tak go nie użyje — czyli budowa bez odbiorcy.
        #
        # `--bez-asystenta` daje `wylaczony` **także bez `--opis`**: flaga jest decyzją
        # operatora i pierwszy ekran ma ją pokazać niezależnie od tego, czy na tej drodze
        # asystent byłby w ogóle potrzebny.
        tryb_asystenta: Asystent = (
            "wylaczony" if bez_asystenta else ("buduj" if opis else "nieproszony")
        )
        deps = (
            _demo_deps(settings, events, asystent=tryb_asystenta)[0]
            if demo
            else build_deps(settings, events=events, asystent=tryb_asystenta)
        )
        for warning in deps.warnings:
            view.warning(warning)
        prompter = _prompter(tak, {"podzial": "partie"} if partie else None)
        if opis:
            # Ta sama implementacja, co w kreatorze — oba wejścia nie mogą się rozjechać.
            # W trybie `--tak` pytanie o zatwierdzenie ma `safe_default=False`, więc kończy się
            # kodem 3: harmonogram nie ma prawa działać na interpretacji, której nikt nie
            # przeczytał. Jego drogą jest polecenie z flagami, które kreator wypisuje
            # z zatwierdzonej interpretacji (ADR-0022).
            z_opisu = flow.collect_from_description(deps, prompter, view, opis=opis)
            if z_opisu is None:
                # `None` znaczy „operator wybrał pytania po kolei", a nie „asystenta nie ma".
                raise ConfigError(texts.OPIS_PORZUCONY)
            criteria = z_opisu
        if criteria.is_empty():
            raise ConfigError(texts.EMPTY_CRITERIA)
        view.block(texts.criteria_block(criteria))
        try:
            decision, plan = flow.prepare_fetch(
                criteria,
                deps,
                prompter,
                view,
                source=zrodlo,
                # Przy `--tak` pytanie i tak rozwiązałoby się odpowiedzią domyślną, więc drugie
                # zapytanie o `count` poszłoby na wiedzę, którą już mamy — ta sama pomyłka co
                # w przebiegu B5. Harmonogram zachowuje dzisiejsze zachowanie i dostaje o tym
                # jedno zdanie; szerzej wybiera się jawnie flagą.
                #
                # Warunek `and not criteria.pkd_2007` stał tu do 2026-09-10 po to, żeby wybór
                # zapisany w pliku zapytania wygrywał z domyślnym zawężeniem trybu `--tak`.
                # Po wycofaniu pliku (ADR-0022) `criteria.pkd_2007` nie ma tu skąd przyjść —
                # flagi listy starych kodów nie ma, a asystent tego pola nie wypełnia — więc
                # warunek był od tej chwili zawsze prawdziwy i mówił o drodze, której nie ma.
                # Rocznik w harmonogramie wybiera się jawnie flagą `--pkd-2007`.
                rocznik_2007=(pkd_2007 if pkd_2007 is not None else (False if tak else None)),
            )
            if decision in ("wyjdz", "anuluj"):
                # Dwa różne fakty pod jednym `return`, i koperta ma je rozróżniać.
                # `plan.count == 0` znaczy „zapytanie poszło i nie pasowało nic" — pod `--tak`
                # pytanie o poszerzenie rozstrzyga się bezpieczną domyślną „wyjdź" (ADR-0017),
                # więc harmonogram kończy właśnie tutaj. Operator, który przeczytał tabelę
                # kosztów i zrezygnował, to `przerwano`. Czytane z `plan`, nie liczone na nowo.
                koperta.ustaw(
                    _wynik_pobrania(
                        deps,
                        settings,
                        plan.criteria,
                        status="brak_trafien" if plan.count == 0 else "przerwano",
                    )
                )
                return
            if decision == "popraw":
                view.message(texts.FIX_CRITERIA)
                koperta.ustaw(_wynik_pobrania(deps, settings, plan.criteria, status="przerwano"))
                return
            result = flow.execute(decision, plan, deps, view, force_lock=force)
            podsumowanie = flow.export_and_report(
                result.run_ids,
                deps,
                view,
                out=_sanitised(out, deps),
                name_from=plan.criteria if len(result.run_ids) > 1 else None,
                cel=cel,
                formats=_parse_formats(formaty),
                notes=result.notes,
            )
            koperta.ustaw(
                _wynik_pobrania(
                    deps,
                    settings,
                    plan.criteria,
                    status="ok",
                    run_ids=result.run_ids,
                    rekordy=podsumowanie.records,
                    pliki=podsumowanie.paths,
                    # Uwagi przebiegu jako proza: zamknięte zbiory kodów istnieją dla powodu
                    # braku raportu i ograniczeń zapytania, ale nie dla tych zdań (ADR-0024,
                    # poddecyzja do 1). Kod dochodzi wtedy, gdy ktoś go potrzebuje.
                    uwagi=result.notes,
                )
            )
        finally:
            events.close()
            deps.store.close()


def _wynik_pobrania(
    deps: Deps,
    settings: Settings,
    criteria: Criteria,
    *,
    status: Status,
    run_ids: tuple[str, ...] = (),
    rekordy: int | None = None,
    pliki: tuple[Path, ...] = (),
    uwagi: Sequence[str] = (),
) -> Wynik:
    """Koperta `pobierz` — cztery drogi wyjścia, jeden zestaw pól.

    Osobna funkcja, bo te same pola wypełniają się w czterech miejscach jednego polecenia,
    a czwarta kopia to pierwsza, która zapomni o `demo`. Znacznik pokazu jest tu obowiązkowy
    łącznie z pozostałymi pięcioma (ADR-0014), a jest to jedyny kanał, którego nikt za
    agentem nie ogląda.
    """
    return Wynik(
        polecenie="pobierz",
        status=status,
        demo=deps.demo,
        srodowisko=settings.environment,
        kryteria=criteria,
        run_ids=run_ids,
        rekordy=rekordy,
        pliki=pliki,
        uwagi=tuple(Uwaga(tekst) for tekst in uwagi),
    )


KNOWN_FORMATS = frozenset({"xlsx", "csv", "jsonl"})
KNOWN_SOURCES = frozenset({"auto", "api", "raport"})


def _parse_formats(formaty: str) -> tuple[str, ...]:
    formats = tuple(f.strip().lower() for f in formaty.split(",") if f.strip())
    unknown = sorted(set(formats) - KNOWN_FORMATS)
    if unknown or not formats:
        raise ConfigError(
            f"Nieznany format: {', '.join(unknown) or '(pusty)'}. Dozwolone: xlsx, csv, jsonl."
        )
    return formats


def _export_after(
    deps: Deps, run_id: str, out: Path | None, cel: str | None, formaty: str
) -> ExportSummary:
    """Oddaje podsumowanie, a nie `None`: koperta czyta z niego `pliki` i `rekordy`.

    Ekran i koperta mają być dwoma renderowaniami **tych samych** wartości (ADR-0024,
    decyzja 1B), więc drugie źródło tych liczb nie ma prawa powstać.
    """
    return flow.export_and_report(
        (run_id,),
        deps,
        view,
        out=_sanitised(out, deps),
        cel=cel,
        formats=_parse_formats(formaty),
    )


@app.command()
def wznow(
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="domyślnie ostatni przerwany")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    cel: Annotated[str | None, typer.Option("--cel")] = None,
    force: ForceOpt = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
    demo: DemoOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Wznawia przerwane pobieranie z checkpointu i eksportuje wynik."""
    with WynikPolecenia("wznow", json=_tryb_json("wznow", wynik, tak=tak), demo=demo) as koperta:
        settings = (
            _settings_demo(srodowisko, produkcja) if demo else _settings(srodowisko, produkcja, tak)
        )
        _banner(settings, demo=demo)
        events = _events()
        koperta.licz_zadania(events)
        deps = _demo_deps(settings, events)[0] if demo else build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            if run_id is None:
                candidates = list_resumable(deps)
                if not candidates:
                    view.message(texts.NO_RESUMABLE)
                    # Nie ma czego wznawiać — to jest zakończenie poprawne, więc pod flagą
                    # kod 4, a bez niej zero (decyzja 3B). Harmonogram odróżnia dzięki temu
                    # „wznowiłem" od „nie było czego", a dziś oba były zerem.
                    koperta.ustaw(
                        Wynik(
                            polecenie="wznow",
                            status="nic_do_zrobienia",
                            demo=deps.demo,
                            srodowisko=settings.environment,
                        )
                    )
                    return
                run_id = candidates[0].run_id
            run = deps.store.get_run(run_id)
            if run.kind != "firmy":
                raise ConfigError(
                    f"Run {run_id} to pobranie typu {run.kind!r}, nie da się go wznowić. "
                    "Powtórz `pobierz --zrodlo raport` albo `aktualizuj`."
                )
            try:
                criteria = Criteria.model_validate_json(run.criteria_json)
            except ValidationError as exc:
                raise ConfigError(f"Run {run_id} ma nieczytelne kryteria: {exc}") from exc
            result = run_fetch(criteria, deps, resume_run_id=run_id, force_lock=force)
            podsumowanie = _export_after(deps, result.run_id, out, cel, "xlsx")
            koperta.ustaw(
                Wynik(
                    polecenie="wznow",
                    status="ok",
                    demo=deps.demo,
                    srodowisko=settings.environment,
                    kryteria=criteria,
                    run_ids=(result.run_id,),
                    rekordy=podsumowanie.records,
                    pliki=podsumowanie.paths,
                )
            )
        finally:
            events.close()
            deps.store.close()


@app.command()
def eksportuj(
    run_id: Annotated[
        str | None, typer.Option("--run-id", help="domyślnie ostatni zakończony")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    cel: Annotated[str | None, typer.Option("--cel")] = None,
    formaty: Annotated[str, typer.Option("--format", help="xlsx,csv,jsonl")] = "xlsx",
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    demo: DemoOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Ponowny eksport z bazy — bez żadnego żądania do API."""
    _tryb_json("eksportuj", wynik)
    try:
        settings = (
            _settings_demo(srodowisko, produkcja)
            if demo
            else _settings(srodowisko, produkcja, True)
        )
        # Pasek i komunikaty także tutaj. Bez `events` zależności dostawały `NullEvents`, więc
        # z `eksportuj` znikał nie tylko pasek, ale i zdanie „Zapisuję skoroszyt…" — a zapis
        # pełnego województwa to przy zmierzonych 453 firmach na sekundę ponad dziesięć minut
        # zupełnej ciszy, w poleceniu uruchamianym po każdym przerwanym pobraniu. Komentarz
        # nad tym zdaniem w `pipeline` obiecywał, że pada ono „nawet tam, gdzie paska nie
        # widać" — i była to jedyna droga, na której nie padało (audyt 2026-09-07).
        events = _events()
        deps = build_deps(settings, events=events, online=False)
        # Bez tego ponowny eksport gubi znaczniki 2 i 3: plik nie dostaje prefiksu `DEMO_`,
        # a arkusz `Metadane` zaczyna się od `kryteria`, więc druga kopia skoroszytu z pokazu
        # jest nie do odróżnienia od produkcyjnej. ADR-0014 wymaga pięciu znaczników łącznie,
        # a `eksportuj` istnieje właśnie po to, żeby zrobić kolejną kopię pliku.
        deps.demo = demo
        for warning in deps.warnings:
            view.warning(warning)
        try:
            if run_id is None:
                # Pomoc flagi obiecuje „ostatni zakończony", a nie „ostatni". Run w stanie
                # `w_toku` albo `przerwany` ma komplet kolumn i połowę wierszy, więc wybrany
                # po cichu eksportował się jak pełny (audyt 2026-09-08, A7). Filtr idzie do
                # zapytania: odsianie po `LIMIT 20` gubiłoby starszy zakończony run.
                zakonczone = deps.store.list_runs(statuses=("zakonczony",))
                if not zakonczone:
                    wszystkie = deps.store.count_runs()
                    view.message(
                        texts.NO_RUNS if not wszystkie else texts.no_finished_run(wszystkie)
                    )
                    return
                run_id = zakonczone[0].run_id
            _export_after(deps, run_id, out, cel, formaty)
        finally:
            events.close()
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


@app.command()
def runy(
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    demo: DemoOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Lista pobrań zapisanych w bazie."""
    # `tak` nie przechodzi, bo `runy` o nic nie pyta: żądanie flagi, która nie ma czego
    # rozstrzygnąć, byłoby ceremonią (ADR-0024, decyzja 6).
    with WynikPolecenia("runy", json=_tryb_json("runy", wynik), demo=demo) as koperta:
        settings = (
            _settings_demo(srodowisko, produkcja)
            if demo
            else _settings(srodowisko, produkcja, True)
        )
        deps = build_deps(settings, online=False)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            rows: list[tuple[str, ...]] = []
            dane: list[dict[str, object]] = []
            for run in deps.store.list_runs():
                try:
                    desc = Criteria.model_validate_json(run.criteria_json).describe()
                except ValueError:
                    desc = run.criteria_json[:60]
                rows.append(
                    (
                        run.run_id,
                        run.status,
                        run.mode,
                        str(run.records_seen),
                        str(run.count_api or ""),
                        run.created_utc,
                        desc[:70],
                    )
                )
                # Ekran obcina opis do siedemdziesięciu znaków, bo tabela ma szerokość;
                # koperta nie ma szerokości, więc nie obcina. To nie jest rozjazd, tylko
                # różnica między renderowaniem a wartością.
                dane.append(
                    {
                        "run_id": run.run_id,
                        "status": run.status,
                        "tryb": run.mode,
                        "rodzaj": run.kind,
                        "rekordy": run.records_seen,
                        "count_api": run.count_api,
                        "utworzono": run.created_utc,
                        "opis": desc,
                    }
                )
            view.block(texts.runs_table(rows))
            koperta.ustaw(
                Wynik(
                    polecenie="runy",
                    # Pusta baza to `nic_do_zrobienia`, a nie `brak_trafien`: nikt o nic nie
                    # pytał rejestru, po prostu nie ma jeszcze żadnego pobrania.
                    status="ok" if dane else "nic_do_zrobienia",
                    demo=demo,
                    srodowisko=settings.environment,
                    dodatki={"runy": dane},
                )
            )
        finally:
            deps.store.close()


@app.command()
def raporty(
    pobierz_id: Annotated[
        str | None, typer.Option("--pobierz", help="id raportu do pobrania")
    ] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Lista gotowych raportów CEIDG; opcjonalnie pobranie jednego (ZIP)."""
    with WynikPolecenia("raporty", json=_tryb_json("raporty", wynik, tak=tak)) as koperta:
        settings = _settings(srodowisko, produkcja, tak)
        _banner(settings)
        # Pasek tylko przy pobieraniu: przy samej liście nie ma czego pokazywać, a przy
        # 21 MB archiwum cisza jest defektem (CLAUDE.md).
        events = _events(quiet=pobierz_id is None)
        koperta.licz_zadania(events)
        deps = build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            client = deps.client
            assert client is not None
            reports = client.list_reports()
            # Bez `url`: link pobiera się poleceniem `raporty --pobierz <id>`, a wypisanie go
            # zapraszałoby do sięgnięcia po archiwum z pominięciem bramki wyjścia (reguła 11).
            spis: list[dict[str, object]] = [
                {"id": r.id, "nazwa": r.nazwa, "format": r.format, "utworzono": r.utworzono}
                for r in reports
            ]
            if pobierz_id:
                match = [r for r in reports if r.id == pobierz_id]
                if not match:
                    raise ConfigError(f"Nie ma raportu o id {pobierz_id}.")
                dest = (
                    settings.data_dir
                    / "raporty"
                    / safe_filename(out.stem if out is not None else match[0].nazwa, ".zip")
                )
                client.download_report(match[0], dest, progress=deps.events.on_download)
                # Pasek gaśnie, zanim padnie zdanie o zapisie: żywy `rich` nadpisuje wszystko,
                # co wypisze się po nim (faza 3e). `close()` w `finally` powtórzy się nieszkodliwie.
                events.close()
                view.message(texts.report_saved(dest))
                koperta.ustaw(
                    Wynik(
                        polecenie="raporty",
                        status="ok",
                        srodowisko=settings.environment,
                        pliki=(dest,),
                        dodatki={"raporty": [w for w in spis if w["id"] == pobierz_id]},
                    )
                )
                return
            view.block(texts.reports_table(reports))
            koperta.ustaw(
                Wynik(
                    polecenie="raporty",
                    status="ok" if spis else "nic_do_zrobienia",
                    srodowisko=settings.environment,
                    dodatki={"raporty": spis},
                )
            )
        finally:
            events.close()
            deps.store.close()


@app.command()
def aktualizuj(
    od: Annotated[str | None, typer.Option("--od", help="początek zakresu zmian (ISO)")] = None,
    do: Annotated[str | None, typer.Option("--do", help="koniec zakresu zmian (ISO)")] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o")] = None,
    force: ForceOpt = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
    demo: DemoOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Pobiera zmiany od ostatniego uruchomienia (`/zmiana`) i odświeża cache szczegółów."""
    with WynikPolecenia(
        "aktualizuj", json=_tryb_json("aktualizuj", wynik, tak=tak), demo=demo
    ) as koperta:
        settings = (
            _settings_demo(srodowisko, produkcja) if demo else _settings(srodowisko, produkcja, tak)
        )
        _banner(settings, demo=demo)
        events = _events()
        koperta.licz_zadania(events)
        deps = _demo_deps(settings, events)[0] if demo else build_deps(settings, events=events)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            try:
                since = _as_utc(od)
            except ValueError as exc:
                raise ConfigError(f"--od musi być datą ISO (YYYY-MM-DD), jest: {od!r}") from exc
            # `--do` domyka zakres. Bez niego jedynym końcem jest „teraz", więc `aktualizuj`
            # przy rozjechanym znaczniku jest zobowiązaniem, którego operator nie ma jak
            # przyciąć: tabela kosztów ogłasza czterdzieści minut i tyle. Z `--do` da się
            # wziąć dwie godziny, zobaczyć wynik i wrócić po resztę.
            try:
                until = _koniec_zakresu_zmian(do, deps.clock)
            except ValueError as exc:
                raise ConfigError(f"--do musi być datą ISO (YYYY-MM-DD), jest: {do!r}") from exc
            prompter = _prompter(tak)
            start, plan = flow.prepare_update(deps, prompter, view, since=since, until=until)
            if not start:
                view.message(texts.update_declined(plan.count))
                # Zero zmian w zakresie to `nic_do_zrobienia`; rezygnacja po tabeli kosztów
                # to `przerwano`. Obie drogi kończą się tu, ale są to dwa różne fakty i
                # `plan.count` je rozróżnia bez liczenia czegokolwiek na nowo.
                koperta.ustaw(
                    Wynik(
                        polecenie="aktualizuj",
                        status="nic_do_zrobienia" if plan.count == 0 else "przerwano",
                        demo=deps.demo,
                        srodowisko=settings.environment,
                        dodatki={"zmienione": plan.count},
                    )
                )
                return
            result = run_update(deps, since=plan.since, until=plan.until, force_lock=force)
            view.message(
                texts.update_summary(
                    result.records, result.details, result.unresolved, result.stale_details
                )
            )
            pliki_aktualizacji: tuple[Path, ...] = ()
            if out:
                pliki_aktualizacji = _export_after(deps, result.run_id, out, None, "xlsx").paths
            koperta.ustaw(
                Wynik(
                    polecenie="aktualizuj",
                    status="ok",
                    demo=deps.demo,
                    srodowisko=settings.environment,
                    run_ids=(result.run_id,),
                    # Bez wspólnego `rekordy`: dla aktualizacji to słowo znaczyłoby „ile
                    # rekordów pobrano", czyli `szczegoly`, a nie „ile się zmieniło". Ta sama
                    # liczba pod dwoma kluczami jest zaproszeniem do rozjazdu, a `KLUCZE_WSPOLNE`
                    # duplikatu nie widzą (przegląd testów, 2026-09-24).
                    pliki=pliki_aktualizacji,
                    # Te same cztery liczby, które dostaje `texts.update_summary`.
                    # `nierozwiazane` i `przeterminowane` są tu nie mniej ważne niż dwie
                    # pierwsze: zero jest ich jedyną poprawną wartością, a cokolwiek innego
                    # znaczy, że praca została wykonana i zgubiona (ADR-0013, audyt A1).
                    dodatki={
                        "zmienione": result.records,
                        "szczegoly": result.details,
                        "nierozwiazane": result.unresolved,
                        "przeterminowane": result.stale_details,
                    },
                )
            )
        finally:
            events.close()
            deps.store.close()


@app.command("sprawdz-token")
def sprawdz_token(
    srodowisko: EnvOpt = None, produkcja: ProdOpt = False, wynik: WynikOpt = WYNIK_EKRAN
) -> None:
    """Pokazuje środowisko, źródło i datę ważności tokenu — nic więcej."""
    _tryb_json("sprawdz-token", wynik)
    try:
        settings = load_settings(environment=srodowisko, prod_consent=produkcja)
        view.message(texts.token_summary(settings, now=datetime.now(tz=UTC)))
        for warning in settings.warnings:
            view.warning(warning)
    except CeidgError as exc:
        _fail(exc)


AsystentOpt = Annotated[
    bool, typer.Option("--asystent", help="dotyczy klucza API asystenta, nie tokenu CEIDG")
]


@token_app.command("zapisz")
def token_zapisz(asystent: AsystentOpt = False, wynik: WynikOpt = WYNIK_EKRAN) -> None:
    """Zapisuje token CEIDG albo (z `--asystent`) klucz API w magazynie haseł, bez echa."""
    # Flaga także w podpoleceniach grupy, żeby „flaga jest przy każdym poleceniu" nie miało
    # przypisu. `token zapisz` wczytuje sekret z ukrytego wejścia, więc maszynowy wołający
    # nie ma tu czego szukać — i właśnie to mówi mu odmowa, zamiast angielskiego „No such
    # option" z kodem 2.
    _tryb_json("token", wynik)
    pytanie = texts.ASSISTANT_KEY_PROMPT if asystent else texts.TOKEN_PROMPT
    sekret = typer.prompt(pytanie, hide_input=True).strip()
    if not sekret:
        view.message(texts.ASSISTANT_KEY_EMPTY if asystent else texts.TOKEN_EMPTY)
        raise typer.Exit(code=3)
    try:
        store_token_in_keyring(sekret, KEYRING_ASSISTANT_USERNAME if asystent else KEYRING_USERNAME)
    except CeidgError as exc:
        _fail(exc)
    view.message(texts.assistant_key_saved() if asystent else texts.token_saved())


@token_app.command("usun")
def token_usun(asystent: AsystentOpt = False, wynik: WynikOpt = WYNIK_EKRAN) -> None:
    """Usuwa token CEIDG albo (z `--asystent`) klucz API z magazynu haseł."""
    _tryb_json("token", wynik)
    try:
        removed = delete_token_from_keyring(
            KEYRING_ASSISTANT_USERNAME if asystent else KEYRING_USERNAME
        )
    except CeidgError as exc:
        _fail(exc)
    if asystent:
        view.message(texts.ASSISTANT_KEY_REMOVED if removed else texts.ASSISTANT_KEY_ABSENT)
    else:
        view.message(texts.token_removed(removed))


@app.command()
def wyczysc(
    starsze_niz: Annotated[
        int | None, typer.Option("--starsze-niz", help="dni; domyślnie retencja z konfiguracji")
    ] = None,
    wszystko: Annotated[
        bool,
        typer.Option("--wszystko", help="usuń bazę, checkpointy, logi i pobrane raporty"),
    ] = False,
    potwierdzam: Annotated[
        bool,
        typer.Option(
            "--potwierdzam-usuniecie",
            help="Zgoda na nieodwracalne usunięcie bazy w trybie --tak",
        ),
    ] = False,
    srodowisko: EnvOpt = None,
    produkcja: ProdOpt = False,
    tak: YesOpt = False,
    wynik: WynikOpt = WYNIK_EKRAN,
) -> None:
    """Usuwa stare dane z bazy (albo całą bazę z logami po potwierdzeniu)."""
    _tryb_json("wyczysc", wynik)
    try:
        settings = _settings(srodowisko, produkcja, tak)
        if potwierdzam and not wszystko:
            raise ConfigError(
                "--potwierdzam-usuniecie dotyczy tylko --wszystko. Bez niego polecenie usuwa "
                "wyłącznie dane starsze niż retencja i żadnej zgody nie potrzebuje."
            )
        if wszystko:
            # Brak terminala to nie zgoda. Bez tego `wyczysc --wszystko` w harmonogramie kończył
            # się angielskim „Aborted." i kodem 1 zamiast czytelnym zdaniem i kodem 3.
            unattended = tak or not interactive_available(
                stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty(), yes=False
            )
            if unattended and not potwierdzam:
                raise ConfigError(texts.purge_all_needs_consent(settings.store_path))
            if not unattended and not _confirm(texts.confirm_purge_all(settings.store_path)):
                raise typer.Exit(code=0)
            close_file_handlers()  # Windows nie usunie otwartego pliku logu
            targets = [
                settings.store_path,
                *settings.store_path.parent.glob(settings.store_path.name + "*"),
                *settings.log_dir.glob("*.log*"),
            ]
            failures: list[tuple[str, str]] = []
            for path in targets:
                try:
                    path.unlink(missing_ok=True)
                except OSError as exc:
                    # Dalej, nie stop: zatrzymanie się na zajętym pliku bazy zostawiało raporty
                    # ZIP, czyli najobszerniejsze dane osobowe, na dysku bez słowa.
                    failures.append((str(path), str(exc)))
            zips = purge_report_files(settings.data_dir / "raporty", days=0, now_epoch=float("inf"))
            view.message(texts.purge_all_summary(zips))
            if failures:
                raise ConfigError(texts.purge_all_partial(failures))
            return
        deps = build_deps(settings, online=False)
        for warning in deps.warnings:
            view.warning(warning)
        try:
            days = dni_retencji(starsze_niz, settings.retention_days)
            runs, records = deps.store.purge_older_than(days)
            zips = purge_report_files(
                settings.data_dir / "raporty", days=days, now_epoch=datetime.now(tz=UTC).timestamp()
            )
            view.message(texts.purge_summary(runs, records, zips))
        finally:
            deps.store.close()
    except CeidgError as exc:
        _fail(exc)


def run() -> None:
    try:
        app()
    except KeyboardInterrupt:
        view.message(texts.INTERRUPTED)
        sys.exit(130)
