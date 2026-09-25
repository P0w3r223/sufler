"""Wiersz poleceń i złożenie zależności — jedyne miejsce, które konstruuje adaptery.

Composition root usługi: tu powstają klient HTTP, klient Graph i adapter modelu, i tylko tu
czytane są argumenty wiersza poleceń oraz `.env`. Reszta obiegu (``runtime.nudge``,
``runtime.listener``, ``runtime.service``) dostaje gotowe zależności w parametrach — dzięki temu
daje się je uruchomić w teście bez sieci i bez klucza API.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from powiadomienia_teams import alerts
from powiadomienia_teams import state as st
from powiadomienia_teams.agent.anthropic_llm import AnthropicLlm
from powiadomienia_teams.agent.interpreter import (
    LlmClient,
)
from powiadomienia_teams.config import (
    ALIAS_KLUCZA_MODELU,
    NAZWA_KLUCZA_MODELU,
    ConfigError,
    Settings,
    alias_klucza_zignorowany,
    uzyte_przestarzale_nazwy,
    uzyte_usuniete_nazwy,
)
from powiadomienia_teams.graph.auth import (
    AmbiguousAccountError,
    AuthExpiredError,
    build_token_provider,
    login_interactive,
)
from powiadomienia_teams.graph.client import GraphClient
from powiadomienia_teams.graph.tylko_odczyt import GraphClientTylkoOdczyt
from powiadomienia_teams.runtime import operator
from powiadomienia_teams.runtime.budzet import BudzetPrzebiegu
from powiadomienia_teams.runtime.cisza import CiszaWstrzymalaPrzebieg
from powiadomienia_teams.runtime.listener import poll_replies
from powiadomienia_teams.runtime.nudge import run_once
from powiadomienia_teams.runtime.przeglad import raport_stanu, zbadaj_zrodlo
from powiadomienia_teams.runtime.service import run_forever, spij_z_pulsem
from powiadomienia_teams.single_instance import (
    AlreadyRunningError,
    acquire_single_instance_lock,
)

logger = logging.getLogger(__name__)
_UTC = timezone.utc


_AUTH_CHECK_ATTEMPTS = 3
_AUTH_CHECK_BACKOFF_S = 5


def _ensure_authenticated(
    settings: Settings,
    provider_factory: Callable[[Settings], Callable[[], str]] = build_token_provider,
    *,
    attempts: int = _AUTH_CHECK_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[], str]:
    """Sprawdź token na starcie — usługa nie może wejść w pętlę bez ważnego uwierzytelnienia.

    Brak ważnego tokenu: z terminalem → jednorazowe interaktywne logowanie; bez terminala (usługa)
    → instrukcja i wyjście ≠ 0, zamiast blokowania na device-code w środku pętli.

    Błąd INNY niż utrata tokenu (DNS, niedostępny ``login.microsoftonline.com``) jest transientny
    i musi być ponowiony: przy restarcie serwera kontener potrafi wstać, zanim sieć jest gotowa,
    a wyjście ≠ 0 przy `restart: unless-stopped` daje wtedy pętlę restartów zamiast spokojnego
    poczekania na sieć. Pętla ``run_forever`` jest na to odporna; ta ścieżka startowa nie była.

    BUDOWA dostawcy jest wewnątrz pętli ponowień, nie przed nią. ``msal.PublicClientApplication``
    odpytuje tenant (OIDC discovery) JUŻ PRZY KONSTRUKCJI, więc przy braku sieci wyjątek leci
    właśnie stamtąd — poza pętlą wywracał cały start śladem stosu, mimo że ponowienie by pomogło.
    Wyszło to dopiero przy uruchomieniu obrazu; testy wstrzykiwały gotowego dostawcę i nie mogły
    tego zobaczyć.
    """
    utracona: AuthExpiredError | None = None
    for attempt in range(1, attempts + 1):
        try:
            provider = provider_factory(settings)
            provider()
            return provider
        except AuthExpiredError as blad:
            utracona = blad
            break  # nie do naprawienia ponowieniem — niżej instrukcja `--login`
        except Exception as blad:
            if attempt >= attempts:
                komunikat = (
                    f"Nie udało się przygotować uwierzytelnienia po {attempts} próbach: {blad}. "
                    "Sprawdź łączność z login.microsoftonline.com oraz poprawność CLIENT_ID "
                    "i TENANT_ID."
                )
                logger.critical("%s", komunikat)
                # Alert i odczekanie DOKŁADNIE jak przy utracie sesji niżej. Ta gałąź łapie awarie
                # najbardziej prawdopodobne na serwerze klienta — brak DNS tuż po reboocie hosta,
                # zablokowany `login.microsoftonline.com` po zmianie polityki firewalla, literówkę
                # w CLIENT_ID/TENANT_ID, pełny dysk przy zapisie cache tokenu — a kończyła się
                # samym logiem. Pod `restart: unless-stopped` dawało to kontener wirujący
                # w milczeniu: jedyny kanał niezależny od AAD nie był używany właśnie wtedy, gdy
                # był jedynym działającym. Poprawka gałęzi obok (utrata sesji bez terminala) tej
                # nie objęła.
                operator.alert(
                    settings,
                    "Nie udało się uwierzytelnić przy starcie",
                    komunikat,
                    waga=alerts.KRYTYCZNY,
                )
                operator.odczekaj_przed_wyjsciem(settings, sleep)
                raise SystemExit(1) from None
            wait = _AUTH_CHECK_BACKOFF_S * attempt
            logger.warning(
                "Nie udało się sprawdzić uwierzytelnienia (próba %d/%d) — ponawiam za %ds",
                attempt,
                attempts,
                wait,
            )
            sleep(wait)

    # Dotarliśmy tu wyłącznie przez `break`, czyli po AuthExpiredError.
    if isinstance(utracona, AmbiguousAccountError):
        # Device-code NIE naprawia dwuznaczności — dołożyłby trzecie konto do cache. Człowiek musi
        # usunąć plik cache, a instrukcja jest już w treści wyjątku.
        logger.critical("%s", utracona)
        if not sys.stdin.isatty():
            operator.zglos_utrate_sesji(settings, utracona, sleep)
        raise SystemExit(1)
    if sys.stdin.isatty():
        logger.info("Brak ważnego tokenu — uruchamiam jednorazowe logowanie device-code.")
        login_interactive(settings)
        return provider_factory(settings)
    # TA SAMA obsługa co przy utracie sesji w pętli: alert + odczekanie przed wyjściem. Wcześniej
    # ta gałąź miała własne `logger.critical` + `SystemExit(1)`, przez co pod `unless-stopped`
    # operator dostawał alert TYLKO w pierwszym cyklu — każdy kolejny restart kończył się tu po
    # cichu, a kontener wirował w tempie backoffu Dockera zamiast co `auth_failure_exit_delay_s`.
    operator.zglos_utrate_sesji(
        settings,
        utracona or AuthExpiredError("brak ważnego uwierzytelnienia i brak terminala"),
        sleep,
    )
    raise SystemExit(1)


def _polecenie_jednorazowe(akcja: Callable[[], Any]) -> None:
    """Wykonaj `--once`/`--poll-once`, zamieniając awarię na czytelny komunikat zamiast śladu stosu.

    To są DOKŁADNIE te polecenia, które operator uruchamia podczas wdrożenia (kroki weryfikacyjne
    w `deploy/README-docker.md`). Pętla usługi ma własną obsługę przez `_safe_run_once`, ale
    ścieżka jednorazowa jej nie miała — nieutworzony grafik dawał 20 linii traceback, w których
    trzeba było szukać jednej istotnej. Przyczyna i tak jest już w logu: `_raise_for_status`
    zapisuje treść odpowiedzi Graph na poziomie ERROR.
    """
    try:
        akcja()
    except AuthExpiredError:
        raise  # ma własną, czytelną obsługę wyżej
    except CiszaWstrzymalaPrzebieg as odmowa:
        # Odmowa z REGUŁY, nie awaria — więc bez `critical` i bez „nie powiodło się": operator ma
        # zobaczyć, co zrobić, a nie szukać usterki. Kod wyjścia mimo to NIEZEROWY, bo przebieg się
        # nie wykonał, a zero znaczy w tym poleceniu „wykonane" (krok wdrożenia sprawdza właśnie
        # kod wyjścia). To ta sama zasada, dla której `run_once` przestało zwracać pustą listę.
        logger.warning(
            "%s Uruchom ponownie po tej godzinie albo świadomie pomiń ciszę: "
            "--once --ignoruj-cisze",
            odmowa,
        )
        raise SystemExit(1) from None
    except Exception as blad:
        logger.critical("Polecenie nie powiodło się: %s: %s", type(blad).__name__, blad)
        raise SystemExit(1) from None


def _przebieg_jednorazowy(settings: Settings, client: GraphClient, *, ignoruj_cisze: bool) -> None:
    """`--once`: przebieg, dla którego odniesienie tygodnia i chwila faktyczna to TA SAMA chwila.

    Osobna funkcja, a nie `lambda`, wyłącznie po to, żeby dało się wyliczyć chwilę raz i podać ją
    pod oba parametry. Nadrabianie zaległego terminu (jedyny przypadek, w którym te dwie chwile się
    różnią) należy do pętli usługi — `--once` uruchamia człowiek i mówi nim „teraz".
    """
    chwila = datetime.now(_UTC)
    run_once(settings, client, now=chwila, teraz=chwila, ignoruj_cisze=ignoruj_cisze)


@dataclass(frozen=True)
class Zaleznosci:
    """Gotowe adaptery usługi: klient Graph, model i wspólny budżet czasu przebiegu."""

    client: GraphClient
    llm: LlmClient
    budzet: BudzetPrzebiegu


def zbuduj_zaleznosci(
    settings: Settings,
    http: httpx.Client,
    provider: Callable[[], str],
    *,
    sleep: Callable[[float], None] = time.sleep,
    teraz: Callable[[], datetime] = lambda: datetime.now(_UTC),
) -> Zaleznosci:
    """Złóż adaptery — JEDYNE miejsce, w którym powstają, i jedyne, które o sobie wie.

    Wydzielone z ``main``, bo samo okablowanie niosło decyzje, których nie sprawdzał żaden test:
    że klient śpi PRZEZ puls (inaczej healthcheck orzeka „pętla stoi" dokładnie wtedy, gdy klient
    cierpliwie czeka na ``Retry-After``), że ostrzeżenie o suficie stronicowania idzie kanałem
    operatorskim, i że sufit czasu przebiegu jest tym samym obiektem, który przestawia pętla.
    Testy odtwarzały to okablowanie własnymi atrapami, więc zmiana tutaj mogła być zielona
    w testach i zła w produkcji.

    ``teraz`` jest wspólne dla budżetu i wołającego z tego samego powodu, dla którego pętla
    dostaje zegar parametrem: w produkcji to ta sama wartość, ale „w produkcji to jedno i to samo"
    nie jest niezmiennikiem, tylko zbiegiem okoliczności.

    """
    # Jeden budżet na proces, przestawiany przy każdym przebiegu przez `run_forever`. Powstaje TU,
    # bo klient dostaje go przy budowie, a klient żyje tyle co proces. `--once`, `--poll-once`
    # i `--proba-nasluchu` okna NIE otwierają, więc limit ich nie dotyczy: te polecenia uruchamia
    # człowiek i patrzy na wynik, a przerwanie w połowie odebrałoby mu diagnostykę.
    budzet = BudzetPrzebiegu(settings.run_deadline_s, teraz)
    return Zaleznosci(
        client=GraphClient(
            http,
            provider,
            # Czekanie na `Retry-After` (budżet do 900 s na żądanie) jest ŻYCIEM usługi, nie
            # zawisem — ale bez pulsu w środku snu healthcheck orzekłby „pętla stoi" dokładnie
            # wtedy, gdy klient czeka zgodnie z projektem.
            sleep=lambda s: spij_z_pulsem(settings, s, sleep),
            # Ostrzeżenie o zbliżaniu się do sufitu stronicowania musi wyjść kanałem, który ktoś
            # czyta — log w instalacji bez monitoringu jest równoznaczny z ciszą.
            ostrzegaj=lambda tytul, tresc: operator.alert(settings, tytul, tresc),
            # Sufit czasu na przebieg. Bez niego pojedynczy odczyt potrafi trwać godzinami (do
            # `_MAX_PAGES` żądań × budżet czekania na każde), a puls bije przez cały ten czas.
            sprawdz_czas=budzet.sprawdz,
        ),
        llm=AnthropicLlm(settings.anthropic_api_key, model=settings.llm_model),
        budzet=budzet,
    )


def zbuduj_klienta_proby(
    settings: Settings,
    http: httpx.Client,
    provider: Callable[[], str],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> GraphClientTylkoOdczyt:
    """Klient próby: to samo okablowanie co produkcyjne, bez metod piszących i bez budżetu.

    Osobna funkcja, a nie flaga w ``zbuduj_zaleznosci``, z dwóch powodów. Typ ma nieść informację
    „ten klient NIE pisze" aż do miejsca użycia — inaczej pomyłka w okablowaniu próby wychodziłaby
    dopiero na tenancie klienta. A budżet czasu jest tu niepotrzebny: próbę uruchamia człowiek
    i patrzy na wynik, więc przerwanie w połowie odebrałoby mu diagnostykę, po którą sięgnął.
    """
    return GraphClientTylkoOdczyt(
        http,
        provider,
        sleep=lambda s: spij_z_pulsem(settings, s, sleep),
        ostrzegaj=lambda tytul, tresc: operator.alert(settings, tytul, tresc),
        # Ta sama bramka co w `nudge.run_once`: treść wiadomości bota niesie nazwę powodu
        # nieobecności z tenanta, więc do logu wchodzi wyłącznie w trybie diagnostycznym.
        loguj_tresc=settings.loguj_nazwiska,
    )


def _ustawienia_proby(settings: Settings) -> Settings:
    """Konfiguracja próby: OSOBNY plik stanu, zasiany kopią produkcyjnego, i wyłączony dry-run.

    Bezpieczeństwo próby stoi na dwóch nogach i to jest ta druga. Klient
    (``GraphClientTylkoOdczyt``) odcina świat zewnętrzny, ale przebieg nasłuchu ZAPISUJE STAN:
    przesuwa watermarki, ustawia statusy, dopisuje pamięć rozmowy. Gdyby pisał po stanie
    produkcyjnym, oznaczyłby rozmowy jako obsłużone i wiadomości jako wysłane — a pracownik nie
    dostałby nigdy niczego. Próba zabrałaby wtedy ludziom prośby, których nikt nie zobaczył.

    ``dry_run`` schodzi z drogi, bo w listenerze znaczy „nie wchodź tu wcale" — a wejście w tę
    ścieżkę jest całym celem. Obietnica „nic nie zostanie wysłane ani zapisane" jest w próbie
    utrzymana MOCNIEJ niż przez flagę: nie przez wczesny return, tylko przez brak metod, którymi
    dałoby się cokolwiek wysłać.
    """
    sciezka = settings.state_path.with_name(settings.state_path.stem + "-proba.json")
    st.save_state(sciezka, st.load_state(settings.state_path))
    # Cisza WYŁĄCZONA dla próby: klient próby ma odcięte metody piszące, więc nie ma tu przed czym
    # chronić — a bez tego próba uruchomiona po 20:00 kończyła się zdaniem „nikt w tej chwili nie
    # czekał na obsługę", które byłoby nieprawdą: to nie pracownicy milczeli, tylko obieg się nie
    # zaczął. To polecenie stoi w sekwencji wdrożenia i musi mówić prawdę o tym, co sprawdziło.
    return replace(settings, state_path=sciezka, dry_run=False, cisza_od_h=0, cisza_do_h=0)


def _proba_nasluchu(settings: Settings, client: GraphClientTylkoOdczyt, llm: LlmClient) -> None:
    """Jeden obieg nasłuchu na ŻYWYM tenancie i żywym modelu, bez skutków na zewnątrz.

    Wynikiem nie jest „przeszło bez wyjątku", tylko lista czynności, które wydarzyłyby się
    naprawdę. To jedyna forma odpowiedzi na pytanie „czy nasłuch działa u tego klienta", jaką
    da się uzyskać przed wpuszczeniem usługi do grafiku.
    """
    ustawienia = _ustawienia_proby(settings)
    logger.info(
        "PRÓBA NASŁUCHU: odczyty idą do prawdziwego Graph i modelu, zapisy i wiadomości są "
        "blokowane. Stan produkcyjny nietknięty — próba pisze do %s",
        ustawienia.state_path,
    )
    wynik = poll_replies(ustawienia, client, llm)
    logger.info(
        "Próba zakończona: %d otwartych rozmów w stanie, %d zablokowanych czynności.",
        wynik.open_count,
        len(client.zablokowane),
    )
    for czynnosc, szczegol in client.zablokowane:
        logger.info("  • %s — %s", czynnosc, szczegol)
    if not client.zablokowane:
        logger.info(
            "Nic nie było do wysłania ani zapisania. To NIE dowodzi, że ścieżka zapisu działa — "
            "dowodzi, że nikt w tej chwili nie czekał na obsługę."
        )


def _wypisz_stan(settings: Settings) -> None:
    """Wypisz raport diagnostyczny stanu — jedyne polecenie, które NIE bierze blokady instancji.

    Blokada broni przed dwoma procesami PISZĄCYMI ten sam stan (N17). To polecenie wyłącznie
    czyta, a uruchamia się je zwykle wtedy, gdy usługa pracuje — żądanie blokady zamieniłoby
    jedyne okno diagnostyczne w komunikat „już działa", czyli odebrałoby narzędzie dokładnie
    w sytuacji, dla której powstało. Odczyt w trakcie zapisu jest bezpieczny, bo zapis podmienia
    plik JEDNĄ operacją (``state._zapisz``): widać albo poprzednią wersję, albo nową, nigdy połowę.

    Wynik idzie na stdout przez ``print``, a nie loggerem: to raport do przeczytania i przeklejenia
    do zgłoszenia, a prefiks z datą i poziomem przed każdą linią tabeli czyniłby go nieczytelnym.
    Log zostaje kanałem zdarzeń, nie raportów.
    """
    # Fakty o plikach USTALAMY PRZED odczytaniem zegara. Przy odwrotnej kolejności usługa
    # zapisująca stan w tym oknie (nasłuch tyka co 10 s) dawała `mtime` późniejszy niż „teraz",
    # a raport wysyłał operatora na diagnozę zegara hosta, którego nic nie dotyczyło.
    #
    zrodlo = zbadaj_zrodlo(settings.state_path)
    try:
        stan = st.load_state(settings.state_path)
    except st.StateUnreadableError as blad:
        # Plik JEST, ale nie da się go przeczytać — to jest odpowiedź na pytanie operatora, a nie
        # awaria do zgłoszenia śladem stosu. Kod 1: „nie udało się", w odróżnieniu od 2 („źle
        # skonfigurowane"), spójnie z resztą poleceń.
        logger.critical("%s", blad)
        raise SystemExit(1) from None
    # `raport_stanu` liczy pominięte wpisy jako `wpisow_w_pliku - len(stan)`, a te dwie liczby
    # pochodzą z DWÓCH odczytów pliku. `--stan` uruchamia się przy PRACUJĄCEJ usłudze (N17), więc
    # zapis między nimi jest legalny i wywraca różnicę w obie strony: zapis dokładający wpis daje
    # różnicę ujemną (raport milczy — nieszkodliwe), a sprzątanie terminalnych
    # (`reminders/lifecycle.py::prune_terminal`) daje różnicę DODATNIĄ, czyli „UWAGA: N wpis(ów)
    # pominięto jako nieczytelne" o pliku, w którym nie ma ani jednego złego wpisu.
    #
    # Zamykamy to, obejmując odczyt stanu drugim odczytem faktów: gdy licznik się rozjechał, ktoś
    # pisał w międzyczasie i uczciwą odpowiedzią jest „nie wiem", a nie liczba wzięta z dwóch
    # różnych chwil. `wpisow_w_pliku=None` już znaczy dokładnie to i raport to rozumie — sam
    # warunek ostrzeżenia jest na `is not None`. Cena: drugie `zbadaj_zrodlo`, czyli trzy odczyty
    # małych plików (`daje_sie_odczytac` dla pliku i kopii + `_wpisow_w_pliku`) w poleceniu
    # diagnostycznym uruchamianym ręcznie. Nazwana wprost, bo „jeden odczyt" stało tu do 0.2.18
    # i było zaniżone o rząd.
    if zbadaj_zrodlo(settings.state_path).wpisow_w_pliku != zrodlo.wpisow_w_pliku:
        zrodlo = replace(zrodlo, wpisow_w_pliku=None)
    print(
        raport_stanu(
            stan,
            datetime.now(_UTC),
            zrodlo=zrodlo,
            okno=settings.okno_odpowiedzi,
            z_nazwiskami=settings.loguj_nazwiska,
        )
    )


def ostrzezenia_startowe(settings: Settings) -> list[str]:
    """Zdania, które start ma powiedzieć o konfiguracji PRZEPUSZCZONEJ przez `validate()`.

    Wszystkie opisują ustawienia dozwolone i zarazem groźne: rezygnację z jedynego kanału wołania
    człowieka, dead man's switch z jednym adresatem, wycofywane i USUNIĘTE nazwy zmiennych, termin
    odpowiedzi, do którego godzina przebiegu nie zostawia miejsca. `validate()` ich nie zatrzyma —
    instalacja z własnym monitoringiem ma prawo nie mieć webhooka, stara nazwa u klienta ma prawo
    działać, a przebieg wolno przestawić na dowolną godzinę — więc jedynym miejscem, w którym
    ktokolwiek się o nich dowie, jest log startowy.

    Funkcja zwraca zdania zamiast je logować, bo inaczej cała ta warstwa jest nietestowalna:
    siedziała w środku `main()`, za walidacją, blokadą instancji i uwierzytelnieniem. Skasowanie
    któregokolwiek `if` nie psuło ani jednego testu. Bez efektów ubocznych, ale **nie bez wejść
    ukrytych**: dwa zdania powstają z `os.environ`, bo dotyczą NAZW zmiennych, a `Settings` jest
    snapshotem samych wartości i nazwy, spod której przyszły, już nie zna.
    """
    zdania: list[str] = []
    if settings.alerty_wylaczone:
        if settings.alert_webhook_url:
            zdania.append(
                "Ustawiono JEDNOCZEŚNIE adres webhooka i ALERTY_WYLACZONE=true — wygrywa "
                "wyłączenie, alerty NIE będą wysyłane."
            )
        else:
            zdania.append(
                "Alerty wyłączone świadomie — awarie usługi będą widoczne wyłącznie w logu."
            )
    # Alias klucza wypada z tej listy, gdy jego wartość jest ignorowana: „przenieś wartość do…"
    # radziłoby wtedy nadpisać klucz OBOWIĄZUJĄCY wartością właśnie odrzuconą. Jedno zdanie o tej
    # zmiennej, nie dwa idące w przeciwne strony.
    pomin = {NAZWA_KLUCZA_MODELU} if alias_klucza_zignorowany() else set()
    zdania += [
        f"Użyto wycofywanej nazwy {stara} — przenieś wartość do {nowa}."
        for stara, nowa in uzyte_przestarzale_nazwy()
        if nowa not in pomin
    ]
    # Osobno od pętli wyżej: tam wartość DZIAŁA i trzeba ją kiedyś przenieść, tu jest ignorowana
    # już teraz. Bez tego zdania rotacja klucza wpisana pod starą nazwą wygląda na wykonaną,
    # a usługa dalej używa poprzedniego — czyli błąd, który ujawnia się dopiero przy odcięciu
    # starego klucza, w środku tygodnia i po stronie pracownika.
    if alias_klucza_zignorowany():
        zdania.append(
            f"Ustawiono JEDNOCZEŚNIE {NAZWA_KLUCZA_MODELU} i {ALIAS_KLUCZA_MODELU} — wygrywa "
            f"nazwa kanoniczna, wartość spod {ALIAS_KLUCZA_MODELU} jest IGNOROWANA. "
            f"Usuń jedną z nich."
        )
    # Wartość spod usuniętej nazwy nie działa i nie ma jak zadziałać — a operator, który ją wpisał,
    # ma prawo myśleć, że zmienił ludziom termin. To jedyne miejsce, w którym się o tym dowie.
    zdania += [f"{nazwa} nie jest już czytana: {powod}." for nazwa, powod in uzyte_usuniete_nazwy()]
    # Termin kalendarzowy i godzina przebiegu są konfigurowane OSOBNO, więc mogą się rozjechać tak,
    # że termin wypada przed przebiegiem albo zaraz po nim. Wtedy o wygaśnięciu decyduje wyłącznie
    # dolna granica kurtuazji, czyli okno odpowiedzi milcząco przestaje być kalendarzowe — a to
    # widać w konfiguracji tylko wtedy, gdy się te dwie liczby odejmie.
    godzin = settings.godzin_od_przebiegu_do_terminu
    if godzin < settings.reply_min_hours:
        zdania.append(
            f"Od przebiegu do terminu odpowiedzi jest {godzin:.0f} h, a dolna granica kurtuazji to "
            f"{settings.reply_min_hours} h — o wygaśnięciu decyduje więc TYLKO ta granica, "
            f"a REPLY_DEADLINE_OFFSET_H nie ma wpływu. Sprawdź RUN_WEEKDAY/RUN_HOUR "
            f"i REPLY_DEADLINE_OFFSET_H."
        )
    # Godzina przebiegu wewnątrz okna ciszy jest konfiguracją LEGALNĄ (`validate()` sprawdza tylko
    # zakres 0..23), a kosztuje najwięcej, co ta usługa może kosztować: przebieg jest wtedy CO
    # TYDZIEŃ
    # odkładany do końca ciszy. Bez tego zdania jedynym śladem jest linia w logu kontenera — a
    # pozycja
    # A12 zakłada, że logów nikt nie czyta.
    #
    # Zdanie mówi o ZERZE, nie o „krótkim" oknie łaski, i to jest poprawka po przeglądzie: okno
    # łaski
    # nie liczy godzin ciszy (`_catchup_due` odejmuje `cisza_pomiedzy`), więc między terminem
    # a końcem ciszy budżet nie jest zużywany wcale i wystarcza każda wartość dodatnia. Rada
    # „podnieś CATCHUP_GRACE_HOURS" naprawiałaby coś, co nie jest zepsute, i odwracała uwagę od
    # jedynej wartości, która faktycznie kosztuje tydzień.
    if settings.godzina_przebiegu_w_ciszy:
        zdania.append(
            f"Przebieg tygodniowy wypada o {settings.run_hour:02d}:"
            f"{settings.run_minute:02d}, czyli "
            f"w godzinach ciszy ({settings.cisza_od_h}:00–{settings.cisza_do_h}:00) — będzie co "
            f"tydzień ODKŁADANY do jej końca. Przy CATCHUP_GRACE_HOURS=0 nadrabianie jest "
            f"wyłączone, "
            f"więc nie wykona się w żadnym tygodniu. Przestaw RUN_HOUR albo CISZA_OD_H/CISZA_DO_H."
        )
    if len(settings.admin_user_ids) == 1:
        zdania.append(
            "Podsumowanie tygodniowe ma JEDNEGO adresata — w instalacji bez monitoringu brak tej "
            "wiadomości bywa jedynym sygnałem awarii, a przy jednym odbiorcy milczy przez całą "
            "jego nieobecność. Dopisz drugą osobę do ADMIN_USER_IDS."
        )
    return zdania


def _wczytaj_env() -> None:
    """Wczytaj `.env` z KATALOGU ROBOCZEGO, jeśli jest.

    Ścieżka jawnie, bo ``load_dotenv()`` bez argumentu szuka od katalogu pliku wywołującego, a nie
    od CWD. Dla zainstalowanego polecenia ``powiadomienia-teams`` oznaczało to przeszukiwanie
    katalogu pakietu — więc README obiecywał „program czyta `.env` przy starcie", a uruchomienie
    z katalogu z konfiguracją kończyło się „Brak wymaganych ustawień". ``scripts/lista_czlonkow.py``
    ma tę samą poprawkę od początku; tutaj jej brakowało.

    Na serwerze konfiguracja i tak przychodzi ze środowiska (``env_file`` w compose), więc brak
    `.env` nie jest błędem. Zmienne JUŻ USTAWIONE w środowisku mają pierwszeństwo (domyślne
    ``override=False``) — dzięki temu da się wymusić np. ``POWIADOMIENIA_DRY_RUN=true`` niezależnie
    od zawartości pliku.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    env = Path.cwd() / ".env"
    if env.exists():
        load_dotenv(env)


# Sufit funkcji przekroczony ŚWIADOMIE: router poleceń. Rozbicie znaczy rozdzielenie parsowania
# argumentów od budowy zależności, a te dwie rzeczy dziś rozstrzygają się nawzajem (`--login`
# buduje inny klient niż `--stan`). Dług, nie usprawiedliwienie.
def main() -> None:  # noqa: PLR0915
    parser = argparse.ArgumentParser(
        description="Cotygodniowe przypomnienia o zmianach (Microsoft Shifts)."
    )
    parser.add_argument(
        "--once", action="store_true", help="jeden przebieg powiadomień teraz i wyjście"
    )
    parser.add_argument(
        "--poll-once", action="store_true", help="jedno sprawdzenie odpowiedzi i wyjście"
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="jednorazowe interaktywne logowanie (device-code) i wyjście",
    )
    parser.add_argument(
        "--stan",
        action="store_true",
        help="wypisz raport diagnostyczny pliku stanu i wyjdź (bez sieci, bez blokady instancji)",
    )
    parser.add_argument(
        "--ignoruj-cisze",
        action="store_true",
        help="pozwól `--once` pisać do pracowników w godzinach ciszy (nadrabianie po awarii); "
        "`--poll-once` ciszy nie podlega z zasady, bo odpowiada ludziom, którzy właśnie napisali",
    )
    parser.add_argument(
        "--proba-nasluchu",
        action="store_true",
        help="próba: jeden obieg nasłuchu na żywym Graphie i modelu, BEZ zapisów do grafiku "
        "i bez wiadomości do pracowników; stan produkcyjny nietknięty",
    )
    args = parser.parse_args()
    if args.ignoruj_cisze and not args.once:
        # Milczące zignorowanie flagi jest tu najgorszym wariantem: operator nadrabiający po awarii
        # zobaczyłby „nie wysłano nic" i nie miał z czego wywnioskować, że jego flaga nic nie
        # znaczy.
        parser.error(
            "--ignoruj-cisze działa wyłącznie z --once. `--poll-once` ciszy nie podlega z zasady "
            "(odpowiada ludziom, którzy właśnie napisali), a `--proba-nasluchu` nic nie wysyła."
        )

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _wczytaj_env()

    # `from_env()` JEST w tym samym `try`: parsowanie wartości też potrafi zgłosić `ConfigError`
    # (`_int`, `_bool`), a literówka w liczbie albo w wartości logicznej to dokładnie ta sama
    # pomyłka operatora co brak zmiennej. Poza `try` kończyła się śladem stosu zamiast jednym
    # zdaniem — czyli najgorzej w miejscu, w którym człowiek czyta `docker compose logs`.
    #
    # `--login` i `--stan` sprawdzamy WĘŻEJ: pełna lista kontrolna to lista warunków wysyłki,
    # a żadne z tych dwóch poleceń niczego nie wysyła — za to oba bywają krokiem, który dopiero
    # UMOŻLIWIA naprawę reszty `env` (identyfikatory do `ONLY_USER_IDS` wypisuje
    # `scripts/lista_czlonkow.py`, a ten wymaga tokenu; `--stan` odpowiada na pytanie, co usługa
    # zdążyła zapisać, zanim padła na konfiguracji). Bez tego rozróżnienia bramka konfiguracji
    # blokuje jedyne drogi do danych, których żąda — a diagnostyka jest potrzebna właśnie wtedy,
    # gdy pełna walidacja nie przechodzi.
    try:
        settings = Settings.from_env()
        if args.login or args.stan:
            settings.validate_dostep()
        else:
            settings.validate()
    except ConfigError as blad:
        # Błąd konfiguracji to pomyłka operatora, nie awaria programu. Ma dać JEDNO czytelne zdanie
        # w logu usługi, a nie ślad stosu, w którym trzeba wyławiać ostatnią linię — na serwerze
        # czyta to człowiek przez `docker compose logs`, często pod presją czasu.
        # Kod 2 odróżnia „źle skonfigurowane" od „padło w trakcie pracy" (1).
        logger.critical("Błąd konfiguracji: %s", blad)
        raise SystemExit(2) from None
    # Zanim padnie PIERWSZE żądanie HTTP — patrz `alerts.ukryj_adres_w_logach`.
    alerts.ukryj_adres_w_logach(settings.alert_webhook_url)

    if args.login:
        login_interactive(settings)
        return

    # PRZED blokadą instancji i przed uwierzytelnieniem: raport czyta plik i nic więcej, a bywa
    # potrzebny właśnie wtedy, gdy usługa pracuje (blokada zajęta) albo gdy token wygasł i pętla
    # stoi. Warunki startu usługi niżej (ostrzeżenia o alertach i adresatach) go nie dotyczą —
    # to jest polecenie diagnostyczne, nie start.
    if args.stan:
        _wypisz_stan(settings)
        return

    if settings.dry_run:
        logger.info("Tryb DRY-RUN — nic nie zostanie wysłane ani zapisane.")

    for zdanie in ostrzezenia_startowe(settings):
        logger.warning("%s", zdanie)

    # JEDNO złożenie ustawień idzie dalej niż log: godzina przebiegu w oknie ciszy RAZEM
    # z wyłączonym nadrabianiem. Wtedy przebieg jest co tydzień odkładany do końca ciszy, a nie ma
    # go czym nadrobić — usługa nie wyśle NIGDY ani jednej prośby i nie wyśle też podsumowania,
    # bo dead man's switch jest odkładany razem z przebiegiem. Cisza na wszystkich kanałach
    # naraz jest nieodróżnialna od usługi, która stoi.
    #
    # Dlaczego alert, a nie kolejne zdanie w logu: to samo miejsce mówi dwie linie wyżej, że
    # „logów nikt nie czyta" (pozycja A12) — a lekarstwem na to nie może być następna linia w logu.
    # Alert startowy jest jedynym kanałem, który operator obserwuje przed pierwszym uruchomieniem.
    if settings.godzina_przebiegu_w_ciszy and settings.catchup_grace_hours <= 0:
        operator.alert(
            settings,
            "Konfiguracja, przy której usługa nie wyśle nic",
            f"Przebieg wypada o {settings.run_hour:02d}:{settings.run_minute:02d}, czyli w oknie "
            f"ciszy ({settings.cisza_od_h}:00–{settings.cisza_do_h}:00), a CATCHUP_GRACE_HOURS=0 "
            "wyłącza nadrabianie. Przebieg będzie co tydzień odkładany i nigdy nie wykonany; nie "
            "będzie też cotygodniowego podsumowania. Ustaw CATCHUP_GRACE_HOURS na wartość dodatnią "
            "albo przestaw RUN_HOUR poza godziny ciszy.",
            waga=alerts.BLAD,
        )

    # Blokada jednej instancji: dwa procesy piszące ten sam stan obeszłyby idempotencję zapisu do
    # Shifts (np. usługa + ręczne --poll-once). Zwalnia się przy zakończeniu procesu.
    try:
        lock = acquire_single_instance_lock(settings.state_path)
    except AlreadyRunningError as exc:
        logger.critical("%s", exc)
        raise SystemExit(1) from None

    with lock:
        # Budowa dostawcy i sprawdzenie tokenu RAZEM — obie czynności odpytują sieć, więc obie
        # muszą podlegać tym samym ponowieniom (patrz `_ensure_authenticated`).
        provider = _ensure_authenticated(settings)
        with httpx.Client(timeout=30) as http:
            zaleznosci = zbuduj_zaleznosci(settings, http, provider)
            client, llm, budzet = zaleznosci.client, zaleznosci.llm, zaleznosci.budzet
            if args.once:
                # `--once` PODLEGA ciszy: wysyła prośby do ludzi, którzy o nic nie pytali, więc
                # o 23:00 jest wtargnięciem — nawet uruchomione ręcznie. Odstępstwo musi być jawne
                # (`--ignoruj-cisze`), bo operator nadrabiający po awarii wie o niej więcej niż kod.
                #
                # `--once` niczego nie nadrabia, więc odniesienie tygodnia i chwila faktyczna to
                # ta sama chwila — powiedziane WPROST, bo `run_once` nie ma już domyślnego `teraz`
                # (patrz jego docstring). Wyliczona raz: dwa osobne `datetime.now` rozjechałyby
                # się o ułamek sekundy i test czytający jedną z nich nie widziałby drugiej.
                _polecenie_jednorazowe(
                    lambda: _przebieg_jednorazowy(
                        settings,
                        client,
                        ignoruj_cisze=args.ignoruj_cisze,
                    )
                )
            elif args.poll_once:
                # `--poll-once` ciszy NIE podlega (decyzja 4.1/3): odpowiada ludziom, którzy właśnie
                # napisali. Milczenie bota po wiadomości pracownika jest gorsze niż odpowiedź
                # o nietypowej godzinie — a tę godzinę wybrał człowiek, uruchamiając polecenie.
                #
                # Wyłączenie obejmuje ODPOWIADANIE, nie domykanie: wygaszenia i podziękowania za
                # samouzupełnienie idą do ludzi, którzy nic nie napisali, więc `poll_replies`
                # zostawia je pod pierwotnym oknem ciszy i odkłada do jej końca.
                _polecenie_jednorazowe(
                    lambda: poll_replies(
                        settings,
                        client,
                        llm,
                        ignoruj_cisze=True,
                    )
                )
            elif args.proba_nasluchu:
                # Własny klient, bo próba MUSI mieć odcięte metody piszące — a nie tę samą klasę
                # z flagą, którą ktoś kiedyś pominie w nowym miejscu wywołania. Bez budżetu
                # czasu, tak samo jak `--once`/`--poll-once`: to polecenie uruchamia człowiek
                # i patrzy na wynik, a przerwanie w połowie odebrałoby mu diagnostykę.
                klient_proby = zbuduj_klienta_proby(settings, http, provider)
                _polecenie_jednorazowe(lambda: _proba_nasluchu(settings, klient_proby, llm))
            else:
                try:
                    run_forever(settings, client, llm, budzet=budzet)
                except AuthExpiredError:
                    # Alert, log CRITICAL i instrukcja poszły już z `_handle_auth_loss`. Ślad stosu
                    # przykryłby je w `docker compose logs`, a runbook każe operatorowi patrzeć
                    # właśnie tam — zatrzymujemy się tak samo czysto jak przy ConfigError.
                    raise SystemExit(1) from None


if __name__ == "__main__":
    main()
