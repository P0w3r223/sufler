"""Kontener-wykonawca (ADR 0057): serwer gniazda unix uruchamiający polecenia powłoki.

Osobny proces w osobnym kontenerze, wpięty tym samym obrazem co reszta floty (ADR 0044 —
jeden obraz, wiele entrypointów). Wykonawca ma montaże workspace'u i NIE MA sieci
(``network_mode: none``), więc kod pisany przez model nie ma dokąd wynieść danych.

Protokół jest celowo trywialny: jedno połączenie = jedno polecenie, żądanie i odpowiedź to
po jednej linii JSON. Bez sesji, bez stanu, bez strumieniowania — nie ma czego zsynchronizować
między procesami, a zerwane połączenie nie zostawia śladu poza zabitym procesem potomnym.

Granica zaufania biegnie na gnieździe: po jednej stronie jest aplikacja (wysyła polecenia
ułożone przez model), po drugiej powłoka. Wykonawca NIE ocenia treści polecenia — ocenianie
napisu powłoki jest zawodne, a bezpieczeństwo bierze się tu z tego, czego w kontenerze NIE MA
(sieci, zapisywalnych notatek, sekretów), nie z filtrowania.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
from pathlib import Path

# Wykonawca jest POSIX-only: gniazda unix i grupy procesów nie istnieją na Windows, a jedynym
# miejscem jego uruchomienia jest kontener. Rozgałęzienie po ``sys.platform`` (nie ``os.name``)
# jak w ``single_instance.py`` — mypy analizuje wtedy wyłącznie gałąź bieżącej platformy,
# zamiast zgłaszać brak ``AF_UNIX``/``SIGKILL`` w stubach Windows.
if sys.platform == "win32":  # pragma: no cover — nieosiągalne w obrazie
    raise ImportError("workmate-exec działa wyłącznie na POSIX (gniazda unix, grupy procesów).")

logger = logging.getLogger(__name__)

# Sufit wyjścia ODDAWANEGO modelowi. Wyjście narzędzia wraca do kontekstu i jest odsyłane
# w każdej kolejnej turze (bezstratny replay), więc `cat` wielkiego pliku kosztowałby do końca
# rozmowy. Przycięcie jest oznaczane flagą, żeby model wiedział, że patrzy na fragment.
_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_S = 60.0
_MAX_TIMEOUT_S = 300.0
# DOLNA granica limitu polecenia. ``timeout_s`` przychodzi od modelu, a ``min(t, _MAX)`` bez podłogi
# przepuszczał wartość UJEMNĄ: ``communicate(timeout=-5)`` zgłasza ``TimeoutExpired`` natychmiast,
# więc każde polecenie ginęło od razu, z wynikiem nieodróżnialnym od realnego przekroczenia czasu.
_MIN_TIMEOUT_S = 1.0
# Sufit linii żądania — zabezpiecza przed wyczerpaniem pamięci przez zepsutego klienta.
_MAX_REQUEST_BYTES = 1024 * 1024
# Sufit oczekiwania w JEDNYM połączeniu (jak ``exec_manager_server``). Gniazdo zaakceptowane NIE
# dziedziczy timeoutu nasłuchu, więc bez tego klient, który się łączy i milczy, wieszał WĄTEK
# i deskryptor bez końca — a wątków tu przybywa po jednym na połączenie.
_CONN_TIMEOUT_S = 60.0
_SOCKET_PATH = Path(os.environ.get("WORKMATE_EXEC_SOCKET", "/var/run/workmate/exec.sock"))
_DEFAULT_CWD = Path(os.environ.get("WORKMATE_EXEC_CWD", "/home/scratchpad"))


def _dodatni_z_env(nazwa: str, domyslna: int) -> int:
    """Liczba dodatnia ze środowiska; zła wartość → domyślna, bo wykonawca ma wstać.

    Wykonawca startuje bez operatora przy klawiaturze (stawia go menedżer, ADR 0012), więc
    literówka w limicie ma dać limit domyślny, a nie kontener, który nie wstaje — i przez to
    rozmowę bez powłoki, której przyczyny nikt nie widzi.
    """
    try:
        wartosc = int(os.environ.get(nazwa, ""))
    except ValueError:
        return domyslna
    return wartosc if wartosc > 0 else domyslna


# ── Granice zużycia polecenia (ADR infra 0013) ──────────────────────────────────────────────
# Wykonawca ograniczał dotąd wyłącznie WYJŚCIE (64 kB) i CZAS (60 s / max 300 s). Nie ograniczał
# niczego, co polecenie ZOSTAWIA: `dd if=/dev/zero of=x bs=1M count=100000` biegł do wyczerpania
# wolumenu, a `:(){ :|:& };:` do wyczerpania tablicy procesów.
#
# Sufit pliku jest CELOWO tą samą liczbą, co kwota katalogu roboczego dla narzędzi
# (`WORKMATE_WORKSPACE_MAX_FILE_MB`, domyślnie 5 MB). Powłoka i narzędzia piszą w to samo
# miejsce; dwa różne sufity na jeden katalog byłyby dwoma źródłami prawdy do rozjechania.
_MAX_FILE_MB = _dodatni_z_env("WORKMATE_EXEC_MAX_FILE_MB", 5)
_MAX_OPEN_FILES = _dodatni_z_env("WORKMATE_EXEC_MAX_OPEN_FILES", 256)
# Bezpiecznik czasu procesora — patrz prolog niżej. DWUKROTNOŚĆ maksymalnego czasu ściennego,
# żeby nigdy nie wyprzedził timeoutu w poleceniu pierwszoplanowym: ma łapać wyłącznie proces,
# który wyszedł z grupy i którego nie dosięga już nic innego. Podstawialny, żeby sonda mogła
# zmierzyć zachowanie bez czekania dziesięciu minut.
_CPU_BACKSTOP_S = int(2 * _MAX_TIMEOUT_S)
# ŚWIADOMIE NIE MA TU `ulimit -u` (RLIMIT_NPROC), choć bomba widłowa jest dokładnie tym, przed
# czym rlimity mają bronić. Powód: `RLIMIT_NPROC` liczy się per (przestrzeń użytkowników, UID)
# i obejmuje WĄTKI, a cała flota biegnie na tym samym uid 10001 bez remapowania przestrzeni
# użytkowników. Budżet byłby więc JEDEN, wspólny dla aplikacji, mostu GitHub i wszystkich
# wykonawców — a `PidsLimit` (128 per kontener) pozwala JEDNEJ rozmowie go wyczerpać. Skutkiem
# nie byłaby izolacja, tylko jej odwrotność: polecenia POZOSTAŁYCH rozmów zaczynałyby padać na
# `fork: Resource temporarily unavailable` z powodu, którego w ich kontenerze nie widać —
# i to na czas do zabicia grupy, czyli nawet 300 s.
# Bombę widłową zatrzymuje `PidsLimit` — jedyna z tych dwóch granic, która jest per kontener.

# Limity zakłada BUILTIN `ulimit` powłoki, nie `preexec_fn`. Powód jest wykonawczy, nie
# estetyczny: `preexec_fn` biegnie w dziecku po `fork` w procesie, który MA WĄTKI (wątek na
# połączenie), a dokumentacja `subprocess` nazywa to wprost niebezpiecznym — dziecko może zastać
# zamek trzymany przez inny wątek i zawisnąć przed `exec`. `ulimit` nakłada te same rlimity,
# ale robi to już w dziecku, po `exec`, więc żaden zamek rodzica go nie dotyczy.
#
# Polecenie modelu jedzie jako `$0`, nie w treści skryptu: zewnętrzna powłoka nigdy go nie parsuje,
# więc żaden cudzysłów ani `;` nie ma jak zmienić prologu. `&&` sprawia, że nieudany `ulimit`
# ZATRZYMUJE polecenie zamiast puszczać je bez limitów — kierunek awarii, o który tu chodzi.
# `exec` zostawia jeden proces zamiast dwóch, więc zabicie grupy działa dokładnie jak dotąd.
#
# **Sufit CZASU PROCESORA jest tu BEZPIECZNIKIEM, nie granicą polecenia — i to rozróżnienie
# jest całą treścią tej wartości.** Pierwsza wersja ustawiała go równo z sufitem czasu ściennego
# i tłumaczyła przekroczenie na zdanie dla modelu. Nie działało to na dwa sposoby naraz:
# `ulimit -t N` bez `-S`/`-H` ustawia oba sufity na tę samą wartość, a przy `soft == hard` jądro
# wysyła od razu `SIGKILL` zamiast `SIGXCPU` (zmierzone: pętla CPU przy `-t 2` wraca z `-9`);
# a nawet po rozdzieleniu sufitów sygnał nie dochodzi, bo przy kwocie `NanoCpus` jednego rdzenia
# sekunda procesora kosztuje co najmniej sekundę zegara — timeout ścienny wyczerpuje się pierwszy.
#
# Zdjęcie sufitu w całości byłoby jednak przesadą w drugą stronę, bo zostawia bez granicy
# JEDYNY przypadek, którego nie łapie nic innego: proces, który sam wyszedł z grupy procesów
# (`setsid`, wewnętrzna demonizacja). Dla niego `communicate(timeout=…)` nie ma zastosowania
# (potoki zamknięte, polecenie wraca natychmiast), `killpg` nie sięga — `setsid()` przenosi proces
# do NOWEJ sesji i nowej grupy, co widać wprost (`ps` pokazuje własne SID i PGID) — a `PidsLimit`
# liczy procesy, nie cykle. Rlimity dziedziczy się przez `fork`, a `setsid` ich nie zeruje, więc
# sufit czasu procesora sięga tam, gdzie nie sięga nic innego.
#
# Uczciwie o pomiarze: przeżycie takiego procesu odtworzył PRZEGLĄD (pętla CPU wciąż żywa po
# turze, `ps` z narastającym TIME); w harnessie testowym tego repozytorium uciekinier ginie
# i nie udało się tego uczynić powtarzalnym, więc sondy ZACHOWANIA tu nie ma — byłaby migotliwa.
# Sonda pilnuje niezmiennika, który da się sprawdzić: bezpiecznik stoi POWYŻEJ maksymalnego
# czasu ściennego. Cena utrzymania tego sufitu jest zerowa, a bez niego górnym ograniczeniem
# szkody byłby dopiero reaper wykonawcy (900 s rdzenia na rozmowę).
#
# Stąd wartość: `_CPU_BACKSTOP_S`, WYRAŹNIE POWYŻEJ maksymalnego czasu ściennego, żeby nigdy nie
# wyprzedziła ścieżki `timed_out` — i bez komunikatu dla modelu, bo w normalnym poleceniu ten
# sufit z definicji nie pada. Zarzut „martwego kodu" dotyczył KOMUNIKATU, nie limitu.
_PROLOG_LIMITOW = (
    "ulimit -S -t {cpu_s} && ulimit -H -t {cpu_s} && "
    'ulimit -f {fsize_kb} -n {nofile} && exec /bin/bash -c "$0"'
)

# Sygnały, którymi jądro melduje przekroczenie rlimitu. Surowy kod wyjścia `-25` nie mówi modelowi
# nic — a granica, której model nie rozumie, wygląda jak defekt narzędzia i skłania do obchodzenia
# jej kolejnymi próbami zamiast do zmiany podejścia.
_KOMUNIKAT_LIMITU: dict[int, str] = {
    signal.SIGXFSZ: (
        f"Polecenie przekroczyło limit rozmiaru pliku ({_MAX_FILE_MB} MB na plik) i zostało "
        "zatrzymane. UWAGA: plik na dysku urwał się dokładnie na tym rozmiarze i wygląda na "
        "kompletny — usuń go, zanim cokolwiek z niego przeczytasz. Zapisz mniej albo podziel "
        "wynik na części."
    ),
}
# Zabicie SIGKILL-em bez przekroczenia czasu ściennego znaczy, że polecenie zdjął ktoś Z ZEWNĄTRZ
# procesu — na tej flocie prawie zawsze zabójca OOM cgroupy, odkąd wykonawca ma `Memory`
# (ADR infra 0013). Komunikat jest osobno od tablicy wyżej, bo zależy od `timed_out`: ten sam
# sygnał po timeoucie jest zachowaniem zamierzonym i niesie już własne pole w wyniku.
_KOMUNIKAT_ZABICIA = (
    "Polecenie zostało zdjęte z zewnątrz, bez przekroczenia limitu czasu — najczęściej przez "
    "limit pamięci kontenera. Policz to na mniejszej porcji danych albo strumieniowo."
)


def _sygnal_z_kodu(kod: int) -> int | None:
    """Numer sygnału, którym zginęło polecenie — albo ``None``, gdy zakończyło się zwyczajnie.

    DWIE postacie, bo do wykonawcy wraca kod tego, co akurat stało się procesem bezpośrednim:

    * **ujemny** — sygnał zabił proces, na który patrzy ``Popen`` (powłoka ``exec``-uje wtedy
      polecenie prosto, więc ginie ta sama pidem);
    * **``128 + N``** — powłoka polecenie ROZWIDLIŁA (przekierowanie, kilka poleceń po średniku)
      i sama zameldowała śmierć OSTATNIEGO członu swoją konwencją wyjścia.

    Pierwsza wersja tej funkcji znała tylko postać ujemną i przez to milczała dokładnie w tych
    poleceniach, w których model najczęściej pisze duży plik — z przekierowaniem. Ujemny kod
    złapała sonda, drugą postać dopiero sonda z ``2>/dev/null``.

    **Czego ta funkcja NIE widzi:** POTOKU. Kod wyjścia potoku to kod jego OSTATNIEGO członu, więc
    ``(pętla) | cat`` po zabiciu pierwszego członu wraca ``0`` — polecenie „się udało", choć
    granica zadziałała. Nie da się tego naprawić tutaj: informacja ginie w powłoce, zanim
    wykonawca cokolwiek zobaczy. Domknięcie wymagałoby `set -o pipefail` w prologu, a to zmienia
    semantykę KAŻDEGO potoku pisanego przez model — cena wyższa niż zysk.

    Cena rozpoznawania ``128 + N``: program, który SAM zwróci 153, dostanie zdanie o limicie,
    którego nie przekroczył. To konwencja powłoki, a nie odczyt jądra — biorąc ją, bierze się
    i tę niejednoznaczność. Kierunek pomyłki jest tu właściwy: zdanie o limicie przy dziwnym
    kodzie wyjścia myli mniej niż cisza po realnym przekroczeniu limitu.
    """
    if kod < 0:
        return -kod
    if kod > 128:
        return kod - 128
    return None


def _truncate(raw: bytes, budzet: int = _MAX_OUTPUT_BYTES) -> tuple[str, bool]:
    """Przytnij strumień do sufitu i zdekoduj tolerancyjnie.

    Dekodujemy z ``errors="replace"``: polecenie może wypisać dowolne bajty (plik binarny,
    zerwane UTF-8), a wywrócenie wykonawcy na ``UnicodeDecodeError`` zamieniłoby zły wynik
    jednego polecenia w awarię całej tury.

    ``budzet`` jest podstawialny, bo do ``stderr`` doklejamy jeszcze zdanie o przekroczonym
    limicie — a sufit ma obowiązywać CAŁE pole, nie treść przed doklejeniem. Bez tego wyjście
    ucięte co do bajtu na 64 kB wracało o długość komunikatu dłuższe.
    """
    if len(raw) <= budzet:
        return raw.decode("utf-8", errors="replace"), False
    return raw[:budzet].decode("utf-8", errors="replace"), True


def _resolve_cwd(requested: str) -> str | None:
    """Katalog roboczy: żądany → domyślny → katalog procesu (``None`` = dziedziczenie).

    Domyślny bywa nieobecny (wykonawca uruchomiony bez montaży, etap `test` obrazu), a
    ``Popen`` z nieistniejącym ``cwd`` rzuca ``FileNotFoundError``, którego komunikat mówi
    o katalogu, a nie o poleceniu — model dostawał wtedy błąd bez związku z tym, co zrobił.
    Dziedziczenie katalogu procesu jest zawsze poprawne, więc kończymy łańcuch na nim.
    """
    for candidate in (Path(requested) if requested else None, _DEFAULT_CWD):
        if candidate is not None and candidate.is_dir():
            return str(candidate)
    return None


def run_command(command: str, *, cwd: str = "", timeout_s: float = 0) -> dict[str, object]:
    """Uruchom polecenie w powłoce i zwróć wynik w postaci słownika protokołu.

    Proces potomny dostaje WŁASNĄ grupę procesów (``start_new_session``), żeby dało się zabić
    także jego potomków — bez tego ``sleep 999 &`` przeżywa zabicie powłoki. Po ``TimeoutExpired``
    wysyłamy sygnał do całej grupy i dopiero wtedy czytamy to, co proces zdążył wypisać.

    Grupa ginie ZAWSZE, nie tylko po timeoucie: proces odłączony od potoków wraca natychmiast
    i przeżywa turę, a wtedy obchodzi migawkę skrzynki nadawczej. Szczegóły przy samym ``finally``.
    """
    workdir = _resolve_cwd(cwd)
    # Podłoga ORAZ sufit: ``timeout_s`` układa model, a wartość ujemna (albo mikroskopijna)
    # zabijała polecenie natychmiast, dając wynik nieodróżnialny od realnego timeoutu.
    limit = min(max(timeout_s or _DEFAULT_TIMEOUT_S, _MIN_TIMEOUT_S), _MAX_TIMEOUT_S)
    prolog = _PROLOG_LIMITOW.format(
        fsize_kb=_MAX_FILE_MB * 1024, nofile=_MAX_OPEN_FILES, cpu_s=_CPU_BACKSTOP_S
    )

    proc = subprocess.Popen(  # noqa: S602 — powłoka to CEL tego narzędzia, nie przeoczenie
        ["/bin/bash", "-c", prolog, command],
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    timed_out = False
    try:
        try:
            out, err = proc.communicate(timeout=limit)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGKILL)
            out, err = proc.communicate()
    finally:
        # Grupę zabijamy ZAWSZE, nie tylko po timeoucie — i to jest granica bezpieczeństwa.
        # `nohup … >/dev/null 2>&1 &` przekierowuje strumienie, więc potoki zamykają się razem
        # z powłoką: ``communicate`` widzi EOF i wraca NATYCHMIAST z kodem 0, a potomek żyje
        # dalej. Zmierzone w obrazie: polecenie wróciło po 0,01 s, a proces w tle zapisał plik
        # trzy sekundy później. Osierocony proces jednej rozmowy mógł tak zapisać do katalogu
        # innej PO jej migawce skrzynki (``OutboxDelivery.snapshot``) — czyli obejść jedyną
        # kontrolę pochodzenia plików i opublikować treść w cudzym wątku.
        #
        # Nic się przy tym nie traci: polecenie jest synchroniczne, a cokolwiek przeżyje jego
        # zwrot, jest dla modelu i tak nieobserwowalne — wyjście zebrano, tura idzie dalej.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)

    kod = proc.returncode if proc.returncode is not None else -1
    # Przekroczona granica wraca jako SYGNAŁ, nie jako komunikat. Zdanie doklejamy do `stderr`,
    # bo tam model już patrzy, gdy polecenie się nie udało — osobne pole musiałoby wejść do
    # kontraktu protokołu i do opisu narzędzia, żeby ktokolwiek na nie spojrzał.
    #
    # Zdanie liczymy PRZED przycięciem: sufit wyjścia ma objąć całe pole, a nie treść sprzed
    # doklejenia. Zabicie po timeoucie zdania NIE dostaje — `timed_out` już to mówi, a drugi
    # komunikat o tym samym kazałby modelowi zgadywać, która przyczyna jest prawdziwa.
    sygnal = _sygnal_z_kodu(kod)
    zdanie = _KOMUNIKAT_LIMITU.get(sygnal) if sygnal is not None else None
    # Zabicie z zewnątrz rozpoznajemy WYŁĄCZNIE po kodzie UJEMNYM, choć `_sygnal_z_kodu` zna też
    # konwencję `128 + N`. Powód: `137` w pracy z powłoką znaczy zbyt wiele rzeczy — własny
    # `timeout -k`, własny `kill -9` na ostatnim członie, program zwracający tę liczbę z siebie —
    # a to zdanie wskazuje KONKRETNĄ przyczynę. Niejednoznaczność, którą przyjęliśmy przy
    # `SIGXFSZ`, jest tam znośna, bo `153` nikt normalnie nie zwraca. Zabójca OOM cgroupy trafia
    # w proces bezpośredni, a prolog robi `exec` — czyli właśnie w postać ujemną.
    if zdanie is None and kod == -signal.SIGKILL and not timed_out:
        zdanie = _KOMUNIKAT_ZABICIA
    stdout, cut_out = _truncate(out or b"")
    stderr, cut_err = _truncate(
        err or b"",
        _MAX_OUTPUT_BYTES - (len(zdanie.encode("utf-8")) + 1) if zdanie else _MAX_OUTPUT_BYTES,
    )
    if zdanie:
        stderr = f"{stderr}\n{zdanie}".lstrip("\n")
    return {
        "exit_code": kod,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": cut_out or cut_err,
        "timed_out": timed_out,
    }


def _handle(conn: socket.socket) -> None:
    """Obsłuż jedno połączenie: linia JSON → wykonanie → linia JSON.

    Każdy błąd zamieniamy na ODPOWIEDŹ z niezerowym kodem, zamiast pozwolić mu zerwać
    połączenie: klient po drugiej stronie czeka na linię, a cisza po zerwaniu wygląda dla
    niego jak zawieszenie, nie jak porażka polecenia.

    Milczącego klienta odcina ``_CONN_TIMEOUT_S`` (``TimeoutError`` jest podklasą ``OSError``,
    więc łapiemy go razem z zerwaniem) — jak w bliźniaczym ``exec_manager_server``. Bez tego
    połączenie bez ani jednej linii trzymało wątek i deskryptor do końca życia procesu.
    """
    conn.settimeout(_CONN_TIMEOUT_S)
    try:
        with conn, conn.makefile("rwb") as stream:
            line = stream.readline(_MAX_REQUEST_BYTES)
            if not line:
                return
            try:
                request = json.loads(line)
                result = run_command(
                    str(request["command"]),
                    cwd=str(request.get("cwd", "")),
                    timeout_s=float(request.get("timeout_s", 0)),
                )
            except Exception as exc:  # noqa: BLE001 — granica procesu, wszystko wraca jako wynik
                logger.warning("Żądanie odrzucone: %s", exc)
                result = {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"Wykonawca odrzucił żądanie: {exc}",
                    "truncated": False,
                    "timed_out": False,
                }
            stream.write(json.dumps(result, ensure_ascii=False).encode("utf-8") + b"\n")
            stream.flush()
    except OSError:
        logger.warning("Połączenie zerwane w trakcie obsługi — pomijam")


def serve(socket_path: Path = _SOCKET_PATH) -> None:
    """Nasłuchuj na gnieździe unix, obsługując każde połączenie w osobnym wątku.

    Wątek na połączenie, nie pula: równoległość jest ograniczona liczbą tur agenta w locie
    (rzędu jednostek), a wątek czeka głównie na proces potomny. Gniazdo tworzymy od nowa —
    pozostałość po nieczystym zamknięciu blokowałaby ``bind``.
    """
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    socket_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    # Uprawnienia gniazda są JEDYNĄ kontrolą dostępu do wykonawcy: kto może pisać do pliku
    # gniazda, ten może uruchomić polecenie. Wolumen dzielimy tylko z aplikacją.
    socket_path.chmod(0o660)
    server.listen(16)
    logger.info("Wykonawca nasłuchuje na %s", socket_path)
    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=_handle, args=(conn,), daemon=True).start()
    finally:
        server.close()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    """Entrypoint kontenera-wykonawcy: ``workmate-exec``."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve()
