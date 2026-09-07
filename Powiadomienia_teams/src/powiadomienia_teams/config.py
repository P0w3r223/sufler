"""Ustawienia projektu — frozen dataclass + from_env() + validate().

Wzorzec jak w WorkMate (`src/workmate/config.py`). Prefiks zmiennych: `POWIADOMIENIA_`.
Sekrety (klucz Claude) mają `repr=False`. Domyślnie `dry_run=True` — nic nie wysyła ani
nie zapisuje, dopóki nie zostanie jawnie wyłączone.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

# `domain.tozsamosc` nie importuje niczego z projektu, więc cyklu tu nie ma.
from powiadomienia_teams.domain.tozsamosc import znormalizuj

_PREFIX = "POWIADOMIENIA_"

# Delegowane scope Graph — wszystkie nadane i potwierdzone na żywo (Smoke #1, 2026-07-14).
# MSAL sam dokłada openid/profile/offline_access — nie wpisywać ich tutaj.
_DEFAULT_SCOPES: tuple[str, ...] = (
    "User.Read",
    "User.ReadBasic.All",
    "TeamMember.Read.All",
    "Schedule.Read.All",
    "Schedule.ReadWrite.All",
    "Chat.Create",
    "Chat.ReadWrite",
    "ChatMessage.Send",
)

_DEFAULT_TOKEN_CACHE = Path.home() / ".workmate" / "teams_token_cache.bin"
_DEFAULT_STATE = Path.home() / ".workmate" / "powiadomienia_state.json"


class ConfigError(ValueError):
    """Brak lub niepoprawna wartość wymaganego ustawienia."""


@dataclass(frozen=True)
class TeamContext:
    """Dane JEDNEGO zespołu potrzebne obiegowi do odczytu/zapisu (team_id + grupa grafiku).

    Wydzielone ze skalarnej konfiguracji, żeby orkiestracja brała zespół z PARAMETRU, nie z
    globalnych ustawień. Dziś jest zawsze jeden (z konfiguracji jednozespołowej) — to szew, w który
    wielozespołowość ([ADR 0001](../../docs/adr/0001-multi-team-shifts-support.md)) wepnie iterację
    po wielu kontekstach bez zmiany logiki obiegu.
    """

    team_id: str
    scheduling_group_id: str | None = None


@dataclass(frozen=True)
class OknoOdpowiedzi:
    """Polityka TERMINU odpowiedzi — trzy liczby, które razem odpowiadają „do kiedy wolno czekać".

    Wydzielone z ``Settings`` z tego samego powodu co ``TeamContext``: ``reminders.lifecycle`` jest
    czystą logiką czasu i nie ma prawa znać całej konfiguracji, a składanie tych trzech wartości
    u każdego wołającego z osobna kończyło się w A7 warstwą, której skasowanie nie psuło ani
    jednego testu. Mieszka TUTAJ, nie w ``lifecycle``, bo to projekcja konfiguracji — kierunek
    zależności jest ``lifecycle`` → ``config`` i taki musi zostać (``config`` nie wolno importować
    z ``reminders/``, inaczej powstanie cykl).

    ``offset_h`` liczy się od LOKALNEJ PÓŁNOCY PONIEDZIAŁKU tygodnia docelowego i wolno mu być
    ujemny (termin przed początkiem tygodnia). ``min_h`` to dolna granica kurtuazji od ostatniej
    prośby bota — patrz ``lifecycle.termin_odpowiedzi``.
    """

    offset_h: int
    min_h: int
    tz: ZoneInfo


@dataclass(frozen=True)
class OknoCiszy:
    """Godziny, w których usługa NIE pisze do pracowników. Reguła produktowa, nie techniczna.

    Nie jest niezmiennikiem i nie ma za sobą awarii: wiadomość o 23:40 na czacie prywatnym jest
    wtargnięciem, choćby była uprzejma, i to jest cały powód istnienia tego okna.

    Godziny są LOKALNE (strefa zespołu) i pełne — granica zawsze wypada „o którejś". Okno wolno
    zapętlić przez północ (``od_h=20``, ``do_h=7``); równe wartości znaczą „bez ciszy" i to jedyny
    sposób jej wyłączenia, bo osobna flaga dawałaby dwa źródła prawdy o tej samej rzeczy.
    """

    od_h: int
    do_h: int
    tz: ZoneInfo

    @property
    def wylaczone(self) -> bool:
        return self.od_h == self.do_h


def _get(name: str, default: str = "") -> str:
    """Wartość tekstowa ze środowiska — obcięta z białych znaków, pusta znaczy „domyślnie".

    Obcinanie obowiązuje WSZYSTKIE wartości tekstowe, nie tylko sekret. `env` edytowany na Windows
    i przeniesiony na serwer niesie CRLF, a `_get` był jedynym czytnikiem w tym pliku, który tego
    nie zdejmował (`_bool`, `_int` i `_list` zdejmują od zawsze). Cena była różna, ale nigdzie
    zerowa: adres webhooka z doklejonym `\\r` przechodzi kontrolę `https://`, po czym `httpx`
    odrzuca go przy wysyłce, a `send_alert` łyka wyjątek — czyli jedyny kanał niezależny od AAD
    milczy i zostaje po nim wyłącznie WARNING w logu, którego w instalacji bezobsługowej nikt nie
    czyta. Przy `LLM_MODEL` skutek jest jeszcze cichszy: 404 z API nie jest niedostępnością
    granicy, więc wyjątek gaśnie w izolacji per-osoba.
    """
    return _sekret(_PREFIX + name) or default


_PRAWDA = frozenset({"1", "true", "yes", "on", "tak"})
_FALSZ = frozenset({"0", "false", "no", "off", "nie"})


def _bool(name: str, default: bool) -> bool:
    """Wartość logiczna ze środowiska — nierozpoznana treść to BŁĄD, nie ciche `False`.

    Wcześniej wszystko spoza zbioru prawdy schodziło do `False`, więc literówka w WARTOŚCI
    zmiennej wyłączała ochronę bezgłośnie: `DRY_RUN=ture` znaczyło „pracuj na serio", choć operator
    napisał to, myśląc odwrotnie — a krok 7 runbooka każe wpisać tę wartość ręcznie. Ta sama klasa
    błędu dotyczy `PILOTAZ` i `ALERTY_WYLACZONE`: obie są bramkami, a bramka wyłączana literówką
    nie jest bramką. Sąsiedni `_int` fail-fastuje od zawsze — to była niespójność w jednym pliku.

    Pusta wartość nadal znaczy „domyślnie": `ZMIENNA=` w `env` to typowy sposób zapisania
    „zostawiam jak jest", a nie pomyłka.
    """
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    wartosc = raw.strip().lower()
    if wartosc in _PRAWDA:
        return True
    if wartosc in _FALSZ:
        return False
    raise ConfigError(
        f"{_PREFIX}{name} musi być wartością logiczną: {raw!r}. "
        f"Dozwolone: {', '.join(sorted(_PRAWDA))} / {', '.join(sorted(_FALSZ))}"
    )


def _int(name: str, default: int) -> int:
    raw = os.environ.get(_PREFIX + name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{_PREFIX}{name} musi być liczbą całkowitą: {raw!r}") from exc


def _path(name: str, default: Path) -> Path:
    """Ścieżka ze środowiska — obcięta z białych znaków, jak każda inna wartość tekstowa.

    Do 0.2.12 `_path` był jedynym czytnikiem w tym pliku, który brał wartość surową. Cena tego
    wyjątku jest wyższa niż gdzie indziej: `STATE_PATH` z doklejonym `\\r` (`env` edytowany na
    Windows) albo ze spacją na końcu wskazuje na Linuksie INNY, całkowicie legalny plik. Usługa
    startuje wtedy z pustym stanem — czyli uzna wszystkich za nienagabywanych i **wyśle prośby
    drugi raz do całego zespołu**, a otwarte rozmowy przepadną. To ta sama awaria, przed którą
    broni cała maszyneria kopii stanu (A3), wywołana literówką niewidoczną w edytorze.
    """
    wartosc = _sekret(_PREFIX + name)
    return Path(wartosc).expanduser() if wartosc else default


def _list(name: str) -> tuple[str, ...]:
    raw = os.environ.get(_PREFIX + name, "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _bez_dubli(pozycje: tuple[str, ...]) -> tuple[str, ...]:
    """Zachowuje kolejność PIERWSZEGO wystąpienia. Duplikat adresata = druga ta sama wiadomość."""
    widziane: dict[str, None] = dict.fromkeys(pozycje)
    return tuple(widziane)


# Klucz do API modelu: JEDNA nazwa kanoniczna i jeden alias sprzed tej zmiany. Alias jest BEZ
# prefiksu, bo `ANTHROPIC_API_KEY` to konwencja SDK — dlatego słownik niżej trzyma nazwy PEŁNE,
# a nie doklejane do prefiksu.
NAZWA_KLUCZA_MODELU = _PREFIX + "AGENT_API_KEY"
ALIAS_KLUCZA_MODELU = "ANTHROPIC_API_KEY"

# Zmienne wycofywane: stara PEŁNA nazwa → nowa. Czytane nadal (ciche zignorowanie zabrałoby
# działającej instalacji funkcję bez śladu), ale start ma o tym powiedzieć — inaczej migracja
# nigdy się nie kończy, bo nic o niej nie przypomina.
PRZESTARZALE_NAZWY = {
    _PREFIX + "ADMIN_USER_ID": _PREFIX + "ADMIN_USER_IDS",
    ALIAS_KLUCZA_MODELU: NAZWA_KLUCZA_MODELU,
}

# Zmienne USUNIĘTE: nazwa nadal rozpoznawana, ale wartość NIE MA już żadnego znaczenia — inaczej
# niż w `PRZESTARZALE_NAZWY`, gdzie wartość działa i trzeba ją tylko przenieść. Tu nie ma czego
# przenosić: „48 h ciszy" nie przekłada się ani na termin kalendarzowy, ani na retencję, bo to były
# dwie różne role zwinięte w jedną liczbę. Ciche zignorowanie jest tu najgroźniejszym wariantem:
# operator, który wpisał `REPLY_WINDOW_HOURS=72`, ma prawo myśleć, że wydłużył ludziom czas, a nie
# zmienił nic. Zdanie na starcie jest jedynym miejscem, w którym może się o tym dowiedzieć.
USUNIETE_NAZWY = {
    _PREFIX + "REPLY_WINDOW_HOURS": (
        f"okno odpowiedzi przestało być liczbą godzin ciszy — termin jest teraz kalendarzowy "
        f"({_PREFIX}REPLY_DEADLINE_OFFSET_H, {_PREFIX}REPLY_MIN_HOURS), a retencja wpisów "
        f"terminalnych ma własną zmienną ({_PREFIX}TERMINAL_RETAIN_HOURS)"
    ),
}


def uzyte_usuniete_nazwy() -> list[tuple[str, str]]:
    """Pary (nazwa, powód) dla zmiennych USUNIĘTYCH obecnych w środowisku — do logu startowego."""
    return [
        (nazwa, powod)
        for nazwa, powod in USUNIETE_NAZWY.items()
        if os.environ.get(nazwa, "").strip()
    ]


def uzyte_przestarzale_nazwy() -> list[tuple[str, str]]:
    """Pary (stara, nowa) dla zmiennych wycofywanych obecnych w środowisku — do logu startowego."""
    return [
        (stara, nowa)
        for stara, nowa in PRZESTARZALE_NAZWY.items()
        if os.environ.get(stara, "").strip()
    ]


def _sekret(nazwa_pelna: str) -> str:
    """Wartość spod PEŁNEJ nazwy zmiennej, obcięta z białych znaków. Podstawa `_get`.

    Osobno od `_get`, bo alias klucza modelu (`ANTHROPIC_API_KEY`) nie ma prefiksu — nie ma jak
    zapytać o niego przez nazwę skróconą.

    Obcinanie nie jest kosmetyką. Klucz API nigdy nie ma białych znaków na brzegach, a wklejenie
    go z konsoli dostawcy razem z końcem linii daje wartość, która przechodzi walidację startową
    (niepusta), po czym KAŻDA interpretacja odpowiedzi kończy się 401 — po cichu, bo wyjątek łapie
    izolacja per-osoba. To ta sama klasa błędu co literówka w wartości logicznej (patrz `_bool`).

    Skutkiem ubocznym jest to, że wartość pusta albo złożona z samych spacji znaczy „nie ustawiono",
    dzięki czemu `POWIADOMIENIA_AGENT_API_KEY=` (typowe „zostawiam jak jest") nie wygasza aliasu.
    """
    return os.environ.get(nazwa_pelna, "").strip()


def klucz_modelu() -> str:
    """Klucz do API modelu — kanoniczna nazwa WYGRYWA, alias działa dalej.

    Do 0.2.12 pierwszeństwo miał alias, więc instalacja z obiema nazwami brała tę, której operator
    nie uważał za obowiązującą. Jedna rzecz ma jedną nazwę: `POWIADOMIENIA_AGENT_API_KEY`. Alias
    zostaje, bo obraz u klienta ma dziś właśnie jego, a ciche zignorowanie zatrzymałoby nasłuch
    komunikatem o brakującym kluczu, który przecież jest ustawiony.
    """
    return _sekret(NAZWA_KLUCZA_MODELU) or _sekret(ALIAS_KLUCZA_MODELU)


def alias_klucza_zignorowany() -> bool:
    """Czy ustawiono obie nazwy klucza naraz — wtedy wartość aliasu NIE jest używana.

    Osobno od `uzyte_przestarzale_nazwy()`, bo to inna sytuacja: tam wartość działa i trzeba ją
    kiedyś przenieść, tu wartość jest ignorowana już teraz. Bez tego rozróżnienia rotacja klucza
    wpisana pod starą nazwą wyglądałaby na wykonaną, a usługa dalej używałaby poprzedniego.
    """
    return bool(_sekret(NAZWA_KLUCZA_MODELU)) and bool(_sekret(ALIAS_KLUCZA_MODELU))


# Długość cyklu, wokół którego kręci się cała ta usługa: jeden przebieg tygodniowy. Nazwana, bo
# `sufit_wpisu_bez_odczytu_h` jest z niej WYPROWADZONY (ADR 0007) — goła `168` w warunku walidacji
# wyglądałaby na dowolnie dobrany limit, a jest granicą wyścigu z kolejnym przebiegiem.
_GODZIN_W_TYGODNIU = 7 * 24


@dataclass(frozen=True)
class Settings:
    client_id: str
    tenant_id: str
    team_id: str
    scheduling_group_id: str | None = None
    scopes: tuple[str, ...] = _DEFAULT_SCOPES
    token_cache_path: Path = field(default_factory=lambda: _DEFAULT_TOKEN_CACHE)
    state_path: Path = field(default_factory=lambda: _DEFAULT_STATE)
    run_weekday: int = 4  # piątek (0=poniedziałek … 6=niedziela)
    run_hour: int = 16
    run_minute: int = 0
    timezone: str = "Europe/Warsaw"
    # Termin odpowiedzi = północ poniedziałku tygodnia DOCELOWEGO + tyle godzin. Domyślnie +5,
    # czyli poniedziałek 05:00: grafik musi być w Shifts przed pierwszą zmianą (typowo 6:00), a to
    # jest rozstrzygnięcie klienta (plan §4.2/1), nie liczba techniczna. Wolno ujemny — termin przed
    # początkiem tygodnia. Świadoma cena tej wartości: odpowiedź z poniedziałku po 05:00 jest już po
    # terminie — godzinę przed pierwszą zmianą i trzy godziny przed typowym początkiem dnia
    # biurowego (na tej drugiej liczbie stoi test). Patrz `plan-rozwoju.md` B1.
    reply_deadline_offset_h: int = 5
    # Dolna granica kurtuazji: nigdy nie zamykaj tematu wcześniej niż tyle godzin po OSTATNIEJ
    # prośbie bota. Jedyne, co zostało z kotwicy N10 — bez tego pending obsłużony po przestoju
    # dostaje prośbę o potwierdzenie i wygasa w kolejnym cyklu, bo termin kalendarzowy już minął.
    reply_min_hours: int = 24
    # Retencja wpisów TERMINALNYCH — osobna liczba, nie okno odpowiedzi. Do 0.2.12 obie role brała
    # jedna zmienna, więc każda zmiana terminu przestawiała po cichu strażnika N15 („jedna prośba
    # na osobę na tydzień"), a skutkiem było ponowne zaczepienie osoby, której bot obiecał
    # „kończę przypominanie".
    terminal_retain_hours: int = 48
    # Godziny ciszy: nie pisz do PRACOWNIKÓW między `cisza_od_h` a `cisza_do_h` (lokalnie).
    # Alerty operatorskie i cotygodniowe podsumowanie dla administratora idą zawsze — dotyczą
    # stanu USŁUGI, a podsumowanie jest dead man's switchem, w którym brak wiadomości JEST
    # sygnałem. Równe wartości wyłączają ciszę (patrz `OknoCiszy`).
    cisza_od_h: int = 20
    cisza_do_h: int = 7
    send_expiry_message: bool = True  # przy wygaśnięciu wyślij uprzejme domknięcie do pracownika
    poll_interval_s: int = 10  # bazowy (minimalny) odstęp odpytywania; backoff go wydłuża
    poll_max_interval_s: int = 3600  # górny limit odstępu przy długiej ciszy (1 h)
    # Po ilu sekundach CISZY pracownika (ta sama kotwica co wygaśnięcie) wolno zajrzeć do Shifts,
    # by wykryć samodzielne uzupełnienie grafiku. -1 wyłącza funkcję; 0 = sprawdzaj co cichy cykl.
    self_fill_check_min_idle_s: int = 3600
    catchup_grace_hours: int = 6  # jak długo po minionym terminie wolno nadrobić przebieg (0=off)
    # Twardy sufit WIEKU wpisu, którego nie da się rozstrzygnąć, bo czat nie daje się odczytać
    # (ADR 0007). Liczony od kotwicy `lifecycle._anchor` („jak dawno cokolwiek się tu działo"),
    # NIE od `week_start` — mierzymy bezruch rozmowy, nie odległość od tygodnia docelowego.
    #
    # 144 h = 6 dób, czyli doba KRÓCEJ niż cykl tygodniowy (168 h), i ta doba nie jest zapasem
    # „na wszelki wypadek". Kotwicą stojącego wpisu jest w najgorszym razie `nudged_at` ustawiony
    # w trakcie przebiegu, więc sufit 168 h wypadałby dokładnie w chwili kolejnego przebiegu.
    # Przegrana tego wyścigu nie jest niewinna: `run_once` wchodzi wtedy w gałąź nadpisania, która
    # alarmuje operatora, że uzgodnienie nie trafiło do grafiku — a uzgodnienia nigdy nie było.
    # Doba pokrywa okno łaski (`catchup_grace_hours`, 6 h), odłożenie obiegu na godziny ciszy
    # (do 11 h przy 20–7) i dryf DST (1 h): razem 18 h.
    #
    # Nazwa świadomie POZA rodziną `REPLY_*` — `REPLY_WINDOW_HOURS` leży na `USUNIETE_NAZWY`
    # właśnie za zwinięcie dwóch różnych ról w jedną liczbę.
    sufit_wpisu_bez_odczytu_h: int = 144
    dry_run: bool = True
    only_user_ids: tuple[str, ...] = ()  # pusty = wszyscy; ustawiony = tryb pilotażowy
    # Deklaracja ZAMIARU ograniczenia odbiorców, niezależna od samej listy. Pusta `only_user_ids`
    # znaczy „wszyscy", więc literówka w nazwie zmiennej (`ONLY_USER_ID`, `ONLYUSERIDS`, zły
    # prefiks) zamienia pilotaż na pięciu osobach w wysyłkę do całego zespołu — bezgłośnie, bo
    # konfiguracja z literówką jest nieodróżnialna od konfiguracji świadomie pustej. Wysyłki nie
    # da się cofnąć. Dwie zmienne muszą się tu zgadzać, żeby cokolwiek wyszło.
    pilotaz: bool = False
    llm_model: str = "claude-haiku-4-5"
    # Czytane z `POWIADOMIENIA_AGENT_API_KEY` (kanoniczna) albo `ANTHROPIC_API_KEY` (alias) —
    # patrz `klucz_modelu`.
    anthropic_api_key: str = field(default="", repr=False)
    # --- Praca bezobsługowa ---
    # AAD id administratorów — odbiorcy cotygodniowego podsumowania. LISTA, nie skalar: to
    # podsumowanie jest dead man's switchem, a w instalacji bez monitoringu brak wiadomości
    # w piątek bywa jedynym sygnałem awarii. Przy jednym odbiorcy przełącznik jest martwy przez
    # cały jego urlop — czyli dokładnie wtedy, gdy nie ma komu zauważyć awarii, nie ma też komu
    # zauważyć jej braku. Stara skalarna wartość `ADMIN_USER_ID` nadal działa (patrz `from_env`).
    admin_user_ids: tuple[str, ...] = ()
    # URL webhooka bywa sekretem (potrafi zawierać token w ścieżce) → repr=False jak klucz API.
    alert_webhook_url: str = field(default="", repr=False)
    # Jawna rezygnacja z alertowania przy `dry_run=false`. Furtka, nie domyślność: bez niej
    # instalacja produkcyjna, w której ktoś przeoczył webhooka przy wypełnianiu `env`, wygląda
    # dokładnie tak samo jak instalacja, w której zrezygnowano z alertów świadomie.
    alerty_wylaczone: bool = False
    # Czy logi i alerty mają nazywać pracownika nazwiskiem, czy identyfikatorem (A10).
    # Domyślnie identyfikator: alert zostaje w kanale Teams bezterminowo i jest przeszukiwalny,
    # a logi kontenera podlegają rotacji, nie polityce retencji. Operator rozwiąże identyfikator
    # na nazwisko `scripts/lista_czlonkow.py` — na żądanie i bez zostawiania śladu.
    # Włączenie jest świadomym poszerzeniem tego, co opuszcza instalację, nie ustawieniem
    # wygody: patrz README → „Jakie dane opuszczają instalację".
    loguj_nazwiska: bool = False
    heartbeat_interval_h: int = 24  # co ile godzin sprawdzać sesję poza przebiegiem tygodniowym
    auth_failure_exit_delay_s: int = 600  # ile czekać przed wyjściem po utracie sesji
    # Po jakim czasie bez pulsu healthcheck uznaje pętlę za martwą. NIEZALEŻNE od sufitu nasłuchu:
    # puls bije co minutę (`runtime.service.spij_z_pulsem`), więc próg nie musi rosnąć razem z
    # odstępem
    # odpytywania. Wcześniejsze wyprowadzanie progu z `poll_max_interval_s` dawało 2 h.
    health_max_age_s: int = 900
    # Sufit czasu na JEDEN przebieg (tygodniowy albo obieg nasłuchu). Jedyny limit obejmujący
    # więcej niż jedno żądanie — patrz `runtime.budzet`. 0 wyłącza.
    run_deadline_s: int = 1800

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    @property
    def team_context(self) -> TeamContext:
        """Kontekst zespołu z obecnej (jednozespołowej) konfiguracji — na razie zawsze jeden."""
        return TeamContext(self.team_id, self.scheduling_group_id)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def okno_odpowiedzi(self) -> OknoOdpowiedzi:
        """Polityka terminu odpowiedzi dla ``reminders.lifecycle`` — jedno miejsce jej składania."""
        return OknoOdpowiedzi(
            offset_h=self.reply_deadline_offset_h,
            min_h=self.reply_min_hours,
            tz=self.tz,
        )

    @property
    def okno_ciszy(self) -> OknoCiszy:
        """Godziny ciszy dla `runtime.cisza` — jedno miejsce składania tej polityki."""
        return OknoCiszy(od_h=self.cisza_od_h, do_h=self.cisza_do_h, tz=self.tz)

    @property
    def godzina_przebiegu_w_ciszy(self) -> bool:
        """Czy zaplanowana godzina przebiegu wypada w oknie ciszy — do ostrzeżenia startowego.

        Konfiguracja legalna (`validate()` sprawdza tylko zakresy), a kosztowna: przebieg jest
        wtedy odkładany CO TYDZIEŃ do końca ciszy. Kosztuje tydzień wyłącznie w złożeniu
        z `catchup_grace_hours == 0`, bo zero wyłącza nadrabianie w ogóle — okno łaski NIE liczy
        godzin ciszy (`_catchup_due` odejmuje `cisza_pomiedzy`), więc każda wartość dodatnia
        wystarcza. Zdanie o „krótkim oknie łaski" stało tu do 0.2.13 i wysyłało po niewłaściwą
        dźwignię: kazało podnosić liczbę, która niczego nie blokowała.

        Liczone bez zegara, bo zależy wyłącznie od dwóch par liczb w konfiguracji.
        """
        if self.okno_ciszy.wylaczone:
            return False
        if self.cisza_od_h < self.cisza_do_h:
            return self.cisza_od_h <= self.run_hour < self.cisza_do_h
        return self.run_hour >= self.cisza_od_h or self.run_hour < self.cisza_do_h

    @property
    def godzin_od_przebiegu_do_terminu(self) -> float:
        """Ile godzin ma pracownik od przebiegu tygodniowego do TERMINU kalendarzowego.

        Przebieg celuje zawsze w poniedziałek NASTĘPNEGO tygodnia
        (``scheduler.weekly.week_windows``),
        więc odstęp wynika wprost z konfiguracji i da się go policzyć bez zegara. Liczba jest tu, a
        nie
        w warstwie ostrzeżeń, bo to arytmetyka na ustawieniach — i dlatego daje się sprawdzić testem
        bez uruchamiania startu usługi.

        Dryf DST (±1 h) świadomie pomijamy: to wejście do PROGU ostrzeżenia, nie do terminu.
        """
        do_poniedzialku = (7 - self.run_weekday) * 24 - self.run_hour - self.run_minute / 60
        return do_poniedzialku + self.reply_deadline_offset_h

    def __post_init__(self) -> None:
        """Normalizacja identyfikatorów pilotażu — RAZ, na granicy konfiguracji.

        `ONLY_USER_IDS` wypełnia człowiek, czasem kopiując z portalu Azure, czyli w klamrach albo
        wielkimi literami. Filtr w `runtime.nudge` porównywał to z `m.user_id` z Graph znak w znak,
        więc taki wpis nie pasował do NIKOGO: pilotaż milczał, a podsumowanie mówiło „0 próśb" —
        nieodróżnialnie od spokojnego tygodnia.

        Normalizujemy tutaj, a nie w `from_env`, bo `Settings` bywa budowany wprost (testy, kod
        wołający). I normalizujemy WARTOŚĆ, nie tylko porównanie — w odróżnieniu od identyfikatorów
        z Graph, `only_user_ids` nie trafia ani do pliku stanu, ani do żadnego `POST`-a: służy
        wyłącznie do testu przynależności i do wypisania w alercie startowym. Dzięki temu nowe
        miejsce porównania nie musi pamiętać o `casefold` po tej stronie.

        `frozen=True`, więc przez `object.__setattr__` — jedyna droga i celowo widoczna.

        Uwaga na skutek uboczny: `operator.opis_kregu_odbiorcow` wypisuje tę listę w alercie
        startowym, więc przestaje ona być DOSŁOWNĄ kopią wpisu z `env`. Alert mówi o tym wprost.
        """
        # Deduplikacja PO normalizacji: „AB-CD" i „ab-cd" to jedna osoba, a bez tego alert
        # startowy mówiłby „na liście: 2" i operator szukałby drugiej. `admin_user_ids` przechodzi
        # przez `_bez_dubli` z tego samego powodu.
        znormalizowane: list[str] = []
        for surowy in self.only_user_ids:
            kanoniczny = znormalizuj(surowy)
            if kanoniczny not in znormalizowane:
                znormalizowane.append(kanoniczny)
        object.__setattr__(self, "only_user_ids", tuple(znormalizowane))

    @property
    def webhook_alertow(self) -> str:
        """Adres, na który realnie idą alerty — pusty, gdy alertowanie wyłączono świadomie.

        Jedno miejsce rozstrzygające, że jawne wyłączenie WYGRYWA z ustawionym adresem. Gdyby
        pierwszeństwo żyło w komunikacie na starcie, a `operator.alert` sięgał po surowe pole,
        log mówiłby „alerty nie będą wysyłane", a webhook dostawałby ruch — czyli sprzeczna
        konfiguracja dawałaby sprzeczne zachowanie zamiast jednego, przewidywalnego.
        """
        return "" if self.alerty_wylaczone else self.alert_webhook_url

    @property
    def heartbeat_path(self) -> Path:
        """Plik pulsu obok stanu — czyta go HEALTHCHECK obrazu.

        Wyprowadzony ze `state_path`, a nie osobną zmienną: ma leżeć na tym samym wolumenie co stan
        (inaczej byłby zapisywany do systemu plików tylko-do-odczytu), a jedna ścieżka mniej
        w konfiguracji to jedna okazja mniej, żeby rozjechała się z punktem montowania.
        """
        return self.state_path.with_name("heartbeat")

    def validate_dostep(self) -> None:
        """Minimum potrzebne, żeby SIĘ ZALOGOWAĆ i ODCZYTAĆ roster — nic ponadto.

        Wydzielone z ``validate()``, bo pełna lista kontrolna jest listą warunków **wysyłki**,
        a dwie czynności przygotowawcze wysyłki nie robią: ``--login`` i
        ``scripts/lista_czlonkow.py``.
        Bez tego podziału powstaje zakleszczenie: bramka pilotażu żąda identyfikatorów, a jedyne
        narzędzie, które je wypisuje, sama blokuje. Operator dostawał instrukcję naprawy, której
        nie da się wykonać w tym samym ``env``.
        """
        missing = [n for n in ("client_id", "tenant_id", "team_id") if not getattr(self, n)]
        if missing:
            raise ConfigError(f"Brak wymaganych ustawień: {', '.join(missing)}")
        try:
            _ = self.tz  # walidacja nazwy strefy
        except Exception as exc:
            raise ConfigError(f"Nieznana strefa czasowa: {self.timezone!r}") from exc

    # Sufit funkcji przekroczony ŚWIADOMIE: to lista kontrolna warunków startu, a jej wartością
    # jest KOLEJNOŚĆ, w jakiej padają komunikaty do operatora. Rozbicie na grupy tę kolejność
    # rozmywa i zachęca do sprawdzania warunków w dwóch miejscach. Dług, nie usprawiedliwienie.
    def validate(self) -> None:  # noqa: C901, PLR0915
        self.validate_dostep()
        if not 0 <= self.run_weekday <= 6:
            raise ConfigError(f"run_weekday poza zakresem 0..6: {self.run_weekday}")
        if not 0 <= self.run_hour <= 23:
            raise ConfigError(f"run_hour poza zakresem 0..23: {self.run_hour}")
        if not 0 <= self.run_minute <= 59:
            raise ConfigError(f"run_minute poza zakresem 0..59: {self.run_minute}")
        if self.poll_interval_s < 5:
            raise ConfigError(f"poll_interval_s musi być ≥ 5 s: {self.poll_interval_s}")
        if self.poll_max_interval_s < self.poll_interval_s:
            raise ConfigError(
                f"poll_max_interval_s ({self.poll_max_interval_s}) musi być ≥ poll_interval_s "
                f"({self.poll_interval_s})"
            )
        # Termin poza tygodniem docelowym nie jest terminem odpowiedzi, tylko literówką: offset
        # liczony w godzinach łatwo pomylić z dniami (`REPLY_DEADLINE_OFFSET_H=5` kontra `=120`).
        if not -168 <= self.reply_deadline_offset_h <= 168:
            raise ConfigError(
                f"reply_deadline_offset_h poza zakresem -168..168 (tydzień w każdą stronę): "
                f"{self.reply_deadline_offset_h}"
            )
        # 0 wyłączyłoby dolną granicę kurtuazji, czyli JEDYNĄ pozostałość kotwicy N10 — a to nie
        # jest ustawienie, które ktokolwiek wybiera świadomie przez wpisanie zera.
        if self.reply_min_hours < 1:
            raise ConfigError(f"reply_min_hours musi być ≥ 1 h: {self.reply_min_hours}")
        for nazwa in ("cisza_od_h", "cisza_do_h"):
            godzina = getattr(self, nazwa)
            if not 0 <= godzina <= 23:
                raise ConfigError(f"{nazwa} poza zakresem 0..23: {godzina}")
        if self.terminal_retain_hours <= 0:
            raise ConfigError(f"terminal_retain_hours musi być > 0: {self.terminal_retain_hours}")
        if self.self_fill_check_min_idle_s < -1:
            raise ConfigError(
                f"self_fill_check_min_idle_s musi być ≥ -1 (-1 wyłącza): "
                f"{self.self_fill_check_min_idle_s}"
            )
        if self.catchup_grace_hours < 0:
            raise ConfigError(f"catchup_grace_hours < 0 niedozwolone: {self.catchup_grace_hours}")
        # Sufitu NIE da się wyłączyć zerem — wyłączony przywraca dokładnie tę usterkę, którą
        # ADR 0007 zamyka (wpis wisi bez końca, blokując osobę co tydzień), i robi to bezgłośnie.
        if self.sufit_wpisu_bez_odczytu_h <= 0:
            raise ConfigError(
                f"sufit_wpisu_bez_odczytu_h musi być > 0 (nie da się wyłączyć — ADR 0007): "
                f"{self.sufit_wpisu_bez_odczytu_h}"
            )
        # Poniżej kurtuazji sufit zamykałby rozmowy W TOKU: `reply_min_hours` to dolna granica
        # terminu liczona od ostatniej prośby bota, a kotwica sufitu tę prośbę obejmuje.
        if self.sufit_wpisu_bez_odczytu_h <= self.reply_min_hours:
            raise ConfigError(
                f"sufit_wpisu_bez_odczytu_h ({self.sufit_wpisu_bez_odczytu_h} h) musi być > "
                f"reply_min_hours ({self.reply_min_hours} h) — inaczej sufit zamykałby rozmowy, "
                f"w których bot dopiero co o coś poprosił"
            )
        # Powyżej tygodnia sufit ściga się z kolejnym przebiegiem — patrz komentarz przy polu.
        if self.sufit_wpisu_bez_odczytu_h > _GODZIN_W_TYGODNIU:
            raise ConfigError(
                f"sufit_wpisu_bez_odczytu_h ({self.sufit_wpisu_bez_odczytu_h} h) > "
                f"{_GODZIN_W_TYGODNIU} h: sufit dłuższy niż cykl tygodniowy wypada dopiero po "
                f"kolejnym przebiegu, który nadpisze wpis i zaalarmuje o nieistniejącym uzgodnieniu"
            )
        if self.heartbeat_interval_h <= 0:
            raise ConfigError(f"heartbeat_interval_h musi być > 0: {self.heartbeat_interval_h}")
        if self.health_max_age_s <= 0:
            raise ConfigError(f"health_max_age_s musi być > 0: {self.health_max_age_s}")
        if self.run_deadline_s < 0:
            raise ConfigError(f"run_deadline_s < 0 niedozwolone (0 wyłącza): {self.run_deadline_s}")
        if self.auth_failure_exit_delay_s < 0:
            raise ConfigError(
                f"auth_failure_exit_delay_s < 0 niedozwolone: {self.auth_failure_exit_delay_s}"
            )
        # Sprawdzane ZAWSZE, nie tylko przy dry_run=false. Sekwencja wdrożenia każe przećwiczyć
        # pilotaż najpierw w trybie próbnym (deploy/README-docker.md) — gdyby bramka milczała
        # próba nie sprawdzałaby dokładnie tego, co ma ochronić przy przełączeniu na serio.
        if self.pilotaz and not self.only_user_ids:
            raise ConfigError(
                f"{_PREFIX}PILOTAZ=true wymaga niepustego {_PREFIX}ONLY_USER_IDS — pusta lista "
                f"znaczy »wszyscy«, więc pilotaż wysłałby wiadomości do CAŁEGO zespołu. "
                f"Identyfikatory wypisze scripts/lista_czlonkow.py"
            )
        # Wpis, który po normalizacji nie jest już żadnym identyfikatorem — np. same klamry
        # albo sam biały znak. ZATRZYMUJEMY START, zamiast go po cichu wyrzucić, bo wyrzucenie
        # jest tu groźniejsze niż zostawienie: pusta lista znaczy „wszyscy", więc literówka
        # zamieniłaby ciszę pilotażu w wysyłkę do CAŁEGO zespołu. Zostawiony pusty napis też nie
        # jest wyjściem — nie pasuje do nikogo, a `PILOTAZ` widzi listę jako niepustą, więc
        # pilotaż milczy z własnego powodu i wygląda to jak spokojny tydzień. To ta sama klasa
        # cichej awarii, którą normalizacja miała zamknąć.
        puste = sum(1 for i in self.only_user_ids if not i)
        if puste:
            raise ConfigError(
                f"{_PREFIX}ONLY_USER_IDS zawiera {puste} pozycji, które po normalizacji nie są "
                f"identyfikatorem (same klamry albo białe znaki). Taki wpis nie pasuje do NIKOGO, "
                f"a lista wygląda na niepustą — pilotaż milczałby, wyglądając na spokojny tydzień. "
                f"Popraw wpis albo usuń go. Identyfikatory wypisze scripts/lista_czlonkow.py"
            )
        if not self.dry_run and not self.scheduling_group_id:
            raise ConfigError(
                "scheduling_group_id jest wymagane, gdy dry_run=false (zapis zmian do Shifts)"
            )
        # Bez klucza SDK i tak wyśle żądanie (pusty string ≠ None, więc nie ma fallbacku na profil
        # OAuth) i dostanie 401 — dla KAŻDEJ odpowiedzi, po cichu, bo wyjątek łapie izolacja
        # per-osoba. Bot wysyłałby prośby, na które nigdy nie odpowiada. Fail-fast na starcie.
        if not self.dry_run and not self.anthropic_api_key:
            raise ConfigError(
                "anthropic_api_key jest wymagane, gdy dry_run=false (interpretacja odpowiedzi). "
                f"Ustaw {NAZWA_KLUCZA_MODELU}"
            )
        # Webhook to JEDYNY kanał niezależny od Graph i od AAD, a najważniejsze alerty powstają
        # dokładnie wtedy, gdy tamte nie działają: utrata sesji, tripwire cross-user, nieudany
        # zapis do grafiku, sufit stronicowania. Bez adresu `send_alert` zwraca `False` i nie
        # zostawia nawet śladu w logu — cała konstrukcja alertowania degraduje się do zera po
        # cichu. Rezygnacja ma być JAWNA, bo inaczej nie da się jej odróżnić od przeoczenia.
        if not self.dry_run and not self.alert_webhook_url and not self.alerty_wylaczone:
            raise ConfigError(
                "alert_webhook_url jest wymagane, gdy dry_run=false (alerty o awariach usługi). "
                f"Świadoma rezygnacja: {_PREFIX}ALERTY_WYLACZONE=true"
            )
        # Sprawdzane ZAWSZE, nie tylko przy dry_run=false: alerty celowo działają także w trybie
        # próbnym (patrz `runtime.service._send_summary`), więc adres po `http` wystawiłby token
        # w ścieżce jawnym tekstem również tam. W komunikacie tylko schemat — reszta bywa sekretem.
        if self.alert_webhook_url and not self.alert_webhook_url.startswith("https://"):
            # Do komunikatu trafia WYŁĄCZNIE schemat, i to tylko wtedy, gdy adres w ogóle go ma.
            # Poprzednia wersja robiła `split('://')[0]`, co przy adresie bez schematu (typowa
            # literówka: `przyklad.com/hook?token=…`) zwracało CAŁY adres — czyli wypisywała
            # sekret do `docker compose logs` dokładnie tam, gdzie miała go chronić.
            schemat = (
                self.alert_webhook_url.split("://", 1)[0]
                if "://" in self.alert_webhook_url
                else "brak schematu"
            )
            raise ConfigError(
                f"alert_webhook_url musi zaczynać się od https:// (adres bywa sekretem): "
                f"{schemat!r}"
            )

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            client_id=_get("CLIENT_ID"),
            tenant_id=_get("TENANT_ID"),
            team_id=_get("TEAM_ID"),
            scheduling_group_id=(_get("SCHEDULING_GROUP_ID") or None),
            token_cache_path=_path("TOKEN_CACHE", _DEFAULT_TOKEN_CACHE),
            state_path=_path("STATE_PATH", _DEFAULT_STATE),
            run_weekday=_int("RUN_WEEKDAY", 4),
            run_hour=_int("RUN_HOUR", 16),
            run_minute=_int("RUN_MINUTE", 0),
            timezone=_get("TIMEZONE", "Europe/Warsaw"),
            reply_deadline_offset_h=_int("REPLY_DEADLINE_OFFSET_H", 5),
            reply_min_hours=_int("REPLY_MIN_HOURS", 24),
            terminal_retain_hours=_int("TERMINAL_RETAIN_HOURS", 48),
            cisza_od_h=_int("CISZA_OD_H", 20),
            cisza_do_h=_int("CISZA_DO_H", 7),
            send_expiry_message=_bool("SEND_EXPIRY_MESSAGE", True),
            poll_interval_s=_int("POLL_INTERVAL_S", 10),
            poll_max_interval_s=_int("POLL_MAX_INTERVAL_S", 3600),
            self_fill_check_min_idle_s=_int("SELF_FILL_CHECK_MIN_IDLE_S", 3600),
            catchup_grace_hours=_int("CATCHUP_GRACE_HOURS", 6),
            sufit_wpisu_bez_odczytu_h=_int("SUFIT_WPISU_BEZ_ODCZYTU_H", 144),
            dry_run=_bool("DRY_RUN", True),
            only_user_ids=_list("ONLY_USER_IDS"),
            pilotaz=_bool("PILOTAZ", False),
            llm_model=_get("LLM_MODEL", "claude-haiku-4-5"),
            anthropic_api_key=klucz_modelu(),
            # Obie nazwy SIĘ SUMUJĄ (nie nadpisują): pozycje z `ADMIN_USER_IDS` idą pierwsze,
            # duplikaty odpadają. Stara skalarna `ADMIN_USER_ID` musi nadal działać, bo instalacja
            # u klienta ma dziś właśnie ją, a jej ciche zignorowanie zgasiłoby dead man's switch
            # bez śladu — czyli zepsułoby dokładnie to, co ta zmiana miała wzmocnić. Deduplikacja,
            # bo migracja przechodzi przez stan z obiema zmiennymi, a duplikat na liście adresatów
            # to druga identyczna wiadomość.
            admin_user_ids=_bez_dubli(_list("ADMIN_USER_IDS") + _list("ADMIN_USER_ID")),
            alert_webhook_url=_get("ALERT_WEBHOOK_URL"),
            alerty_wylaczone=_bool("ALERTY_WYLACZONE", False),
            loguj_nazwiska=_bool("LOGUJ_NAZWISKA", False),
            heartbeat_interval_h=_int("HEARTBEAT_INTERVAL_H", 24),
            auth_failure_exit_delay_s=_int("AUTH_FAILURE_EXIT_DELAY_S", 600),
            health_max_age_s=_int("HEALTH_MAX_AGE_S", 900),
            run_deadline_s=_int("RUN_DEADLINE_S", 1800),
        )
