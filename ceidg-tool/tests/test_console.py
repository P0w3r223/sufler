"""`ConsoleEvents` — pasek postępu ograniczony do jednej akcji (ADR-0008, poprawka w `console`).

Kreator wykonuje kilka akcji w jednym procesie. Zanim `close()` zaczęło czyścić identyfikatory
zadań, druga akcja trafiała na nowy `Progress`, ale ze starym `TaskID` — czyli na zadanie,
którego w nowym pasku nie ma. Ten plik pilnuje, żeby każda akcja zaczynała od czystego paska,
i przy okazji tego, że krótkie odstępy limitera nie zasypują użytkownika komunikatami.
"""

from __future__ import annotations

import io

from rich.console import Console

from ceidg_tool.console import LONG_WAIT_S, MIB, ConsoleEvents
from ceidg_tool.ratelimit import REASON_COOLDOWN, REASON_RESUME, REASON_WINDOW

RESUME_AT = 1_700_000_000.0


def events_for() -> tuple[ConsoleEvents, io.StringIO]:
    buffer = io.StringIO()
    # `force_terminal=False`: bez tego `FORCE_COLOR` z otoczenia (GitHub Actions ustawia go
    # domyślnie) sprawia, że `rich` uznaje bufor za terminal i **animuje** pasek — do bufora
    # trafiają wtedy klatki pośrednie, w tym ta sprzed poznania sumy, ze znakiem zapytania.
    # Test o treści końcowego paska mierzyłby wtedy otoczenie, a nie kod.
    return ConsoleEvents(Console(file=buffer, width=120, force_terminal=False)), buffer


# ----------------------------------------------------------------------------- pasek na akcję


def test_a_second_fetch_in_one_process_starts_a_fresh_progress_task() -> None:
    """Sedno poprawki: po `close()` kolejna akcja nie może sięgnąć po `TaskID` z poprzedniej."""
    events, buffer = events_for()
    events.on_page(0, 5, 10)
    events.close()

    events.on_page(0, 5, 10)  # przed poprawką: odwołanie do nieistniejącego zadania
    events.close()

    assert "Lista firm" in buffer.getvalue()


def test_close_forgets_every_task_id() -> None:
    """Każde zadanie trzeba wyzerować — inaczej wraca ten sam błąd.

    `close()` zapomina o wszystkich kanałach albo o żadnym. Test wymieniający podzbiór
    przechodził dla zadań eksportu i pobierania niezależnie od tego, czy kod je zeruje —
    a to właśnie druga akcja w tej samej sesji kreatora sięgałaby po `TaskID` z martwego
    `Progress`, czyli defekt, dla którego ten plik powstał.

    Test wymieniał cztery kanały i tak też się nazywał; piąty (`_task_model`, asystent)
    doszedł w fazie 4j i nie został do niego dopisany, więc usunięcie jego zerowania
    zostawiało suitę zieloną (audyt 2026-09-07). Dlatego lista kanałów jest teraz
    **odczytywana z obiektu**, a nie przepisywana: szósty zapali ten test sam z siebie.
    """
    events, _ = events_for()
    events.on_page(0, 5, 10)
    events.on_details(1, 5)
    events.on_export(1, 5)
    events.on_download(0, 3 * MIB)
    events.on_model(1.0, 64)

    zadania = sorted(a for a in vars(events) if a.startswith("_task_"))
    assert len(zadania) >= 5, f"kanałów jest {len(zadania)} — test miał obejmować wszystkie"
    assert all(getattr(events, a) is not None for a in zadania), (
        "nie każdy kanał dostał zadanie przed `close()` — test nie sprawdziłby zerowania"
    )

    events.close()

    zapomniane = {a: getattr(events, a) for a in zadania}
    assert zapomniane == dict.fromkeys(zadania), f"kanał niezapomniany: {zapomniane}"
    assert events._progress is None


def test_the_download_bar_counts_whole_megabytes() -> None:
    """Pasek archiwum liczy w MiB: bajty dałyby ośmiocyfrowy szum w kolumnie liczb całkowitych."""
    events, buffer = events_for()

    events.on_download(0, 21 * MIB)
    events.on_download(21 * MIB, 21 * MIB)
    events.close()

    output = buffer.getvalue()
    assert "Raport ZIP (MB)" in output
    assert "21/21" in output


def test_a_download_smaller_than_a_megabyte_still_has_a_known_total() -> None:
    """Znany `Content-Length` nie może udawać nieznanego.

    `total // MIB` dawało 0 dla archiwum poniżej 1 MiB, a `total or None` zamieniało zero
    w `None` — czyli w znak zapytania, który `_CountColumn` rezerwuje dla sumy **nieznanej**.
    Pasek nigdy by nie drgnął, choć rozmiar był znany od nagłówków.
    """
    events, buffer = events_for()

    events.on_download(0, MIB // 2)
    events.on_download(MIB // 2, MIB // 2)
    events.close()

    assert "?" not in buffer.getvalue()


def test_a_download_without_a_known_size_shows_a_question_mark() -> None:
    """Kontrola do testu wyżej: przy odpowiedzi bez `Content-Length` znak zapytania ma zostać."""
    events, buffer = events_for()

    events.on_download(0, None)
    events.on_download(MIB, None)
    events.close()

    assert "?" in buffer.getvalue()


def test_close_is_safe_before_anything_was_rendered() -> None:
    """`finally: events.close()` w CLI woła się także wtedy, gdy akcja padła przed paskiem."""
    events, _ = events_for()

    events.close()  # nie może rzucić

    assert events._progress is None


def test_close_twice_is_harmless() -> None:
    events, _ = events_for()
    events.on_page(0, 1, 1)

    events.close()
    events.close()

    assert events._progress is None


def test_three_actions_in_a_row_each_get_their_own_bar() -> None:
    """Kreator w skrajnym przypadku: pobranie, aktualizacja i sprawdzenie NIP w jednej sesji."""
    events, buffer = events_for()

    for _ in range(3):
        events.on_page(0, 2, 4)
        events.on_details(2, 4)
        events.close()

    assert "Lista firm" in buffer.getvalue()
    assert "Szczegóły" in buffer.getvalue()


def test_quiet_mode_renders_no_progress_at_all() -> None:
    """`sprawdz-nip` woła `ConsoleEvents(quiet=True)` — dwa żądania nie potrzebują paska."""
    buffer = io.StringIO()
    events = ConsoleEvents(Console(file=buffer, width=120, force_terminal=False), quiet=True)

    events.on_page(0, 5, 10)
    events.on_details(1, 5)

    assert buffer.getvalue() == ""
    assert events._progress is None


# ----------------------------------------------------------------------------- licznik i czekanie


def test_requests_are_counted() -> None:
    events, _ = events_for()

    events.on_request("firmy", 200, 0.1)
    events.on_request("firma", 200, 0.1)

    assert events.requests == 2


def test_short_limiter_pauses_are_not_reported() -> None:
    """Odstęp 3,75 s zdarza się przed każdym żądaniem — komentowanie go byłoby szumem."""
    events, buffer = events_for()

    events.on_wait(LONG_WAIT_S - 0.1, "odstep", RESUME_AT)

    assert buffer.getvalue() == ""


def test_a_long_wait_tells_the_user_when_work_resumes() -> None:
    events, buffer = events_for()

    events.on_wait(LONG_WAIT_S + 1, "odstep", RESUME_AT)

    assert "dalej o" in buffer.getvalue()


def test_a_cooldown_is_reported_even_when_it_is_short() -> None:
    """Wyjście z limitu API to informacja, której użytkownik potrzebuje niezależnie od długości."""
    events, buffer = events_for()

    events.on_wait(0.5, REASON_COOLDOWN, RESUME_AT)

    assert "limit API" in buffer.getvalue()


def test_a_window_wait_is_reported_as_an_api_limit() -> None:
    events, buffer = events_for()

    events.on_wait(LONG_WAIT_S + 1, REASON_WINDOW, RESUME_AT)

    assert "limit API" in buffer.getvalue()


def test_a_resume_gap_is_reported_even_when_short() -> None:
    events, buffer = events_for()

    events.on_wait(0.5, REASON_RESUME, RESUME_AT)

    assert "wznowienie po przerwie" in buffer.getvalue().lower()


def test_a_lost_connection_says_progress_is_saved() -> None:
    """Zapewnienie „postęp zapisany” jest tym, co powstrzymuje użytkownika przed Ctrl+C."""
    events, buffer = events_for()

    events.on_wait(LONG_WAIT_S + 1, "brak_polaczenia", RESUME_AT)

    output = buffer.getvalue()
    assert "brak połączenia" in output
    assert "postęp zapisany" in output


def test_a_jwt_in_a_message_is_masked_before_reaching_the_console() -> None:
    """§B: gdyby token trafił do komunikatu (np. w adresie), nie może wyjść na ekran."""
    events, buffer = events_for()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"

    events.on_message(f"odrzucone żądanie z tokenem {jwt}")

    output = buffer.getvalue()
    assert jwt not in output
    assert "<token>" in output


# ------------------------------------------------------- opis zadania to też ścieżka wyjścia


def test_the_progress_bar_does_not_re_parse_markup_in_a_task_description() -> None:
    """`TextColumn` formatuje opis i przepuszcza wynik przez `Text.from_markup`.

    Dlatego `safe()` nie wystarczyłoby: zwraca `rich.Text`, ale kolumna zamienia go
    z powrotem na napis i parsuje jeszcze raz — nazwa z `[/i]` kończyłaby program
    wyjątkiem `MarkupError` spoza taksonomii `CeidgError`, a `[link=…]` robiłaby
    z opisu klikalny odnośnik na obcy adres. Opisy są dziś stałymi programu, więc bez
    tego testu poprawka zniknęłaby przy pierwszym refaktorze.
    """
    events, buffer = events_for()
    hostile = "Firma [/i] [link=http://zly.example]klik[/link]"

    progress = events._progress_bar()
    progress.add_task(hostile, total=1)
    progress.refresh()
    events.close()

    printed = buffer.getvalue()
    assert "[link=http://zly.example]" in printed  # znacznik pokazany, nie wykonany
    assert "[/i]" in printed


def test_the_details_bar_follows_a_total_that_grows() -> None:
    """`aktualizuj` poznaje kolejne okna czasowe w trakcie, więc suma rośnie.

    Bez tego pasek zostawał z sumą pierwszego okna i dobijał do 100 % w połowie pracy —
    `on_page` obsługiwał zmianę sumy od początku, `on_details` nie."""
    events, _ = events_for()
    events.on_details(10, 20)
    assert events._task_details is not None
    progress = events._progress
    assert progress is not None
    assert progress.tasks[events._task_details].total == 20

    events.on_details(20, 50)  # drugie okno dołożyło pracy

    assert progress.tasks[events._task_details].total == 50
    assert progress.tasks[events._task_details].completed == 20
    events.close()


# ----------------------------------------------------- oznaka życia, gdy licznik stoi (bramka 3)


def test_the_assistant_bar_shows_elapsed_time_not_only_a_token_count() -> None:
    """Licznik tokenów bywa nieruchomy; czas płynie zawsze — i to on jest oznaką życia.

    Właściciel przy przejściu bramki 3 zobaczył „asystent tokeny 72/?" i nie miał jak odróżnić
    myślenia od zwisu: przy nieznanej sumie `TimeRemainingColumn` nic nie liczy, a sama liczba
    tokenów potrafi stać w miejscu przez większość oczekiwania. Stąd osobna `TimeElapsedColumn`.
    """
    events, buffer = events_for()

    events.on_model(0.0, 0)
    events.on_model(7.0, 512)
    events.close()

    tekst = buffer.getvalue()
    assert "Asystent myśli" in tekst
    assert "512/?" in tekst
    # Kolumna czasu renderuje `H:MM:SS`; jej brak znaczy, że pasek znowu nie ma nic ruchomego.
    assert "0:00:" in tekst, f"brak kolumny upływu czasu: {tekst!r}"
