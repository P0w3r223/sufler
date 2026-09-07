import logging
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from powiadomienia_teams.agent.interpreter import (
    OdpowiedzLlm,
    ReplyDecision,
    _coerce_weekday,
    build_schedule,
    build_time_offs,
    interpret_reply,
)
from powiadomienia_teams.domain.models import Shift, WeekSchedule

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")


# Port modelu w 0.2.19 to `LlmClient.rozmawiaj` zwracające `OdpowiedzLlm` (tura z ewentualnymi
# wywołaniami narzędzi), a nie `complete()` zwracające napis. `OdpowiedzLlm` jest celowo niezależne
# od SDK dostawcy, więc atrapa nie potrzebuje pakietu `anthropic` — patrz docstring tej klasy.
class _FakeLlm:
    def __init__(self, response: str) -> None:
        self._response = response

    def rozmawiaj(
        self,
        *,
        system: str,
        wiadomosci: list[dict[str, Any]],
        narzedzia: list[dict[str, Any]],
        schemat: dict[str, Any],
    ) -> OdpowiedzLlm:
        return OdpowiedzLlm(tekst=self._response, zatrzymanie="end_turn", tokeny_wyjscia=1)


class _RecordingLlm(_FakeLlm):
    """Atrapa, która zapamiętuje ostatnie wywołanie — do sprawdzania, co trafia w payloadzie."""

    def __init__(self, response: str) -> None:
        super().__init__(response)
        self.last_system: str | None = None
        self.last_wiadomosci: list[dict[str, Any]] | None = None
        self.last_narzedzia: list[dict[str, Any]] | None = None

    def rozmawiaj(
        self,
        *,
        system: str,
        wiadomosci: list[dict[str, Any]],
        narzedzia: list[dict[str, Any]],
        schemat: dict[str, Any],
    ) -> OdpowiedzLlm:
        self.last_system = system
        self.last_wiadomosci = wiadomosci
        self.last_narzedzia = narzedzia
        return super().rozmawiaj(
            system=system, wiadomosci=wiadomosci, narzedzia=narzedzia, schemat=schemat
        )

    @property
    def last_user(self) -> str:
        """Cała treść tury użytkownika — zszyta, bo payload jest teraz listą bloków, nie napisem."""
        czesci: list[str] = []
        for wiadomosc in self.last_wiadomosci or []:
            tresc = wiadomosc.get("content")
            if isinstance(tresc, str):
                czesci.append(tresc)
            elif isinstance(tresc, list):
                czesci += [str(blok.get("text", "")) for blok in tresc if isinstance(blok, dict)]
        return "\n".join(czesci)


def _proposal() -> WeekSchedule:
    # poniedziałek 08:00–16:00 lokalnie (06:00Z–14:00Z latem)
    shift = Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC))
    return WeekSchedule("u1", date(2026, 7, 20), (shift,))


def test_confirm_returns_schedule():
    llm = _FakeLlm('{"action":"confirm","shifts":[{"weekday":0,"start":"08:00","end":"16:00"}]}')
    decision = interpret_reply(_proposal(), "ok", tz=WAW, group_id="TAG", llm=llm)
    assert decision.action == "confirm"
    assert decision.schedule is not None
    assert len(decision.schedule.shifts) == 1
    assert decision.schedule.shifts[0].start.astimezone(WAW).hour == 8
    assert decision.schedule.shifts[0].scheduling_group_id == "TAG"


def test_modify_returns_new_schedule():
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":4,"start":"10:00","end":"20:00"}],'
        '"note":"tylko piątek"}'
    )
    decision = interpret_reply(_proposal(), "tylko piątek 10-20", tz=WAW, group_id=None, llm=llm)
    assert decision.action == "modify"
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].start.astimezone(WAW).weekday() == 4
    assert decision.schedule.shifts[0].start.astimezone(WAW).hour == 10


def test_modify_from_empty_proposal_single_day():
    # REGRESJA: pusty gotowiec (pracownik bez zmian w zeszłym tygodniu) + poprawny grafik OD ZERA.
    # Jeden dzień MUSI dać modify, nie unclear — wcześniej bot w kółko odpowiadał „nie zrozumiałem"
    # (przyczyna była w PROMPCIE; ten test broni ścieżki KODU: single-day modify przy pustym gotowcu
    # nie może zostać zdegradowane do unclear). Prompt weryfikuje live-smoke (patrz PLAN.md).
    empty = WeekSchedule("u1", date(2026, 7, 20), ())
    llm = _FakeLlm('{"action":"modify","shifts":[{"weekday":1,"start":"12:00","end":"21:00"}]}')
    decision = interpret_reply(empty, "wtorek 12-21", tz=WAW, group_id="TAG", llm=llm)
    assert decision.action == "modify"
    assert decision.schedule is not None and not decision.schedule.is_empty
    assert len(decision.schedule.shifts) == 1
    assert decision.schedule.shifts[0].start.astimezone(WAW).weekday() == 1
    assert decision.schedule.shifts[0].start.astimezone(WAW).hour == 12
    # Brak gotowca → theme None; domyślny kolor (green) nada dopiero create_shift przy zapisie.
    assert decision.schedule.shifts[0].theme is None


def test_decline_has_no_schedule():
    decision = interpret_reply(
        _proposal(), "nie pracuję", tz=WAW, group_id=None, llm=_FakeLlm('{"action":"decline"}')
    )
    assert decision.action == "decline"
    assert decision.schedule is None


def test_koniec_przed_poczatkiem_to_zmiana_NOCNA_a_nie_blad():
    """20:00→08:00 znaczy „przez północ", nie „interwał odwrócony".

    Linia repozytorium odrzucała taki wpis i degradowała całą odpowiedź do »unclear«. W pracy
    zmianowej — czyli w jedynym zastosowaniu tej usługi — nocka jest normą, więc odrzucenie
    kasowało poprawny grafik i kazało pracownikowi tłumaczyć się drugi raz.
    """
    llm = _FakeLlm('{"action":"modify","shifts":[{"weekday":0,"start":"20:00","end":"08:00"}]}')
    decision = interpret_reply(_proposal(), "nocka w poniedziałek", tz=WAW, group_id=None, llm=llm)
    assert decision.action == "modify"
    assert decision.schedule is not None
    (zmiana,) = decision.schedule.shifts
    assert zmiana.start.astimezone(WAW).hour == 20
    assert zmiana.end.astimezone(WAW).hour == 8
    assert zmiana.end.astimezone(WAW).day == zmiana.start.astimezone(WAW).day + 1


def test_json_owiniety_proza_degraduje_do_unclear():
    """0.2.19 NIE wyłuskuje już JSON-a z prozy — i to jest świadome, nie przeoczenie.

    Kształt wyjścia gwarantuje `output_config.format` (wyjście strukturalne), więc model nie ma
    czym owinąć odpowiedzi; luźne wyszukiwanie nawiasów było siatką pod problem, którego API już
    nie dopuszcza, a samo potrafiło trafić w JSON zacytowany przez PRACOWNIKA.

    Zależność jest jednak realna: gdyby ktoś zdjął `output_config.format`, ten test zacznie
    opisywać awarię, a nie kontrakt — dlatego stoi tu jawnie, a nie został skasowany.
    """
    llm = _FakeLlm(
        'Jasne! {"action":"confirm","shifts":[{"weekday":0,"start":"08:00","end":"16:00"}]} gotowe'
    )
    decision = interpret_reply(_proposal(), "ok", tz=WAW, group_id=None, llm=llm)
    assert decision.action == "unclear"


def test_build_schedule_respects_local_time():
    schedule = build_schedule(
        "u1", date(2026, 7, 20), [{"weekday": 0, "start": "08:00", "end": "16:00"}], WAW, "TAG"
    )
    assert schedule.shifts[0].start.astimezone(WAW).hour == 8
    assert schedule.shifts[0].end.astimezone(WAW).hour == 16


def test_confirm_copies_theme_per_weekday_from_proposal():
    # gotowiec: poniedziałek z kolorem green; model zwraca shifts BEZ theme → kolor kopiowany
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm('{"action":"confirm","shifts":[{"weekday":0,"start":"08:00","end":"16:00"}]}')
    decision = interpret_reply(proposal, "ok", tz=WAW, group_id="TAG", llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"


def test_reply_sets_mode_zdalnie_to_blue():
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00","tryb":"zdalnie"}]}'
    )
    decision = interpret_reply(proposal, "w pon zdalnie", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "blue"


def test_reply_sets_mode_stacjonarnie_to_green():
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="blue",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00",'
        '"tryb":"stacjonarnie"}]}'
    )
    decision = interpret_reply(proposal, "w pon do biura", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"


# Testy kontraktowe (dokumentują mapowanie action → decyzja/grafik/czas wolny). LLM jest atrapą,
# więc NIE weryfikują samego promptu — czy realny model faktycznie tak sklasyfikuje urlop sprawdza
# smoke na żywym modelu (patrz PLAN.md / docs live-smoke). Chronią przed regresją logiki
# interpret_reply/build_schedule/build_time_offs dla scenariuszy nieobecności.
def test_whole_week_vacation_creates_time_off():
    # „urlop cały tydzień" → modify: shifts=[], time_off dla pon–pt z powodem urlop
    llm = _FakeLlm(
        '{"action":"modify","shifts":[],"time_off":['
        '{"weekday":0,"powod":"urlop"},{"weekday":1,"powod":"urlop"},'
        '{"weekday":2,"powod":"urlop"},{"weekday":3,"powod":"urlop"},'
        '{"weekday":4,"powod":"urlop"}]}'
    )
    decision = interpret_reply(_proposal(), "cały tydzień urlop", tz=WAW, group_id="TAG", llm=llm)
    assert decision.action == "modify"
    assert decision.schedule is not None and decision.schedule.is_empty
    assert {t["weekday"] for t in decision.time_off} == {0, 1, 2, 3, 4}
    assert all(t["powod"] == "urlop" for t in decision.time_off)


def test_partial_absence_moves_day_to_time_off():
    # „w piątek mnie nie będzie" → pon–czw praca, piątek do time_off (nieobecność)
    llm = _FakeLlm(
        '{"action":"modify","shifts":['
        '{"weekday":0,"start":"08:00","end":"16:00"},'
        '{"weekday":1,"start":"08:00","end":"16:00"},'
        '{"weekday":2,"start":"08:00","end":"16:00"},'
        '{"weekday":3,"start":"08:00","end":"16:00"}],'
        '"time_off":[{"weekday":4,"powod":"nieobecność"}]}'
    )
    decision = interpret_reply(
        _proposal(), "w piątek mnie nie będzie, reszta tak samo", tz=WAW, group_id="TAG", llm=llm
    )
    assert decision.action == "modify"
    assert decision.schedule is not None
    weekdays = {s.start.astimezone(WAW).weekday() for s in decision.schedule.shifts}
    assert weekdays == {0, 1, 2, 3}  # dni pracujące
    assert decision.time_off == ({"weekday": 4, "powod": "nieobecność"},)


def test_ambiguous_absence_is_unclear():
    # „nie będzie mnie kilka dni" bez wskazania których → unclear (model nie zgaduje dni)
    llm = _FakeLlm('{"action":"unclear","shifts":[],"time_off":[]}')
    decision = interpret_reply(
        _proposal(), "nie będzie mnie kilka dni", tz=WAW, group_id="TAG", llm=llm
    )
    assert decision.action == "unclear"
    assert decision.schedule is None
    assert decision.time_off == ()


def test_day_in_both_shifts_and_time_off_time_off_wins():
    # Model zwrócił piątek i w shifts, i w time_off → dedup: piątek tylko jako wolne
    llm = _FakeLlm(
        '{"action":"modify","shifts":['
        '{"weekday":0,"start":"08:00","end":"16:00"},'
        '{"weekday":4,"start":"08:00","end":"16:00"}],'
        '"time_off":[{"weekday":4,"powod":"urlop"}]}'
    )
    decision = interpret_reply(_proposal(), "w piątek urlop", tz=WAW, group_id="TAG", llm=llm)
    assert decision.schedule is not None
    weekdays = {s.start.astimezone(WAW).weekday() for s in decision.schedule.shifts}
    assert 4 not in weekdays  # piątek usunięty z grafiku pracy
    assert decision.time_off == ({"weekday": 4, "powod": "urlop"},)


def test_build_time_offs_spans_full_day_from_resolved_reason():
    # entries mają już rozstrzygnięte reason_id (etap potwierdzenia)
    offs = build_time_offs("u1", date(2026, 7, 20), [{"weekday": 4, "reason_id": "TOR_L4"}], WAW)
    assert len(offs) == 1
    assert offs[0].reason_id == "TOR_L4"
    assert offs[0].user_id == "u1"
    assert offs[0].start.astimezone(WAW).weekday() == 4
    assert offs[0].start.astimezone(WAW).hour == 0  # całodobowy blok od lokalnej północy


def test_build_time_offs_skips_entry_without_reason_id():
    offs = build_time_offs("u1", date(2026, 7, 20), [{"weekday": 4, "reason_id": ""}], WAW)
    assert offs == []


# --- Regresja: deterministyczne mapowanie nazwy dnia → weekday -------------------------------
# Model bywa zawodny w liczeniu weekday (potrafił zwrócić 4=piątek dla „czwartek”), więc dzień
# podaje jako NAZWĘ, a kod mapuje ją deterministycznie. Poniższe testy bronią ścieżki KODU.


def test_build_schedule_maps_day_name_to_weekday():
    schedule = build_schedule(
        "u1", date(2026, 7, 20), [{"dzien": "czwartek", "start": "08:00", "end": "16:00"}], WAW, "T"
    )
    assert len(schedule.shifts) == 1
    assert schedule.shifts[0].start.astimezone(WAW).weekday() == 3  # czwartek, nie piątek


def test_day_name_beats_wrong_numeric_weekday():
    # Nazwa jest źródłem prawdy: gdy model poda sprzeczne dzien+weekday, wygrywa nazwa.
    schedule = build_schedule(
        "u1",
        date(2026, 7, 20),
        [{"dzien": "czwartek", "weekday": 4, "start": "08:00", "end": "16:00"}],
        WAW,
        "T",
    )
    assert schedule.shifts[0].start.astimezone(WAW).weekday() == 3


def test_build_schedule_falls_back_to_numeric_weekday():
    # Wsteczna kompatybilność: brak nazwy → użyj liczbowego weekday.
    schedule = build_schedule(
        "u1", date(2026, 7, 20), [{"weekday": 3, "start": "08:00", "end": "16:00"}], WAW, "T"
    )
    assert schedule.shifts[0].start.astimezone(WAW).weekday() == 3


def test_interpret_reply_maps_day_name_from_model():
    empty = WeekSchedule("u1", date(2026, 7, 20), ())
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"dzien":"czwartek","start":"08:00","end":"16:00"}]}'
    )
    decision = interpret_reply(empty, "czw 8-16", tz=WAW, group_id="TAG", llm=llm)
    assert decision.action == "modify"
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].start.astimezone(WAW).weekday() == 3


def test_theme_copied_from_proposal_when_model_returns_day_name():
    # Kopiowanie koloru z gotowca (_theme_for) przechodzi teraz przez _coerce_weekday — gdy model
    # zwraca dzień po NAZWIE (bez liczbowego weekday), kolor musi się i tak dobrać po dniu.
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"confirm","shifts":[{"dzien":"poniedziałek","start":"08:00","end":"16:00"}]}'
    )
    decision = interpret_reply(proposal, "ok", tz=WAW, group_id="TAG", llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"  # skopiowany z gotowca po nazwie dnia


def test_time_off_accepts_day_name():
    empty = WeekSchedule("u1", date(2026, 7, 20), ())
    llm = _FakeLlm(
        '{"action":"modify","shifts":[],"time_off":[{"dzien":"piątek","powod":"urlop"}]}'
    )
    decision = interpret_reply(empty, "w piątek urlop", tz=WAW, group_id="TAG", llm=llm)
    assert decision.time_off == ({"weekday": 4, "powod": "urlop"},)


def test_coerce_weekday_name_and_abbrev_and_numeric():
    assert _coerce_weekday({"dzien": "poniedziałek"}) == 0
    assert _coerce_weekday({"dzien": "CZWARTEK"}) == 3  # case-insensitive
    assert _coerce_weekday({"dzien": "czw"}) == 3  # skrót
    assert _coerce_weekday({"weekday": 5}) == 5  # fallback liczbowy
    assert _coerce_weekday({"dzien": "blursday"}) is None  # nieznana nazwa
    assert _coerce_weekday({"weekday": 9}) is None  # poza zakresem
    assert _coerce_weekday({}) is None


# --- Regresja: odporność na niepoprawny JSON z modelu ---------------------------------------
# Model potrafił wstawić nieoescapowany cudzysłów (mylił polski „ ” z ASCII ") w wolnym tekście
# i zwrócić niepoprawny JSON. Wcześniej leciał niekontrolowany JSONDecodeError; teraz jedna zła
# odpowiedź degraduje się do unclear, więc listener obsłuży ją, a nie wywróci.


def test_interpret_reply_malformed_json_falls_back_to_unclear():
    # Nieoescapowany ASCII " wewnątrz stringa → niepoprawny JSON.
    llm = _FakeLlm('{"action":"modify","shifts":[],"note":"mówi "albo nie" i tyle"}')
    decision = interpret_reply(_proposal(), "cokolwiek", tz=WAW, group_id="TAG", llm=llm)
    assert decision.action == "unclear"
    assert decision.schedule is None


def test_interpret_reply_no_json_at_all_is_unclear():
    decision = interpret_reply(
        _proposal(), "cokolwiek", tz=WAW, group_id="TAG", llm=_FakeLlm("brak json tutaj")
    )
    assert decision.action == "unclear"
    assert decision.schedule is None


def _shape(payload: str) -> ReplyDecision:
    return interpret_reply(_proposal(), "cokolwiek", tz=WAW, group_id="TAG", llm=_FakeLlm(payload))


def test_wrong_json_shape_degrades_to_unclear():
    """Poprawny SKŁADNIOWO, zły STRUKTURALNIE JSON nie może rzucać wyjątku.

    Watermark przesuwa się dopiero po udanej obsłudze, więc wyjątek tutaj oznaczałby, że ta sama
    wiadomość wraca w każdym ticku aż do wygaśnięcia okna — dziesiątki wywołań modelu, a na koniec
    nieprawdziwe „nie dostałem odpowiedzi" do osoby, która przecież odpisała.
    """
    for payload in (
        '{"action":"modify","shifts":"poniedzialek 8-16","time_off":[]}',
        '{"action":"modify","shifts":{"dzien":"wtorek"},"time_off":[]}',
        '{"action":"modify","shifts":[],"time_off":{"dzien":"piatek"}}',
        '{"action":"modify","shifts":[["wtorek","08:00","16:00"]],"time_off":[]}',
    ):
        decision = _shape(payload)
        assert decision.action == "unclear", payload
        assert decision.schedule is None


def test_bad_entries_are_skipped_not_whole_answer():
    """Zły POJEDYNCZY wpis pomijamy (jak zawsze), zły KONTENER degraduje całość."""
    decision = _shape(
        '{"action":"modify","time_off":[],'
        '"shifts":[{"dzien":"wtorek","start":"08:00","end":"16:00"},"śmieć"]}'
    )
    assert decision.action == "modify"
    assert decision.schedule is not None
    assert len(decision.schedule.shifts) == 1  # poprawny wpis przeżył


# --- Pamięć rozmowy: `history` w payloadzie ---------------------------------------------------


def test_history_omitted_from_payload_when_none():
    """Bez historii payload jest bajt-w-bajt jak dotąd — zgodność wsteczna z istniejącymi
    testami."""
    llm = _RecordingLlm('{"action":"decline"}')
    interpret_reply(_proposal(), "nie", tz=WAW, group_id=None, llm=llm)
    assert llm.last_user is not None
    assert "historia_pracownika" not in llm.last_user


def test_history_omitted_from_payload_when_empty_list():
    llm = _RecordingLlm('{"action":"decline"}')
    interpret_reply(_proposal(), "nie", tz=WAW, group_id=None, llm=llm, history=[])
    assert llm.last_user is not None
    assert "historia_pracownika" not in llm.last_user


def test_history_included_in_payload_oldest_to_newest():
    llm = _RecordingLlm('{"action":"decline"}')
    interpret_reply(
        _proposal(),
        "i tyle",
        tz=WAW,
        group_id=None,
        llm=llm,
        history=["pon-pt 8-16", "a piątek zdalnie"],
    )
    assert llm.last_user is not None
    assert "historia_pracownika" in llm.last_user
    # Kolejność zachowana (najstarsza→najnowsza) i treść obu wiadomości obecna.
    idx_pon = llm.last_user.index("pon-pt 8-16")
    idx_piatek = llm.last_user.index("a piątek zdalnie")
    assert idx_pon < idx_piatek


def test_system_prompt_mentions_history_and_memory_limits():
    llm = _RecordingLlm('{"action":"decline"}')
    interpret_reply(_proposal(), "nie", tz=WAW, group_id=None, llm=llm, history=["x"])
    assert llm.last_system is not None
    assert "historia_pracownika" in llm.last_system
    assert "10" in llm.last_system  # limit liczby zapamiętanych wiadomości


# --- Tryb pracy podany kolorem/emotką, nie tylko słowem ----------------------------------------


def test_reply_sets_mode_from_green_emoji():
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="blue",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00","tryb":"🟢"}]}'
    )
    decision = interpret_reply(proposal, "w pon 🟢", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"


def test_reply_sets_mode_from_blue_emoji():
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00","tryb":"🔵"}]}'
    )
    decision = interpret_reply(proposal, "w pon 🔵", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "blue"


def test_reply_sets_mode_from_color_word_niebieski():
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="green",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00",'
        '"tryb":"niebieski"}]}'
    )
    decision = interpret_reply(proposal, "w pon niebieski", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "blue"


def test_reply_sets_mode_from_color_word_zielono():
    shift = Shift(
        "u1",
        datetime(2026, 7, 20, 6, tzinfo=UTC),
        datetime(2026, 7, 20, 14, tzinfo=UTC),
        theme="blue",
    )
    proposal = WeekSchedule("u1", date(2026, 7, 20), (shift,))
    llm = _FakeLlm(
        '{"action":"modify","shifts":[{"weekday":0,"start":"09:00","end":"17:00",'
        '"tryb":"zielono"}]}'
    )
    decision = interpret_reply(proposal, "w pon na zielono", tz=WAW, group_id=None, llm=llm)
    assert decision.schedule is not None
    assert decision.schedule.shifts[0].theme == "green"


def test_zly_json_nie_wypuszcza_calego_wyjscia_modelu_do_logu(caplog):
    """Wyjście modelu to przetworzona WIADOMOŚĆ PRACOWNIKA — log dostaje skrót, nie całość.

    Powód urlopu, sprawa rodzinna czy stan zdrowia potrafią przejść przez model i wrócić w jego
    odpowiedzi. Log usługi bywa zbierany centralnie i czytany przez ludzi spoza zespołu, więc
    trafia tam tyle, ile trzeba do rozpoznania „model systematycznie psuje JSON": powód, długość
    i krótki początek. Wcześniej szło 200 znaków w treści wyjątku PLUS pełny ślad stosu
    (`exc_info=True`), którego `JSONDecodeError` ciągnie razem z całym dokumentem.
    """
    wrazliwe = "leczenie onkologiczne w klinice w Gliwicach"
    surowe = "Nie mogę zwrócić JSON. " + ("x" * 300) + " " + wrazliwe
    with caplog.at_level(logging.WARNING):
        decision = interpret_reply(
            _proposal(), "cokolwiek", tz=WAW, group_id="TAG", llm=_FakeLlm(surowe)
        )
    assert decision.action == "unclear"
    zapis = caplog.text
    assert wrazliwe not in zapis
    assert "x" * 200 not in zapis  # skrót, nie 200-znakowy wycinek jak dotąd
    # 0.2.19 idzie dalej niż wymagał ten test: do logu nie trafia Z WYJŚCIA MODELU NIC — ani
    # wycinek, ani długość. Diagnostykę „model systematycznie psuje JSON" niesie druga gałąź
    # (`stop_reason` + liczba tokenów wyjścia przy pustym tekście), więc wymaganie jest spełnione
    # innym środkiem, a nie porzucone.
    assert "nie zwrócił poprawnego JSON" in zapis
    assert surowe[:40] not in zapis


# --- Nocka: cała droga, od interwału do opisu ---------------------------------
#
# Ten test istnieje, bo `deploy/README-docker.md` przez jedenaście wydań twierdził, że zmiana
# przez północ „traci informację o przejściu doby i jest po cichu pomijana". Nieprawda była
# w każdym członie — a przeżyła tak długo, bo ŻADEN test nie przechodził tej drogi w całości:
# osobno sprawdzano budowę zmiany, osobno round-trip, osobno opis.


def test_nocka_przechodzi_cala_droge_do_opisu():
    """22:00–06:00 z piątku: koniec wypada w SOBOTĘ, round-trip wierny, opis mówi »do soboty«."""
    from powiadomienia_teams.agent.interpreter import schedule_to_intervals
    from powiadomienia_teams.messages import describe_schedule

    interwaly = [{"dzien": "piątek", "start": "22:00", "end": "06:00"}]
    grafik = build_schedule("u1", date(2026, 9, 14), interwaly, WAW, "grupa-1")

    assert len(grafik.shifts) == 1, "nocka została pominięta przy budowie"
    zmiana = grafik.shifts[0]
    start_lokalnie = zmiana.start.astimezone(WAW)
    koniec_lokalnie = zmiana.end.astimezone(WAW)
    assert start_lokalnie.date() == date(2026, 9, 18), "dzień przypisania to dzień ROZPOCZĘCIA"
    assert koniec_lokalnie.date() == date(2026, 9, 19), (
        "koniec nie został przewinięty na następny dzień"
    )
    assert (zmiana.end - zmiana.start).total_seconds() == 8 * 3600

    # Round-trip: stan trzyma interwały, więc rozjazd tych dwóch funkcji znaczyłby, że wpis
    # zapisany w piątek nie da się odtworzyć w poniedziałek.
    z_powrotem = schedule_to_intervals(grafik, WAW)
    assert z_powrotem == [{"weekday": 4, "start": "22:00", "end": "06:00", "theme": None}]
    assert (
        build_schedule("u1", date(2026, 9, 14), z_powrotem, WAW, "grupa-1").shifts == grafik.shifts
    )

    opis = describe_schedule(grafik, WAW)
    assert "22:00" in opis and "06:00" in opis
    assert "sobot" in opis.lower(), f"opis nie mówi, że zmiana kończy się nazajutrz: {opis!r}"


def test_zrownane_godziny_to_nie_nocka_tylko_sprzecznosc():
    """„8–8" nie znaczy doby pracy w realnej rozmowie — wpis ma odpaść, nie urosnąć do 24 h."""
    grafik = build_schedule(
        "u1", date(2026, 9, 14), [{"dzien": "piątek", "start": "08:00", "end": "08:00"}], WAW, None
    )
    assert grafik.is_empty
