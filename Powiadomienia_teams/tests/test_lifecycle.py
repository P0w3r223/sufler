from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.config import OknoOdpowiedzi
from powiadomienia_teams.domain.models import TimeOff

# 0.2.19 zamieniło OKNO liczone od ostatniej aktywności na TERMIN KALENDARZOWY
# (`OknoOdpowiedzi`: początek tygodnia + offset, z dolną granicą kurtuazji od prośby BOTA).
# `past_hard_ceiling` zniknął razem z polityką kotwicy — patrz komentarz na końcu pliku.
from powiadomienia_teams.reminders.lifecycle import (
    ReadOutcome,
    is_expired,
    prune_terminal,
    przekroczyl_sufit,
    ready_for_self_fill_check,
    should_expire,
    still_writable,
    termin_odpowiedzi,
)
from powiadomienia_teams.state import (
    APPLIED,
    AWAITING_REPLY,
    EXPIRED,
    SELF_FILLED,
    PendingReminder,
)

UTC = timezone.utc
NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)  # czwartek przed tygodniem docelowym
WAW = ZoneInfo("Europe/Warsaw")
# Wartości domyślne z `config.from_env`: termin w poniedziałek 05:00 lokalnie, kurtuazja 24 h.
OKNO = OknoOdpowiedzi(offset_h=5, min_h=24, tz=WAW)
TERMIN = datetime(2026, 7, 20, 5, 0, tzinfo=WAW)  # week_start 2026-07-20 + 5 h


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _pending(status=AWAITING_REPLY, watermark="", nudged_at=""):
    return PendingReminder(
        member_id="u1",
        member_name="Ala",
        chat_id="c",
        week_start="2026-07-20",
        status=status,
        watermark=watermark,
        nudged_at=nudged_at,
    )


def test_termin_bierze_sie_z_KALENDARZA_a_nie_z_wieku_wpisu():
    """Sedno zmiany 0.2.13: termin wisi na tygodniu docelowym, nie na ostatniej aktywności.

    Okno liczone od aktywności zamykało się przy przebiegu w piątek już w NIEDZIELĘ — przed
    początkiem tygodnia, którego dotyczyło — więc człowiek siadający do grafiku w poniedziałek
    rano był po terminie, choć zachował się normalnie.
    """
    assert termin_odpowiedzi(_pending(nudged_at=_iso(NOW)), OKNO) == TERMIN


def test_po_terminie_kalendarzowym_wpis_jest_wygasly():
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=200)))
    assert is_expired(p, TERMIN + timedelta(minutes=1), OKNO) is True


def test_przed_terminem_wpis_nie_jest_wygasly():
    assert is_expired(_pending(nudged_at=_iso(NOW)), NOW, OKNO) is False


def test_aktywnosc_PRACOWNIKA_nie_przedluza_terminu():
    """Drugi defekt polityki kotwicy: okno przedłużało się własnym ogonem.

    Kotwicą było `max(watermark, bot_last_message_at, nudged_at)`, a bot odzywa się w otwartym
    temacie przy każdym doprecyzowaniu — więc rozmowa dożywała kolejnego piątku i zostawała
    nadpisana razem z uzgodnionym grafikiem. Kurtuazja liczy się dziś WYŁĄCZNIE od prośby bota.
    """
    swiezy_watermark = _pending(
        watermark=_iso(TERMIN + timedelta(hours=1)), nudged_at=_iso(NOW - timedelta(hours=200))
    )
    assert termin_odpowiedzi(swiezy_watermark, OKNO) == TERMIN
    assert is_expired(swiezy_watermark, TERMIN + timedelta(hours=2), OKNO) is True


def test_prosba_BOTA_odsuwa_termin_o_kurtuazje():
    """Jedyne, co zostało z kotwicy: nie zamykamy tematu zaraz po tym, jak bot o coś poprosił.

    Bez tego pending obsłużony po przestoju dłuższym niż tydzień dostawał prośbę o potwierdzenie
    i wygasał w kolejnym cyklu — po kilkunastu sekundach, bo obsłużona odpowiedź resetuje backoff.
    """
    prosba = TERMIN + timedelta(hours=10)
    p = _pending(nudged_at=_iso(prosba))
    assert termin_odpowiedzi(p, OKNO) == prosba + timedelta(hours=OKNO.min_h)
    assert is_expired(p, prosba + timedelta(hours=23), OKNO) is False
    assert is_expired(p, prosba + timedelta(hours=25), OKNO) is True


def test_nieczytelny_week_start_znaczy_ze_wpis_NIE_wygasa():
    """Brak wyznaczalnego terminu jest tak samo bezpieczny jak dawny brak kotwicy."""
    p = _pending()
    p.week_start = "nie-data"
    assert termin_odpowiedzi(p, OKNO) is None
    assert is_expired(p, datetime(2030, 1, 1, tzinfo=UTC), OKNO) is False


def test_prune_drops_old_terminal_keeps_fresh_and_open():
    state = {
        "old": _pending(status=APPLIED, watermark=_iso(NOW - timedelta(hours=200))),
        "fresh": _pending(status=EXPIRED, watermark=_iso(NOW - timedelta(hours=10))),
        "open": _pending(status=AWAITING_REPLY, watermark=_iso(NOW - timedelta(hours=500))),
    }
    kept = prune_terminal(state, NOW, retain_hours=48)
    # stary terminalny wyrzucony; otwarty (mimo wieku) i świeży terminalny zostają
    assert set(kept) == {"fresh", "open"}


def test_prune_keeps_terminal_without_anchor():
    state = {"noanchor": _pending(status=APPLIED)}  # brak kotwicy → nie znamy wieku
    assert prune_terminal(state, NOW, 48) == state


def test_prune_returns_new_dict_without_mutating_input():
    state = {"old": _pending(status=APPLIED, watermark=_iso(NOW - timedelta(hours=200)))}
    prune_terminal(state, NOW, 48)
    assert "old" in state  # wejście nietknięte


# --- should_expire: termin to za mało, potrzebny DOWÓD (ADR 0003) ---------------------------

_PO_TERMINIE = TERMIN + timedelta(hours=1)


def test_should_expire_only_on_successful_read_finding_nothing():
    p = _pending(nudged_at=_iso(NOW))
    assert should_expire(p, _PO_TERMINIE, OKNO, read=ReadOutcome.NOTHING_NEW) is True


def test_handled_reply_blocks_expiry_even_after_deadline():
    # Przestój dłuższy niż okno: odpowiedź czekała w czacie i właśnie została obsłużona. Wygaszenie
    # w tym samym przebiegu wysłałoby prośbę o potwierdzenie i zaraz po niej „brak odpowiedzi".
    p = _pending(nudged_at=_iso(NOW))
    assert should_expire(p, _PO_TERMINIE, OKNO, read=ReadOutcome.HANDLED) is False


def test_failed_read_blocks_expiry_even_after_deadline():
    # Brak dowodu to nie dowód braku — awaria odczytu nie może kosztować pracownika grafiku.
    p = _pending(nudged_at=_iso(NOW))
    assert should_expire(p, _PO_TERMINIE, OKNO, read=ReadOutcome.UNKNOWN) is False


def test_evidence_alone_does_not_expire_before_deadline():
    # Kontrola w drugą stronę: dowód bez upływu terminu też nie wygasza.
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=1)))
    assert should_expire(p, NOW, OKNO, read=ReadOutcome.NOTHING_NEW) is False


def test_obcy_nadawca_tez_blokuje_wygaszenie():
    """`BLOCKED` musi zachowywać się przy wygaszaniu DOKŁADNIE jak `UNKNOWN`.

    Rozdział na dwie wartości (ADR 0007) dotyczy licznika i sufitu, nie bezpieczeństwa. Gdyby
    `should_expire` przepuściło `BLOCKED`, cudza wiadomość w wątku kończyłaby się komunikatem
    „nie dostałem odpowiedzi" wysłanym pracownikowi, którego czatu nawet nie odczytaliśmy do końca
    — czyli dokładnie tym, czemu ADR 0003 ma zapobiegać.
    """
    p = _pending(nudged_at=_iso(NOW))
    assert should_expire(p, _PO_TERMINIE, OKNO, read=ReadOutcome.BLOCKED) is False


def test_kazdy_read_outcome_ma_rozstrzygniete_zachowanie_przy_wygaszaniu():
    """Strażnik na PRZYSZŁE wartości enuma, nie na obecne.

    `should_expire` jest napisane jako „wszystko poza NOTHING_NEW blokuje", więc piąta wartość
    dodana kiedyś przez kogoś odziedziczy zachowanie bezpieczne w ciszy — i to jest właściwy
    kierunek. Ten test pilnuje, żeby ta własność była ZAPISANA, a nie przypadkowa: gdyby ktoś
    przepisał funkcję na jawną listę dozwolonych wartości, nowa wartość musi tu zapalić czerwono.
    """
    p = _pending(nudged_at=_iso(NOW))
    przepuszczone = {
        outcome for outcome in ReadOutcome if should_expire(p, _PO_TERMINIE, OKNO, read=outcome)
    }
    assert przepuszczone == {ReadOutcome.NOTHING_NEW}


# --- przekroczyl_sufit: twardy sufit wieku wpisu (ADR 0007) ---------------------------------


def test_sufit_liczy_od_kotwicy_a_nie_od_terminu():
    """Sufit mierzy BEZRUCH rozmowy, więc kotwicą jest ostatnia aktywność, nie tydzień docelowy."""
    p = _pending(nudged_at=_iso(NOW))
    assert przekroczyl_sufit(p, NOW + timedelta(hours=143, minutes=59), 144) is False
    assert przekroczyl_sufit(p, NOW + timedelta(hours=144), 144) is True


def test_sufit_przesuwa_sie_z_ostatnia_wiadomoscia_bota():
    """Rozmowa żywa nie starzeje się: prośba bota jest kotwicą, więc odsuwa sufit.

    Bez tego sufit liczony od `nudged_at` zamykałby wpisy, w których bot dopiero co o coś prosił —
    a to są rozmowy W TOKU, nie martwe.
    """
    p = _pending(nudged_at=_iso(NOW))
    p.bot_last_message_at = _iso(NOW + timedelta(hours=100))
    assert przekroczyl_sufit(p, NOW + timedelta(hours=150), 144) is False


def test_sufit_bez_kotwicy_nie_zamyka():
    """Brak kotwicy = nie znamy wieku wpisu. Kierunek bezpieczny to zostawić go otwartym.

    Ta sama zasada co w `prune_terminal` i co w domyślnym `UNKNOWN` przy wygaszaniu: niewiedza
    usługi nigdy nie działa na niekorzyść pracownika.
    """
    p = _pending(nudged_at="")
    assert przekroczyl_sufit(p, NOW + timedelta(days=365), 144) is False


# --- still_writable: użyteczność zapisu ma własny termin (ADR 0003) -------------------------


def _zmiana(start_h: int, end_h: int) -> TimeOff:
    """Wpis z konkretnym oknem czasu. TimeOff wystarcza — filtr patrzy wyłącznie na `end`."""
    return TimeOff(
        "u1",
        NOW.replace(hour=0) + timedelta(hours=start_h),
        NOW.replace(hour=0) + timedelta(hours=end_h),
        "TOR_URLOP",
    )


def test_finished_entries_are_dropped():
    # NOW to 12:00. Wpis 8:00-10:00 już się skończył — w grafiku byłby fałszywym stanem faktycznym.
    assert still_writable([_zmiana(8, 10)], NOW) == ()


def test_entry_in_progress_is_kept():
    # 8:00-16:00 trwa w tej chwili: praca jest realna, więc wpis nadal wart zapisania. Kryterium
    # to KONIEC, nie początek — inaczej gubilibyśmy dzień, który właśnie się dzieje.
    wpis = _zmiana(8, 16)
    assert still_writable([wpis], NOW) == (wpis,)


def test_future_entries_are_kept():
    wpis = _zmiana(30, 38)  # jutro
    assert still_writable([wpis], NOW) == (wpis,)


def test_partially_past_week_keeps_only_the_rest():
    # Sedno decyzji: „tak" potwierdzone w środku tygodnia zapisuje RESZTĘ tygodnia, a nie nic
    # (jak przy zamykaniu całego tematu) i nie wszystko (jak przed tą zmianą).
    minione, trwajace, przyszle = _zmiana(0, 6), _zmiana(8, 16), _zmiana(30, 38)
    assert still_writable([minione, trwajace, przyszle], NOW) == (trwajace, przyszle)


def test_empty_input_gives_empty_result():
    # Pusty wynik jest sygnałem „nie ma czego zapisać" dla wołającego — nie może rzucać.
    assert still_writable([], NOW) == ()


# --- SELF_FILLED: nowy status terminalny --------------------------------------------------


def test_self_filled_is_terminal_and_pruned_like_others():
    state = {
        "old": _pending(status=SELF_FILLED, watermark=_iso(NOW - timedelta(hours=200))),
        "open": _pending(status=AWAITING_REPLY, watermark=_iso(NOW - timedelta(hours=500))),
    }
    kept = prune_terminal(state, NOW, retain_hours=48)
    assert set(kept) == {"open"}


def test_self_filled_kept_when_fresh():
    state = {"fresh": _pending(status=SELF_FILLED, watermark=_iso(NOW - timedelta(hours=10)))}
    assert prune_terminal(state, NOW, retain_hours=48) == state


# --- ready_for_self_fill_check --------------------------------------------------------------


def test_ready_for_self_fill_check_true_after_idle_threshold():
    p = _pending(nudged_at=_iso(NOW - timedelta(seconds=3601)))
    assert ready_for_self_fill_check(p, NOW, 3600) is True


def test_ready_for_self_fill_check_false_before_idle_threshold():
    p = _pending(nudged_at=_iso(NOW - timedelta(seconds=1000)))
    assert ready_for_self_fill_check(p, NOW, 3600) is False


def test_ready_for_self_fill_check_negative_min_idle_disables():
    p = _pending(nudged_at=_iso(NOW - timedelta(hours=1000)))
    assert ready_for_self_fill_check(p, NOW, -1) is False


def test_ready_for_self_fill_check_zero_checks_every_silent_cycle():
    p = _pending(nudged_at=_iso(NOW - timedelta(seconds=1)))
    assert ready_for_self_fill_check(p, NOW, 0) is True


def test_ready_for_self_fill_check_false_without_anchor():
    assert ready_for_self_fill_check(_pending(), NOW, 3600) is False


def test_ready_for_self_fill_check_watermark_extends_like_expiry():
    # Rozmowa w toku: nudge dawno, ostatnia aktywność świeża → liczone od aktywności (jak _anchor).
    p = _pending(
        watermark=_iso(NOW - timedelta(seconds=100)),
        nudged_at=_iso(NOW - timedelta(hours=100)),
    )
    assert ready_for_self_fill_check(p, NOW, 3600) is False


# --- Twardy sufit: czego 0.2.19 NIE ma i czym to zastąpiło ---------------------------------
#
# Linia repozytorium miała `past_hard_ceiling` (3 × okno) na jeden konkretny problem:
# `should_expire`
# słusznie odmawia wygaszenia bez udanego odczytu („brak dowodu ≠ dowód braku"), więc wpis, którego
# czatu trwale nie da się odczytać, nigdy nie stawał się terminalny, nigdy nie podlegał
# `prune_terminal`, a `run_once` co tydzień omijał tę osobę, bo jej wpis „istniał".
#
# W 0.2.19 sufitu nie ma, ale dziura jest domknięta z drugiej strony i wcześniej: po
# `listener._MAX_PENDING_FAILURES` nieudanych obiegach `_record_failure` PRZESUWA watermark
# i zamyka bramkę zapisu, więc kolejny obieg widzi `NOTHING_NEW` i termin kalendarzowy może
# orzec wygaśnięcie. Zamiast liczyć wielokrotność okna, liczymy nieudane próby — bliżej przyczyny.
#
# Testy tego mechanizmu należą do `test_app.py` (ścieżka listenera), nie tutaj: `lifecycle` jest
# czystą arytmetyką terminu i o nieudanych odczytach nic nie wie.


def test_termin_kalendarzowy_sam_z_siebie_nie_wisi_na_kotwicy():
    """Kontrola pozostała po `past_hard_ceiling`: sam upływ terminu nie zależy od stanu rozmowy.

    Wpis bez jednego znacznika czasu (żadnej aktywności, żadnej prośby) i tak ma termin, bo bierze
    go z tygodnia docelowego. W polityce kotwicy taki wpis nie wygasał NIGDY.
    """
    goly = _pending()  # watermark="" i nudged_at=""
    assert termin_odpowiedzi(goly, OKNO) == TERMIN
    assert is_expired(goly, TERMIN + timedelta(seconds=1), OKNO) is True


def test_zly_typ_week_start_nie_wywraca_wygaszania():
    """Siatka na wołającego, który zbuduje `PendingReminder` z pominięciem `state._wczytaj`.

    `should_expire` jest wołane w list-comprehension kroku 2 `poll_replies` — POZA izolacją
    per-osoba — więc wyjątek stąd kładł cały obieg nasłuchu deterministycznie, w każdym ticku,
    przy bijącym pulsie i zielonym healthchecku. Brak terminu ma znaczyć „nie wygaszam",
    a nie „przewracam usługę".
    """
    chory = PendingReminder(
        member_id="u1",
        member_name="Ala",
        chat_id="c",
        week_start=20260720,  # type: ignore[arg-type]  # celowo zły typ — to jest przedmiot testu
        status=AWAITING_REPLY,
        nudged_at=_iso(NOW),
    )
    assert termin_odpowiedzi(chory, OKNO) is None
    assert should_expire(chory, NOW, OKNO, read=ReadOutcome.NOTHING_NEW) is False


def test_zly_typ_znacznika_nie_wywraca_kotwicy():
    """`_najpozniejszy` pomija nieparsowalne znaczniki — „nieparsowalne" obejmuje ZŁY TYP.

    `parse_graph_datetime(123)` nie rzuca `ValueError`, tylko `AttributeError`, a `if not iso`
    przepuszcza każdą niezerową liczbę. Kotwica karmi `ready_for_self_fill_check` (krok 1.5)
    i `prune_terminal`, więc wyjątek stąd kładł obieg tak samo jak zły `week_start`.
    """
    chory = PendingReminder(
        member_id="u1",
        member_name="Ala",
        chat_id="c",
        week_start="2026-07-20",
        status=AWAITING_REPLY,
        watermark=17530000,  # type: ignore[arg-type]  # celowo zły typ
        nudged_at=_iso(NOW),
    )
    # Kotwicą zostaje zdrowy `nudged_at`; zepsuty znacznik jest pomijany, nie wywraca wywołania.
    assert ready_for_self_fill_check(chory, NOW + timedelta(hours=2), 3600) is True
