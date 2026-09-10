from __future__ import annotations

from collections.abc import Callable, Sequence

import pytest

from ceidg_tool.apiprofile import ApiProfile, load_profile
from ceidg_tool.estimating import effective_spacing
from ceidg_tool.ratelimit import (
    REASON_BUDGET,
    REASON_COOLDOWN,
    REASON_SPACING,
    REASON_WINDOW,
    WAIT_SLICE_S,
    InMemoryHistory,
    RateLimiter,
    RequestStamp,
)
from ceidg_tool.store import DEFAULT_LOCK_STALE_S
from tests.conftest import FakeClock

WINDOWS = ((50, 180.0), (1000, 3600.0))

# Tolerancja do porównań **czasu ściennego**. `pytest.approx(x)` ma domyślnie `rel=1e-6`,
# a `FakeClock` startuje na 1 700 000 000, więc domyślna tolerancja to ±1700 s: asercja
# „bicie padło na początku postoju" przechodziła dla bicia spóźnionego o 300 s, a nawet o 900.
# Recenzent przesunął bicie za `clock.sleep(plaster)` i cały zestaw był zielony. Arytmetyka
# `FakeClock` jest dokładna (dodawanie float bez pomiaru), więc na znacznikach porównujemy
# bezwzględnie; na **czasach trwania** (małe liczby) domyślne `approx` zostaje.
TOLERANCJA_ZEGARA_S = 1e-6


class Recorder:
    """Zapisuje zdarzenia razem z **czasem zegara**, w którym padły.

    Sama kolejność w liście nie odróżnia „przed uśpieniem" od „po uśpieniu": `FakeClock.sleep`
    przesuwa zegar natychmiast, więc zamiana `on_wait` i `sleep` miejscami w `acquire` nie
    zmienia kolejności wpisów. Recenzent zrobił dokładnie tę zamianę i całe 975 testów
    przeszło na zielono. Rozróżnia je dopiero znacznik czasu w chwili zdarzenia."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.waits: list[tuple[float, str, float]] = []
        self.wait_walls: list[float] = []
        self._clock = clock

    def on_request(self, endpoint: str, status: int, elapsed_s: float) -> None:
        return None

    def on_wait(self, seconds: float, reason: str, resume_at_epoch: float) -> None:
        self.waits.append((seconds, reason, resume_at_epoch))
        if self._clock is not None:
            self.wait_walls.append(self._clock.wall())

    def on_page(self, page_index: int, records: int, total: int | None) -> None:
        return None

    def on_details(self, done: int, total: int) -> None:
        return None

    def on_export(self, done: int, total: int) -> None:
        return None

    def on_download(self, done_bytes: int, total_bytes: int | None) -> None:
        return None

    def on_model(self, elapsed_s: float, tokens: int) -> None:
        return None

    def on_message(self, text: str) -> None:
        return None

    def close(self) -> None:
        return None


def make(
    clock: FakeClock,
    *,
    spacing: float = 3.6,
    cooldown: float = 185.0,
    history: InMemoryHistory | None = None,
    windows: tuple[tuple[int, float], ...] = WINDOWS,
    heartbeat: Callable[[], object] | None = None,
) -> tuple[RateLimiter, InMemoryHistory, Recorder]:
    hist = history or InMemoryHistory()
    rec = Recorder(clock)
    limiter = RateLimiter(
        windows=windows,
        min_spacing_s=spacing,
        cooldown_s=cooldown,
        clock=clock,
        history=hist,
        events=rec,
        heartbeat=heartbeat,
    )
    return limiter, hist, rec


def test_first_request_is_immediate_and_recorded(clock: FakeClock) -> None:
    limiter, hist, rec = make(clock)
    limiter.acquire("firmy")
    assert clock.sleeps == []
    assert len(hist) == 1
    assert rec.waits == []


def test_min_spacing_is_enforced_between_requests(clock: FakeClock) -> None:
    limiter, _, rec = make(clock)
    limiter.acquire("firmy")
    clock.advance(1.0)
    limiter.acquire("firmy")
    assert clock.sleeps == [pytest.approx(2.6)]
    assert rec.waits[0][1] == REASON_SPACING


def test_51st_request_in_3_minutes_waits_for_window(clock: FakeClock) -> None:
    limiter, _, rec = make(clock, spacing=0.0)
    for _ in range(50):
        limiter.acquire("firmy")
        clock.advance(1.0)  # 50 żądań w 50 s
    limiter.acquire("firmy")  # 51. musi poczekać, aż pierwsze opuści okno 180 s
    assert rec.waits[-1][1] == REASON_WINDOW
    assert clock.sleeps[-1] == pytest.approx(130.0)


def przespane_od(clock: FakeClock, od: int) -> float:
    """Suma snów od danego miejsca w historii — kontraktem jest **łączny** postój.

    Od 2026-09-08 limiter przesypia długie czekanie w plastrach po `WAIT_SLICE_S`, żeby
    dzierżawa blokady bazy (600 s) dostała bicie serca w środku postoju przyciętego do
    najdłuższego okna (3600 s). Asercja na `sleeps[-1]` mierzyłaby wtedy rozmiar plastra,
    czyli szczegół implementacyjny, zamiast tempa, o które w tych testach chodzi."""
    return sum(clock.sleeps[od:])


def test_hourly_window_binds_independently(clock: FakeClock) -> None:
    windows = ((50, 180.0), (100, 3600.0))
    limiter, _, rec = make(clock, spacing=0.0, windows=windows)
    for _ in range(100):
        limiter.acquire("firmy")
        clock.advance(10.0)  # 100 żądań w 1000 s: okno 3 min nigdy nie pełne
    przed = len(clock.sleeps)
    limiter.acquire("firmy")
    assert rec.waits[-1][1] == REASON_WINDOW
    assert przespane_od(clock, przed) == pytest.approx(2600.0)


def test_429_blocks_full_cooldown_from_last_attempt(clock: FakeClock) -> None:
    limiter, _, rec = make(clock, spacing=0.0)
    limiter.acquire("firmy")
    clock.advance(2.0)  # odpowiedź przyszła po 2 s
    limiter.note_response(429)
    limiter.acquire("firmy")
    assert rec.waits[-1][1] == REASON_COOLDOWN
    assert clock.sleeps[-1] == pytest.approx(183.0)  # 185 liczone od próby, nie od odpowiedzi


def test_retry_after_never_shortens_cooldown(clock: FakeClock) -> None:
    limiter, _, _ = make(clock, spacing=0.0)
    limiter.acquire("firmy")
    limiter.note_response(429, retry_after_s=30.0)
    limiter.acquire("firmy")
    assert clock.sleeps[-1] == pytest.approx(185.0)

    limiter.note_response(429, retry_after_s=400.0)
    przed = len(clock.sleeps)
    limiter.acquire("firmy")
    assert przespane_od(clock, przed) == pytest.approx(400.0)


def test_history_from_previous_process_rebuilds_window(clock: FakeClock) -> None:
    # poprzedni proces wysłał 50 żądań w ostatniej minucie i się wywrócił
    now = clock.wall()
    history = InMemoryHistory([RequestStamp(now - 60 + i, "firmy", 200) for i in range(50)])
    limiter, _, rec = make(clock, spacing=0.0, history=history)
    limiter.acquire("firmy")
    assert rec.waits[-1][1] == REASON_WINDOW
    assert clock.sleeps[-1] == pytest.approx(120.0)


def test_429_from_previous_process_is_respected(clock: FakeClock) -> None:
    now = clock.wall()
    history = InMemoryHistory([RequestStamp(now - 100, "firmy", 429)])
    limiter, _, rec = make(clock, spacing=0.0)
    limiter, _, rec = make(clock, spacing=0.0, history=history)
    limiter.acquire("firmy")
    assert rec.waits[-1][1] == REASON_COOLDOWN
    assert clock.sleeps[-1] == pytest.approx(85.0)


def test_wall_clock_jump_does_not_break_spacing(clock: FakeClock) -> None:
    limiter, _, _ = make(clock)
    limiter.acquire("firmy")
    clock.jump_wall(-3600.0)  # zegar cofnięty o godzinę
    limiter.acquire("firmy")
    assert clock.sleeps[-1] == pytest.approx(3.6)
    clock.jump_wall(+7200.0)  # i skok do przodu
    limiter.acquire("firmy")
    assert clock.sleeps[-1] == pytest.approx(3.6)


def test_stamps_from_the_future_are_ignored(clock: FakeClock) -> None:
    history = InMemoryHistory(
        [RequestStamp(clock.wall() + 500 + i, "firmy", 200) for i in range(50)]
    )
    limiter, _, _ = make(clock, spacing=0.0, history=history)
    limiter.acquire("firmy")
    assert clock.sleeps == []


def test_backoff_delay_goes_through_limiter(clock: FakeClock) -> None:
    limiter, _, rec = make(clock, spacing=0.0)
    limiter.acquire("firmy")
    limiter.note_response(503)
    limiter.acquire("firmy", extra_delay_s=10.0)
    assert clock.sleeps[-1] == pytest.approx(10.0)
    assert rec.waits[-1][2] == pytest.approx(clock.wall(), abs=TOLERANCJA_ZEGARA_S)


def test_resume_at_epoch_is_reported(clock: FakeClock) -> None:
    limiter, _, rec = make(clock)
    start = clock.wall()
    limiter.acquire("firmy")
    limiter.acquire("firmy")
    assert rec.waits[0][2] == pytest.approx(start + 3.6, abs=TOLERANCJA_ZEGARA_S)


def test_invalid_windows_are_rejected(clock: FakeClock) -> None:
    with pytest.raises(ValueError):
        RateLimiter(
            windows=((0, 180.0),),
            min_spacing_s=0,
            cooldown_s=0,
            clock=clock,
            history=InMemoryHistory(),
        )


# --- serie: co realnie zobaczy serwer, a nie co deklaruje konfiguracja --------------------

# Udokumentowane limity API (docs/api_notes.md). Nasze okna są od nich węższe, ale to
# nie one decydują o szczycie — decyduje odstęp minimalny, patrz test niżej.
API_LIMITS: tuple[tuple[int, float], ...] = ((50, 180.0), (1000, 3600.0))


def busiest_window(stamps: Sequence[float], span: float) -> int:
    """Najwięcej znaczników, jakie mieszczą się w oknie `span` — po wszystkich pozycjach."""
    best = start = 0
    for i, t in enumerate(stamps):
        while stamps[start] <= t - span:
            start += 1
        best = max(best, i - start + 1)
    return best


def stamps_from(clock: FakeClock, profile: ApiProfile, count: int) -> list[float]:
    limiter = RateLimiter(
        windows=profile.rate.windows,
        min_spacing_s=profile.rate.min_spacing_s,
        cooldown_s=profile.rate.cooldown_s,
        clock=clock,
        history=InMemoryHistory(),
    )
    out = []
    for _ in range(count):
        limiter.acquire("firmy")
        out.append(clock.monotonic())
    return out


@pytest.mark.parametrize("environment", ["prod", "test"])
def test_shipped_profiles_cannot_burst_past_their_window(
    clock: FakeClock, environment: str
) -> None:
    """Odstęp minimalny mniejszy niż `span/limit` pozwala nadrabiać postojem i robić serie.

    Przy `min_spacing_s = 3.6` i oknie 48/180 zmierzony szczyt wynosił **49** żądań na 180 s
    — o jedno ponad nasz własny limit i o jedno poniżej limitu API. Ten test pilnuje, żeby
    żaden profil nie wrócił do konfiguracji, w której szczyt przekracza zadeklarowane okno."""
    profile = load_profile(environment)
    stamps = stamps_from(clock, profile, 900)

    for limit, span in profile.rate.windows:
        assert busiest_window(stamps, span) <= limit, f"{environment}: okno {limit}/{span:.0f}s"


@pytest.mark.parametrize("environment", ["prod", "test"])
def test_shipped_profiles_keep_a_margin_to_the_documented_api_limits(
    clock: FakeClock, environment: str
) -> None:
    """To jest pytanie, które naprawdę boli: ile brakuje do limitu, którego pilnuje serwer."""
    stamps = stamps_from(clock, load_profile(environment), 900)

    for limit, span in API_LIMITS:
        assert busiest_window(stamps, span) < limit, f"{environment}: {limit} na {span:.0f}s"


@pytest.mark.parametrize("environment", ["prod", "test"])
def test_the_limiter_paces_exactly_as_the_cost_table_promises(environment: str) -> None:
    """Estymator i limiter muszą liczyć ten sam odstęp — inaczej tabela kosztów kłamie.

    Rozjazd był realny: `effective_spacing` zwracało 3,75 s, a limiter przepuszczał żądania
    co 3,6 s i nadrabiał postojem, więc czas pobrania się zgadzał, a rozkład żądań nie."""
    profile = load_profile(environment)
    assert profile.rate.min_spacing_s == pytest.approx(effective_spacing(profile))


# --- hamulec na budżet zgłaszany przez serwer --------------------------------------------

# Limit jest nakładany na token, a nie na proces: sonda, druga maszyna albo Postman zużywają
# ten sam budżet, a `request_log` ich nie widzi. `X-Rate-Limit-Remaining` to jedyny sygnał.


def test_a_low_budget_holds_requests_until_the_server_says_reset(clock: FakeClock) -> None:
    limiter, _, rec = make(clock)
    limiter.acquire("firmy")
    reset = clock.wall() + 900.0

    czekanie = limiter.note_budget(remaining=3, reset_epoch=reset)
    assert czekanie == pytest.approx(900.0)

    limiter.acquire("firmy")
    assert clock.sleeps and sum(clock.sleeps) == pytest.approx(900.0)
    assert rec.waits[-1][1] == REASON_BUDGET
    assert rec.waits[-1][2] == pytest.approx(reset, abs=TOLERANCJA_ZEGARA_S)


def test_a_healthy_budget_changes_nothing(clock: FakeClock) -> None:
    """Hamulec ma milczeć, dopóki serwer nie zgłosi problemu — inaczej dubluje własne okna."""
    limiter, _, rec = make(clock)
    assert limiter.note_budget(remaining=900, reset_epoch=clock.wall() + 900.0) == 0.0
    limiter.acquire("firmy")
    assert clock.sleeps == []
    assert not [w for w in rec.waits if w[1] == REASON_BUDGET]


@pytest.mark.parametrize(
    ("remaining", "reset_offset"),
    [
        (None, 900.0),  # nagłówka nie było albo nie był liczbą
        (3, None),  # nie wiadomo, do kiedy czekać — zgadywanie byłoby gorsze niż jedno 429
        (3, -60.0),  # okno już się zresetowało
    ],
)
def test_the_brake_stays_silent_without_two_usable_numbers(
    clock: FakeClock, remaining: int | None, reset_offset: float | None
) -> None:
    limiter, _, _ = make(clock)
    reset = None if reset_offset is None else clock.wall() + reset_offset
    assert limiter.note_budget(remaining=remaining, reset_epoch=reset) == 0.0
    limiter.acquire("firmy")
    assert clock.sleeps == []


def test_a_far_away_reset_cannot_stall_longer_than_the_window(clock: FakeClock) -> None:
    """Zepsuty albo źle zinterpretowany `X-Rate-Limit-Reset` (sekundy kontra milisekundy)
    nie może zatrzymać pracy na dobę — postój przycinamy do najdłuższego okna."""
    limiter, _, _ = make(clock)
    czekanie = limiter.note_budget(remaining=0, reset_epoch=clock.wall() + 30 * 86_400.0)
    assert czekanie == pytest.approx(max(span for _, span in WINDOWS))


def test_the_longest_hold_wins_when_the_budget_is_reported_twice(clock: FakeClock) -> None:
    """Kolejne odpowiedzi nie mogą skrócić postoju — inaczej starczy jedna z krótszym resetem."""
    limiter, _, _ = make(clock)
    limiter.note_budget(remaining=1, reset_epoch=clock.wall() + 600.0)
    limiter.note_budget(remaining=1, reset_epoch=clock.wall() + 120.0)
    limiter.acquire("firmy")
    assert sum(clock.sleeps) == pytest.approx(600.0)


def test_a_profile_without_a_rate_section_paces_like_the_shipped_ones() -> None:
    """Domyślny odstęp też musi zgadzać się z oknem — profil bywa pisany ręcznie.

    Oba profile w pakiecie niosą 3,75 s, więc testy powyżej przechodziły, a `RateProfile`
    domyślał się 3,6 s. Profil podstawiony przez `CEIDG_PROFILE` bez sekcji `rate:` brał tę
    domyślną i wracał do tempa, przy którym zmierzony szczyt to 49 żądań na 180 s przy limicie
    API 50 — bez czerwonego testu, bo żaden nie patrzył na wartość domyślną.
    """
    default = ApiProfile(base_url="https://test-dane.biznes.gov.pl/api/ceidg/v3")

    assert default.rate.min_spacing_s == pytest.approx(effective_spacing(default))
    assert all(
        default.rate.min_spacing_s >= span / limit for limit, span in default.rate.windows
    ), "odstęp domyślny poniżej okna pozwala na serię gęstszą niż własny limit"


# ------------------------------------------------------- sen w plastrach i bicie serca blokady


class BicieSerca:
    """Wywołanie zwrotne limitera z zapisem czasu **ściennego** każdego bicia.

    Czas, a nie liczba: pytanie brzmi „jak długo dzierżawa blokady stała bez odświeżenia",
    a to jest odległość między biciami na zegarze, nie ich liczba w liście.
    """

    def __init__(self, clock: FakeClock) -> None:
        self._clock = clock
        self.walle: list[float] = []

    def __call__(self) -> bool:
        self.walle.append(self._clock.wall())
        return True

    def najdluzsza_przerwa(self, poczatek: float, koniec: float) -> float:
        """Najdłuższy odcinek, w którym dzierżawa nie dostała ani jednego dotknięcia.

        Liczony od **początku postoju**, a nie od pierwszego bicia: przed wejściem w czekanie
        dzierżawa była właśnie odświeżona, więc to od tej chwili biegnie jej wiek. Metryka
        zaczynająca się od pierwszego bicia nie widziałaby pustego odcinka na samym początku,
        czyli dokładnie tego, co robi bicie przesunięte za plaster."""
        punkty = [poczatek, *self.walle, koniec]
        return max(b - a for a, b in zip(punkty[:-1], punkty[1:], strict=True))


def budzetowy_postoj(clock: FakeClock) -> tuple[RateLimiter, Recorder, BicieSerca, float]:
    """Limiter tuż przed godzinnym postojem budżetowym — najdłuższy postój, jaki program zna.

    Budżet zgłoszony przez serwer jako wyczerpany blokuje do czasu resetu, przyciętego do
    najdłuższego okna (3600 s). Dzierżawa blokady bazy gaśnie po 600 s, więc to jest ten
    postój, który ją zabijał.
    """
    serce = BicieSerca(clock)
    limiter, _, rec = make(clock, spacing=0.0, heartbeat=serce)
    limiter.acquire("firmy")
    limiter.note_budget(0, clock.wall() + 3600.0)
    return limiter, rec, serce, clock.wall()


def test_godzinny_postoj_bije_w_blokade_wiele_razy(clock: FakeClock) -> None:
    """Jedno `sleep(3600)` zabijało dzierżawę pod pracującym procesem.

    Asercja jest o **liczbie bić większej niż jedno** i o tym, że łączny postój się zgadza —
    ile dokładnie plastrów, to szczegół implementacyjny `WAIT_SLICE_S`."""
    przed = len(clock.sleeps)
    limiter, _, serce, start = budzetowy_postoj(clock)

    limiter.acquire("firmy")

    assert len(serce.walle) > 1, "godzinny postój przespany jednym snem"
    assert przespane_od(clock, przed) == pytest.approx(3600.0)


def test_najdluzsza_przerwa_bez_bicia_nie_przekracza_plastra(clock: FakeClock) -> None:
    """Sedno poprawki: dzierżawa (600 s) nie może stać dłużej niż plaster bez odświeżenia.

    Mierzone na zegarze, nie na liczniku wywołań — bo to zegar decyduje o wygaśnięciu."""
    limiter, _, serce, start = budzetowy_postoj(clock)

    limiter.acquire("firmy")

    assert serce.najdluzsza_przerwa(start, clock.wall()) <= WAIT_SLICE_S


def test_bicie_wyprzedza_plaster_a_nie_idzie_za_nim(clock: FakeClock) -> None:
    """Pierwsze bicie pada, **zanim** proces zaśnie choćby na sekundę.

    Gdyby szło po plastrze, pierwsze odświeżenie dzierżawy wypadałoby 300 s po jej ostatnim
    dotknięciu — czyli w połowie drogi do wygaśnięcia, zanim cokolwiek zdąży pomóc."""
    limiter, _, serce, start = budzetowy_postoj(clock)

    limiter.acquire("firmy")

    # Tolerancja **bezwzględna**: `approx` na znaczniku epoch (~1,7e9) ma domyślnie
    # względne ±1700 s, więc przepuściłby bicie przesunięte o cały plaster (300 s).
    assert serce.walle[0] == pytest.approx(start, abs=TOLERANCJA_ZEGARA_S), (
        "pierwsze bicie po plastrze"
    )


def test_on_wait_pada_przed_postojem_a_nie_po_nim(clock: FakeClock) -> None:
    """Kolejność mierzona **czasem zegara**, bo kolejność w liście jej nie odróżnia.

    `FakeClock.sleep` przesuwa zegar natychmiast, więc zamiana `on_wait` i `_sleep_in_slices`
    miejscami nie zmienia ani kolejności wpisów, ani niczego, co widzi `Recorder` bez zegara.
    Zamiana jest za to widoczna na produkcji jako cisza przez cały postój: pasek i log
    dowiadują się o godzinnym czekaniu dopiero wtedy, gdy się skończyło.

    Stąd dwie asercje: zapowiedź pada w chwili sprzed postoju, a zapowiedziana godzina
    wznowienia leży w przyszłości względem tej chwili."""
    limiter, rec, _, start = budzetowy_postoj(clock)

    limiter.acquire("firmy")

    assert rec.wait_walls, "limiter nie zapowiedział postoju"
    assert rec.wait_walls[0] == pytest.approx(start, abs=TOLERANCJA_ZEGARA_S), (
        "zapowiedź padła po postoju, nie przed"
    )
    assert rec.wait_walls[0] < clock.wall()
    sekundy, _, wznowienie = rec.waits[0]
    assert wznowienie == pytest.approx(rec.wait_walls[0] + sekundy, abs=TOLERANCJA_ZEGARA_S), (
        "zapowiedziana godzina wznowienia ma być liczona od chwili zapowiedzi"
    )


def test_krotki_postoj_bije_raz_i_spi_raz(clock: FakeClock) -> None:
    """Postój krótszy niż plaster nie ma być cięty — plastry mają nie zmieniać tempa pracy.

    Odstęp minimalny (3,75 s) pada między każdą parą żądań; gdyby plastrowanie dokładało tu
    choć jedno wywołanie więcej, każde pobranie płaciłoby za to tysiące razy."""
    serce = BicieSerca(clock)
    limiter, _, _ = make(clock, heartbeat=serce)
    limiter.acquire("firmy")
    przed = len(clock.sleeps)

    limiter.acquire("firmy")

    assert len(serce.walle) == 1
    assert len(clock.sleeps) - przed == 1
    assert przespane_od(clock, przed) == pytest.approx(3.6)


def test_limiter_bez_bicia_serca_dziala_tak_samo(clock: FakeClock) -> None:
    """`heartbeat` jest opcjonalny — sonda i testy budują limiter bez blokady bazy."""
    przed = len(clock.sleeps)
    limiter, _, rec = make(clock, spacing=0.0)
    limiter.acquire("firmy")
    limiter.note_budget(0, clock.wall() + 3600.0)

    limiter.acquire("firmy")

    assert przespane_od(clock, przed) == pytest.approx(3600.0)
    assert rec.waits[-1][1] == REASON_BUDGET


def test_plaster_miesci_sie_pod_dzierzawa_blokady() -> None:
    """Zależność między dwoma modułami, której nie da się zapisać importem.

    `ratelimit` nie zna bazy (reguła granic), więc nie może odwołać się do
    `store.DEFAULT_LOCK_STALE_S` — a cała wartość plastra bierze się właśnie z tego, że jest
    od niej **mniejszy**. Podniesienie plastra do 600 s albo skrócenie dzierżawy przeszłoby
    przez wszystkie pozostałe testy: każdy z nich porównuje wynik z `WAIT_SLICE_S`, więc
    zmieniłby się razem z nią. Ta asercja jest jedynym miejscem, gdzie obie stałe się widzą.

    Margines, a nie sama nierówność: przy plastrze równym połowie dzierżawy jedno spóźnione
    bicie nie zabija blokady, bo zostaje jeszcze tyle samo czasu."""
    assert WAIT_SLICE_S < DEFAULT_LOCK_STALE_S
    assert WAIT_SLICE_S <= DEFAULT_LOCK_STALE_S / 2
