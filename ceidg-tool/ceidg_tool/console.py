"""Konsolowa implementacja `Events`: pasek postępu, komunikaty o limicie i braku sieci."""

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

from .clock import local_hhmm
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


class ConsoleEvents:
    """Postęp na konsoli; krótkie odstępy limitera (3,75 s) nie są raportowane."""

    def __init__(self, console: Console | None = None, *, quiet: bool = False) -> None:
        self.console = console or make_console()
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
        if seconds < LONG_WAIT_S and reason not in (REASON_COOLDOWN, REASON_RESUME, REASON_BUDGET):
            return
        when = local_hhmm(resume_at_epoch)
        if reason == REASON_BUDGET:
            # Inny komunikat niż przy zwykłym oknie: tu limit zużył ktoś poza tym procesem,
            # a postój bywa długi (do końca godzinnego okna), więc operator ma wiedzieć czemu.
            self.on_message(
                f"budżet godzinny tokenu na wyczerpaniu (zużyty także poza tym pobraniem), "
                f"wznawiam o {when}"
            )
        elif reason == REASON_COOLDOWN or reason == REASON_WINDOW:
            self.on_message(f"limit API, wznawiam o {when}")
        elif reason == REASON_NO_CONNECTION:
            self.on_message(f"brak połączenia, czekam do {when}, postęp zapisany")
        elif reason == REASON_RESUME:
            self.on_message(f"wznowienie po przerwie, start o {when}")
        else:
            self.on_message(f"czekam {seconds:.0f} s ({reason}), dalej o {when}")

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
