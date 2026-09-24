"""`ConsoleEvents` — pasek postępu ograniczony do jednej akcji (ADR-0008, poprawka w `console`).

Kreator wykonuje kilka akcji w jednym procesie. Zanim `close()` zaczęło czyścić identyfikatory
zadań, druga akcja trafiała na nowy `Progress`, ale ze starym `TaskID` — czyli na zadanie,
którego w nowym pasku nie ma. Ten plik pilnuje, żeby każda akcja zaczynała od czystego paska,
i przy okazji tego, że krótkie odstępy limitera nie zasypują użytkownika komunikatami.
"""

from __future__ import annotations

import io
import re

from rich.console import Console

from ceidg_tool.console import LONG_WAIT_S, MIB, ConsoleEvents, LineEvents
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
    events.on_request("firma", 200, 0.1)

    assert buffer.getvalue() == ""
    assert events._progress is None
    # Cisza dotyczy paska, nie licznika. To jest liczba, którą przeczyta koperta (ADR-0024),
    # a `wznow`, `raporty` i `sprawdz-nip` wołają ten odbiornik właśnie z `quiet=True` — czyli
    # gałąź, w której najłatwiej zgubić `zapytania` i nigdy tego nie zauważyć.
    assert events.requests == 1


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


# ------------------------------------------ wiersze zamiast paska (ADR-0024, decyzja 4)


class _StalyZegar:
    """Zegar, który stoi.

    Celowo: rytm wierszy ma zależeć od tego, ile program zrobił, a nie od tego, ile minęło.
    Zatrzymany zegar sprawia, że test mierzący kadencję nie może przypadkiem zmierzyć czasu —
    a przy okazji znacznik czasu jest powtarzalny.
    """

    def monotonic(self) -> float:
        return 0.0

    def wall(self) -> float:
        return RESUME_AT

    def sleep(self, seconds: float) -> None:
        return None


def linie_for(*, quiet: bool = False) -> tuple[LineEvents, io.StringIO]:
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False)
    return LineEvents(console, quiet=quiet, clock=_StalyZegar()), buffer


def _wiersze(buffer: io.StringIO) -> list[str]:
    return [linia for linia in buffer.getvalue().splitlines() if linia.strip()]


def test_the_bar_says_nothing_off_a_terminal_and_that_is_why_this_class_exists() -> None:
    """Pomiar, na którym stoi cała decyzja 4 — i strażnik na wypadek, gdyby `rich` to zmienił.

    `rich/live.py:269` renderuje klatki pośrednie wyłącznie przy `console.is_terminal`. Sześć
    stron to sześć żądań i ponad dwadzieścia sekund pracy, a do bufora nie trafia ani jeden
    znak aż do `close()`. Gdyby ten test zrobił się czerwony, znaczyłoby to, że `LineEvents`
    przestał być potrzebny — i lepiej dowiedzieć się tego z testu niż nie dowiedzieć wcale.
    """
    pasek, bufor = events_for()

    for numer in range(6):
        pasek.on_page(numer, 25, 150)

    assert bufor.getvalue() == ""
    pasek.close()
    assert "Lista firm" in bufor.getvalue()


def test_twenty_four_requests_are_exactly_three_lines() -> None:
    """Kadencja: jeden wiersz na osiem żądań, czyli na około 30 s przy odstępie 3,75 s.

    24 = 3 × 8. Sąsiednia liczba jest tu po to, żeby próg był progiem, a nie przybliżeniem:
    licznik, który gubi jedno żądanie na cykl, przechodzi pierwszą asercję i oblewa drugą.
    """
    zdarzenia, bufor = linie_for()
    for _ in range(24):
        zdarzenia.on_request("firmy", 200, 0.1)
    assert len(_wiersze(bufor)) == 3

    chudsze, bufor_chudszy = linie_for()
    for _ in range(23):
        chudsze.on_request("firmy", 200, 0.1)
    assert len(_wiersze(bufor_chudszy)) == 2


def test_a_stage_transition_is_always_a_line() -> None:
    """Przejście między etapami nie czeka na próg — między etapami bywa dłużej niż próg."""
    zdarzenia, bufor = linie_for()

    zdarzenia.on_page(0, 25, 150)
    zdarzenia.on_details(0, 150)

    wiersze = _wiersze(bufor)
    assert len(wiersze) == 2
    assert "Lista firm" in wiersze[0]
    assert "Szczegóły" in wiersze[1]


def test_the_list_line_counts_rows_not_pages() -> None:
    """Liczone w wierszach, nigdy w stronach — pomyłka o jedną warstwę z 2026-09-06.

    Strona `/zmiana` to 500 identyfikatorów, czyli do stu żądań. Licznik stron pokazałby po
    dwóch stronach „2" i wyglądałby na zdrowy przez pół godziny.
    """
    zdarzenia, bufor = linie_for()

    zdarzenia.on_page(0, 500, 1000)
    zdarzenia.on_page(1, 500, 1000)
    for _ in range(8):
        zdarzenia.on_request("zmiana", 200, 0.1)

    wiersze = _wiersze(bufor)
    assert "Lista firm 500/1000" in wiersze[0]
    assert "Lista firm 1000/1000" in wiersze[1]


def test_the_export_stage_speaks_although_it_sends_no_request() -> None:
    """Etap bez żądań ma własny próg, bo licznik żądań go nie napędza.

    Zapis skoroszytu zmierzono na 453 firmy/s — pełne województwo to ponad dziesięć minut
    przy zerowym liczniku żądań. Sam próg żądań zostawiłby tu ciszę dokładnie tam, gdzie
    `on_export` powstało, żeby ciszy nie było.
    """
    zdarzenia, bufor = linie_for()

    for zrobione in range(0, 40_001, 5_000):
        zdarzenia.on_export(zrobione, 40_000)

    # Przejście etapu plus cztery progi po 10 000 wierszy.
    assert len(_wiersze(bufor)) == 5


def test_quiet_counts_requests_without_saying_anything() -> None:
    """`quiet` znaczy to samo co przy pasku: licznik rośnie, postęp się nie pokazuje.

    Licznik musi rosnąć, bo to jest liczba, którą przeczyta koperta — a koperta ma podać tę
    samą liczbę, która napędzała oznaki życia, nie drugą, liczoną gdzie indziej.
    """
    zdarzenia, bufor = linie_for(quiet=True)

    for _ in range(24):
        zdarzenia.on_request("firmy", 200, 0.1)
    zdarzenia.on_page(0, 25, 150)

    assert _wiersze(bufor) == []
    assert zdarzenia.requests == 24


def test_close_writes_nothing_because_there_is_nothing_to_stop() -> None:
    """Brak `Live` to brak sprzątania — i brak ostatniej klatki dopisanej po podsumowaniu."""
    zdarzenia, bufor = linie_for()
    zdarzenia.on_page(0, 25, 150)
    przed = bufor.getvalue()

    zdarzenia.close()
    zdarzenia.close()

    assert bufor.getvalue() == przed


def test_both_receivers_word_a_wait_identically() -> None:
    """Jedno zdarzenie, dwa wyjścia, jedno zdanie — po to `komunikat_o_czekaniu` jest osobno.

    Dwa egzemplarze tej treści rozjechałyby się przy pierwszej poprawce jednego z nich; ta
    sama pomyłka co `richtext.safe` w dwóch miejscach (ADR-0009).
    """
    pasek, bufor_paska = events_for()
    zdarzenia, bufor_linii = linie_for()

    pasek.on_wait(600.0, REASON_COOLDOWN, RESUME_AT)
    zdarzenia.on_wait(600.0, REASON_COOLDOWN, RESUME_AT)

    zdanie = bufor_paska.getvalue().strip()
    assert zdanie.startswith("limit API")
    assert zdanie in bufor_linii.getvalue()


def test_a_jwt_in_a_line_is_masked_before_reaching_the_console() -> None:
    """Drugie wyjście to druga droga tokenu na ekran — i przechodzi tym samym `safe`."""
    zdarzenia, bufor = linie_for()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27uhbUJU1p1r_wW1g"

    zdarzenia.on_message(f"odrzucone żądanie z tokenem {jwt}")

    wynik = bufor.getvalue()
    assert jwt not in wynik
    assert "<token>" in wynik


def test_every_line_is_stamped_to_the_second() -> None:
    """Znacznik czasu i jego **rozdzielczość** — obie rzeczy były do 2026-09-24 bez obserwatora.

    Zmierzone: zamiana `local_hhmmss` na `local_hhmm` przechodziła całą suitę. Przy rytmie około
    30 s dwie kolejne oznaki życia bywają wtedy tym samym napisem, czyli przerwa, którą ten
    wiersz ma pokazywać, robi się niewidoczna — a to jest jedyny powód, dla którego `clock.py`
    ma dwie funkcje zamiast jednej.

    Asercja idzie po **kształcie**, nie po wartości: godzina zależy od strefy maszyny, a pytanie
    brzmi „czy są sekundy", nie „która jest godzina".
    """
    zdarzenia, bufor = linie_for()

    zdarzenia.on_page(0, 25, 150)

    assert re.match(r"^\[\d{2}:\d{2}:\d{2}\] \S", _wiersze(bufor)[0])


def test_a_message_is_stamped_too_and_that_is_the_one_that_matters() -> None:
    """Zapowiedź postoju bez godziny nie odróżnia blokady limitera od uśpionego laptopa.

    To jest ta sama obserwacja, przez którą `_LogEvents` zapisuje godzinę wznowienia, a nie
    tylko liczbę sekund (2026-09-08). `test_both_receivers_word_a_wait_identically` porównuje
    przez `in`, więc brak przedrostka przechodził tam niezauważony.
    """
    zdarzenia, bufor = linie_for()

    zdarzenia.on_wait(600.0, REASON_COOLDOWN, RESUME_AT)

    assert re.match(r"^\[\d{2}:\d{2}:\d{2}\] limit API", _wiersze(bufor)[0])


def test_a_second_action_in_one_process_starts_a_fresh_stage() -> None:
    """`close()` zapomina etap — z tego samego powodu, dla którego pasek zapomina `TaskID`.

    Kreator wykonuje kilka akcji w jednym procesie. Bez tego druga kontynuowała licznik wierszy
    pierwszej i **nie wypisywała wiersza przejścia**, bo etap „już był" — czyli gubiła
    dokładnie tę informację, na którą wołający czeka najbardziej (przegląd kodu 2026-09-24).
    """
    zdarzenia, bufor = linie_for()
    zdarzenia.on_page(0, 25, 150)
    zdarzenia.close()

    zdarzenia.on_page(0, 25, 150)

    wiersze = _wiersze(bufor)
    assert len(wiersze) == 2
    assert all("Lista firm 25/150" in wiersz for wiersz in wiersze)


def test_the_fallback_console_of_both_receivers_is_on_stderr() -> None:
    """Gałąź zapasowa nie ma prawa łamać niezmiennika, którego broni gałąź główna.

    `cli` zawsze podaje konsolę, więc te dwa `or make_console(...)` są dziś nieużywane —
    i właśnie dlatego są warte testu: pierwsze wywołanie bez argumentu to jedyna okazja, żeby
    znak trafił na stdout w środku cudzego dokumentu JSON, a `console.print` reguła 15 z
    rozmysłu pomija (to sprawa reguły 10, a ta o strumieniach nie orzeka).
    """
    assert ConsoleEvents().console.stderr is True
    assert LineEvents().console.stderr is True
