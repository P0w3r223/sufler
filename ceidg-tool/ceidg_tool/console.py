"""Konsolowe implementacje `Events`: pasek postępu (terminal) i wiersze (wszystko inne).

Dwie, bo `rich` rysuje pośrednie klatki `Live` **wyłącznie** przy `console.is_terminal`
(`rich/live.py:269`). Poza terminalem pasek nie emituje nic aż do zatrzymania, więc wołający
bez terminala dostawał jedną klatkę na końcu półgodzinnej pracy. To jest dokładnie ta cisza,
którą CLAUDE.md nazywa defektem, tyle że po stronie, której nikt nie oglądał (ADR-0024).

**Rozstrzyga terminal na stderr**, bo konsola programu jest na stderr (decyzja 2) — czyli
harmonogram, kontener CI i podproces agenta, w których oba strumienie są rurami. Do 2026-09-24
stało tu „operator z `pobierz > log.txt`" i było to nieprawdą od chwili przeniesienia ekranów:
przy terminalu takie przekierowanie zabiera stdout, a pasek zostaje animowany na stderr, gdzie
zawsze był widoczny (przegląd kodu).

Wyboru dokonuje `cli` po `console.is_terminal`, a nie po fladze `--wynik json`: gdyby zależał
od flagi, defekt zostałby otwarty dla wszystkich, którzy agentami nie są.
"""

from __future__ import annotations

import math

from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    ProgressColumn,
    Task,
    TaskID,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

from .clock import Clock, SystemClock, local_hhmm, local_hhmmss
from .ratelimit import (
    REASON_BUDGET,
    REASON_COOLDOWN,
    REASON_NO_CONNECTION,
    REASON_RESUME,
    REASON_WINDOW,
)
from .richtext import make_console, safe

LONG_WAIT_S = 5.0
MIB = 1 << 20


class _CountColumn(ProgressColumn):
    """`ukończone/suma`, a przy nieznanej sumie `ukończone/?`.

    `TextColumn("{task.completed}/{task.total}")` drukowało przy nieznanej sumie dosłowne
    „None" — ścieżka raportu podaje sumę dopiero na ostatniej stronie, więc przez cały skan
    operator widział `1/None`. Znak zapytania mówi to samo, nie udając wartości."""

    def render(self, task: Task) -> Text:
        total = "?" if task.total is None else f"{int(task.total)}"
        return Text(f"{int(task.completed)}/{total}")


def komunikat_o_czekaniu(seconds: float, reason: str, resume_at_epoch: float) -> str | None:
    """Zdanie o postoju albo `None`, gdy postój jest za krótki, żeby o nim mówić.

    Jedna kopia reguły dla obu odbiorców. Wcześniej stała tylko w `ConsoleEvents`; przepisanie
    jej do `LineEvents` dałoby dwa egzemplarze zdania, które **musi** być identyczne, bo to
    jest to samo zdarzenie oglądane przez dwa różne wyjścia — a poprawka jednego egzemplarza
    nie dotyka drugiego (ta sama pomyłka co `richtext.safe` w dwóch miejscach, ADR-0009).

    Krótkie odstępy limitera (3,75 s) są milczące; wyjątkiem są powody, przy których postój
    bywa długi i wymaga wyjaśnienia, nawet gdy akurat trwał chwilę.
    """
    if seconds < LONG_WAIT_S and reason not in (REASON_COOLDOWN, REASON_RESUME, REASON_BUDGET):
        return None
    when = local_hhmm(resume_at_epoch)
    if reason == REASON_BUDGET:
        # Inny komunikat niż przy zwykłym oknie: tu limit zużył ktoś poza tym procesem,
        # a postój bywa długi (do końca godzinnego okna), więc operator ma wiedzieć czemu.
        return (
            f"budżet godzinny tokenu na wyczerpaniu (zużyty także poza tym pobraniem), "
            f"wznawiam o {when}"
        )
    if reason == REASON_COOLDOWN or reason == REASON_WINDOW:
        return f"limit API, wznawiam o {when}"
    if reason == REASON_NO_CONNECTION:
        return f"brak połączenia, czekam do {when}, postęp zapisany"
    if reason == REASON_RESUME:
        return f"wznowienie po przerwie, start o {when}"
    return f"czekam {seconds:.0f} s ({reason}), dalej o {when}"


class ConsoleEvents:
    """Postęp na konsoli; krótkie odstępy limitera (3,75 s) nie są raportowane."""

    def __init__(self, console: Console | None = None, *, quiet: bool = False) -> None:
        self.console = console or make_console(stderr=True)
        self.quiet = quiet
        self._progress: Progress | None = None
        self._task_pages: TaskID | None = None
        self._task_details: TaskID | None = None
        self._task_export: TaskID | None = None
        self._task_download: TaskID | None = None
        self._task_model: TaskID | None = None
        self.requests = 0

    def _progress_bar(self) -> Progress:
        if self._progress is None:
            self._progress = Progress(
                # `markup=False`: `TextColumn` formatuje opis i przepuszcza wynik przez
                # `Text.from_markup`, więc `Text` zwrócony przez `safe` zostałby z powrotem
                # zamieniony na napis i sparsowany — nazwa z `[/i]` wywróciłaby program.
                TextColumn("{task.description}", style="bold", markup=False),
                BarColumn(),
                _CountColumn(),
                # Czas, który upłynął — jedyna kolumna płynąca **zawsze**, także gdy licznik
                # stoi. Przy nieznanej sumie `TimeRemainingColumn` nic nie policzy, więc bez
                # tej kolumny asystent pokazywał nieruchome „72/?" i nie dało się odróżnić
                # myślenia od zwisu (bramka 3, 2026-09-07). Dotyczy też pobierania raportu,
                # gdzie suma bywa nieznana przy `Content-Encoding`.
                TimeElapsedColumn(),
                TimeRemainingColumn(),
                console=self.console,
                transient=False,
            )
            self._progress.start()
        return self._progress

    def close(self) -> None:
        """Kończy pasek i zapomina identyfikatory zadań — kolejna akcja w tej samej
        sesji (kreator) zaczyna od nowego paska, a nie od nieistniejącego `TaskID`."""
        if self._progress is not None:
            self._progress.stop()
        self._progress = None
        self._task_pages = None
        self._task_details = None
        self._task_export = None
        self._task_download = None
        self._task_model = None

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        self.requests += 1

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        tekst = komunikat_o_czekaniu(seconds, reason, resume_at_epoch)
        if tekst is not None:
            self.on_message(tekst)

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        if self.quiet:
            return
        progress = self._progress_bar()
        if self._task_pages is None:
            self._task_pages = progress.add_task("Lista firm", total=total or None)
        elif total and progress.tasks[self._task_pages].total != total:
            progress.update(self._task_pages, total=total)
        progress.update(self._task_pages, advance=records)

    def on_details(self, done: int, total: int) -> None:
        if self.quiet:
            return
        progress = self._progress_bar()
        if self._task_details is None:
            self._task_details = progress.add_task("Szczegóły", total=total)
        elif total and progress.tasks[self._task_details].total != total:
            # `aktualizuj` poznaje kolejne okna czasowe w trakcie, więc suma rośnie. Bez tego
            # pasek zostawał z sumą pierwszego okna i dobijał do 100 % w połowie pracy —
            # `on_page` obsługiwał to od początku, `on_details` nie.
            progress.update(self._task_details, total=total)
        progress.update(self._task_details, completed=done)

    def on_export(self, done: int, total: int) -> None:
        """Zapis skoroszytu ma własny pasek: to jedyny długi etap bez żadnego żądania.

        Zmierzone 453 firmy/s — pełne województwo z raportu (287 tys.) to ponad dziesięć
        minut, przez które eksport nie mówił nic, bo `exporter` nie znał zdarzeń."""
        if self.quiet:
            return
        progress = self._progress_bar()
        if self._task_export is None:
            self._task_export = progress.add_task("Zapis skoroszytu", total=total or None)
        elif total and progress.tasks[self._task_export].total != total:
            progress.update(self._task_export, total=total)
        progress.update(self._task_export, completed=done)

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        """Pobieranie archiwum raportu — jedno żądanie, ale 21 MB i minuty transferu.

        Licznik chodzi w pełnych MiB, bo `_CountColumn` pokazuje liczby całkowite, a bajty
        dałyby ośmiocyfrowy szum. Nieznane `Content-Length` (odpowiedź chunked) zostaje jako
        `None` i kolumna napisze `?` zamiast udawać sumę."""
        if self.quiet:
            return
        progress = self._progress_bar()
        # `//` dawało 0 dla archiwum poniżej 1 MiB, a `0 or None` zamieniało **znaną** sumę
        # w znak zapytania zarezerwowany dla nieznanej — pasek nie drgnąłby ani razu.
        total = math.ceil(total_bytes / MIB) if total_bytes else None
        if self._task_download is None:
            self._task_download = progress.add_task("Raport ZIP (MB)", total=total or None)
        elif total and progress.tasks[self._task_download].total != total:
            progress.update(self._task_download, total=total)
        progress.update(self._task_download, completed=done_bytes // MIB)

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        """Pytanie do asystenta: jedno żądanie, ale kilkanaście sekund nad 25 tys. tokenów.

        Suma jest nieznana (`total=None`), bo długości odpowiedzi nie da się przewidzieć;
        `_CountColumn` pokazuje wtedy `?`, co jest prawdą, a nie zmyśloną liczbą. Sam licznik
        tokenów nie wystarcza jako oznaka życia — znalezione przy bramce 3, że operator widzi
        nieruchome „72/?" i nie ma jak odróżnić myślenia od zwisu — więc pasek ma osobną
        `TimeElapsedColumn`, a `caller` liczy też znaki myślenia, nie tylko tekst odpowiedzi."""
        if self.quiet:
            return
        progress = self._progress_bar()
        # Przez `safe`, bo to argument niosący tekst do `rich` — reguła granic 10
        # jest strukturalna i nie zna wyjątku „ten napis akurat jest nasz".
        if self._task_model is None:
            self._task_model = progress.add_task("Asystent myśli", total=None)
        progress.update(self._task_model, completed=tokens)

    def on_message(self, text: str) -> None:
        # Komunikaty niosą nazwy z rejestru (np. nazwę raportu), więc idą jako zwykły tekst:
        # znaczniki `rich` w danych z zewnątrz wywracają program albo podszywają się pod UI.
        self.console.print(safe(text))


# Ile żądań między wierszami. Osiem, bo przy odstępie 3,75 s (`apiprofile.RateLimits.min_spacing_s`)
# to jest około 30 s pracy — ten sam rytm co `store.touch_lock()` i z tego samego powodu.
# Liczone w **żądaniach**, nigdy w stronach: strona `/zmiana` to 500 identyfikatorów, czyli do stu
# żądań, a ta pomyłka o jedną warstwę dała 2026-09-06 cztery osobne defekty.
ZADANIA_NA_LINIE = 8

# Etapy, przez które nie idzie żadne żądanie, mają własne progi — licznik żądań ich nie napędza,
# a to są najdłuższe ciche odcinki w programie. Eksport zmierzono na 453 firmy/s, więc 10 000
# wierszy to około 22 s; archiwum raportu to jedno żądanie i 21 MB transferu; odpowiedź modelu to
# jedno żądanie i kilkanaście sekund. Bez tych trzech progów wiersze milczałyby dokładnie tam,
# gdzie pasek postępu powstał po to, żeby nie milczeć.
WIERSZE_NA_LINIE = 10_000
MIB_NA_LINIE = 2
TOKENY_NA_LINIE = 2_000


class LineEvents:
    """Oznaki życia wierszami, bez `Live` — dla wszystkiego, co nie jest terminalem.

    Ten sam protokół `Events` co `ConsoleEvents` i ta sama konsola (stderr), więc `cli` wybiera
    jedno albo drugie i nic poza tym się nie zmienia. Różnica jest w sposobie pisania: pasek
    nadpisuje swoją linię i poza terminalem nie emituje pośrednich klatek, a te wiersze tylko
    dopisują, więc czyta się je w przechwyconym logu tak samo dobrze po fakcie.

    Wiersz należy się po **przekroczeniu progu**, a nie po upływie czasu: próg jest liczony
    w żądaniach i wierszach, czyli w tym, co program naprawdę robi. Zegar wchodzi tylko do
    znacznika czasu, dzięki czemu rytm jest deterministyczny i daje się zmierzyć testem.
    """

    def __init__(
        self,
        console: Console | None = None,
        *,
        quiet: bool = False,
        clock: Clock | None = None,
    ) -> None:
        self.console = console or make_console(stderr=True)
        self.quiet = quiet
        self.requests = 0
        self._clock = clock or SystemClock()
        self._etap: str | None = None
        self._zrobione = 0
        self._suma: int | None = None
        self._wiersze_listy = 0
        self._zadania_na_linii = 0
        self._zrobione_na_linii = 0

    # ------------------------------------------------------------------ wiersz

    def _linia(self) -> None:
        czesci = []
        if self._etap is not None:
            # Nieznana suma jako `?`, a nie jako „None" — ta sama decyzja co w `_CountColumn`
            # i z tego samego powodu: znak zapytania mówi prawdę, nie udając wartości.
            suma = "?" if self._suma is None else str(self._suma)
            czesci.append(f"{self._etap} {self._zrobione}/{suma}")
        czesci.append(f"zapytań: {self.requests}")
        self._zadania_na_linii = self.requests
        self._zrobione_na_linii = self._zrobione
        # Przez `safe` jak wszystko, co ten moduł podaje `rich` — reguła granic 10 jest
        # strukturalna i nie zna wyjątku „ten napis akurat jest nasz".
        self.console.print(safe(f"{self._znacznik()} {' · '.join(czesci)}"))

    def _znacznik(self) -> str:
        return f"[{local_hhmmss(self._clock.wall())}]"

    def _etap_postep(self, nazwa: str, zrobione: int, suma: int | None, *, co: int | None) -> None:
        """Stan etapu i decyzja, czy należy się wiersz.

        `co is None` znaczy „ten etap napędza licznik żądań" — lista firm i szczegóły wydają
        po żądaniu na jednostkę postępu, więc własny próg byłby drugim licznikiem tego samego.
        Zmiana etapu daje wiersz **zawsze**: przejście jest tą informacją, na którą wołający
        czeka najbardziej, a między etapami potrafi minąć więcej czasu niż jeden próg.
        """
        if self.quiet:
            return
        nowy = nazwa != self._etap
        self._etap, self._zrobione, self._suma = nazwa, zrobione, suma
        if nowy:
            self._linia()
        elif co is not None and zrobione - self._zrobione_na_linii >= co:
            self._linia()

    # ------------------------------------------------------------------ protokół

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        self.requests += 1
        # Licznik rośnie także przy `quiet`, bo to jest liczba, którą czyta koperta (`zapytania`)
        # — i ma być tą samą liczbą, która napędzała oznaki życia.
        if not self.quiet and self.requests - self._zadania_na_linii >= ZADANIA_NA_LINIE:
            self._linia()

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        tekst = komunikat_o_czekaniu(seconds, reason, resume_at_epoch)
        if tekst is not None:
            self.on_message(tekst)

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        # `records` to przyrost strony, nie suma — `ConsoleEvents` podaje go do `advance=`.
        self._wiersze_listy += records
        self._etap_postep("Lista firm", self._wiersze_listy, total, co=None)

    def on_details(self, done: int, total: int) -> None:
        self._etap_postep("Szczegóły", done, total, co=None)

    def on_export(self, done: int, total: int) -> None:
        self._etap_postep("Zapis skoroszytu", done, total or None, co=WIERSZE_NA_LINIE)

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        suma = math.ceil(total_bytes / MIB) if total_bytes else None
        self._etap_postep("Raport ZIP (MB)", done_bytes // MIB, suma, co=MIB_NA_LINIE)

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        self._etap_postep("Asystent myśli", tokens, None, co=TOKENY_NA_LINIE)

    def on_message(self, text: str) -> None:
        """Komunikat ze znacznikiem czasu — inaczej niż `ConsoleEvents`, i to jest celowe.

        Ten strumień jest czytany z pliku po fakcie, a najważniejszym komunikatem jest
        zapowiedź postoju. Sam czas trwania nie odróżnia blokady limitera od uśpionego
        laptopa — to jest ta sama obserwacja, przez którą `_LogEvents` zapisuje godzinę
        wznowienia, a nie tylko liczbę sekund.
        """
        self.console.print(safe(f"{self._znacznik()} {text}"))

    def close(self) -> None:
        """Nic do zamknięcia — i to jest cała różnica między tą klasą a paskiem.

        `Events.close()` istnieje, bo żywy `Live` nadpisuje wszystko, co wydrukowano po nim.
        Tu nie ma czego gasić, więc kolejny ekran po prostu się dopisuje. Metoda zostaje, bo
        należy do protokołu i `cli` wywołuje ją w `finally`, nie wiedząc, którą implementację
        dostał — co jest dokładnie tym, czego się od protokołu oczekuje.

        Zapomina natomiast **stan etapu**, z tego samego powodu co `ConsoleEvents.close()`
        zapomina identyfikatory zadań: kreator wykonuje kilka akcji w jednym procesie, a bez
        tego druga z nich kontynuowała licznik wierszy pierwszej i nie wypisywała wiersza
        przejścia, bo etap „już był" (przegląd kodu 2026-09-24).
        """
        self._etap = None
        self._zrobione = 0
        self._suma = None
        self._wiersze_listy = 0
        self._zrobione_na_linii = 0
