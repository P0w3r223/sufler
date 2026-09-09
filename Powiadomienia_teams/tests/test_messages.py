from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from powiadomienia_teams.domain.models import Member, Shift, WeekSchedule
from powiadomienia_teams.messages import (
    LiczbyTygodnia,
    build_confirm_text,
    build_nudge_text,
    build_przypomnienie_text,
    build_self_filled_text,
    build_summary_text,
    describe_schedule,
    describe_time_off,
    to_html,
)

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")


def test_nudge_with_proposal_lists_days_in_local_time():
    member = Member("u1", "Mikołaj Anonimowicz")
    # 06:00Z–14:00Z latem = 08:00–16:00 czasu lokalnego
    proposal = WeekSchedule(
        "u1",
        date(2026, 7, 20),
        (Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC)),),
    )
    text = build_nudge_text(member, proposal, "20.07–26.07", WAW)
    assert "Mikołaj" in text
    assert "poniedziałek" in text
    assert "08:00–16:00" in text
    assert "ok" in text.lower()


def test_nudge_without_proposal_asks_for_hours():
    member = Member("u1", "Ala Kowalska")
    text = build_nudge_text(member, WeekSchedule("u1", date(2026, 7, 20)), "20.07–26.07", WAW)
    assert "Ala" in text
    assert "napisz" in text.lower()


def test_to_html_escapes_and_breaks_lines():
    html = to_html("linia 1\n<b>x</b>")
    assert "<br>" in html
    assert "&lt;b&gt;" in html
    assert "<b>" not in html


def test_describe_time_off_uses_actual_reason_name():
    entries = [
        {"weekday": 4, "reason_id": "TOR_U", "reason_name": "Urlop"},
        {"weekday": 1, "reason_id": "TOR_L4", "reason_name": "Zwolnienie lekarskie"},
    ]
    assert describe_time_off(entries) == "pt: Urlop, wt: Zwolnienie lekarskie"


def test_describe_schedule_uses_color_emoji_not_words():
    green = Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC))
    blue = Shift(
        "u1",
        datetime(2026, 7, 21, 6, tzinfo=UTC),
        datetime(2026, 7, 21, 14, tzinfo=UTC),
        theme="blue",
    )
    text = describe_schedule(WeekSchedule("u1", date(2026, 7, 20), (green, blue)), WAW)
    assert "🟢" in text  # stacjonarnie (green/None)
    assert "🔵" in text  # zdalnie (blue)
    assert "stacjonarnie" not in text and "zdalnie" not in text  # bez słów w nawiasach


def test_confirm_text_mentions_schedule_and_time_off():
    schedule = WeekSchedule(
        "u1",
        date(2026, 7, 20),
        (Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC)),),
    )
    time_off = [{"weekday": 4, "reason_id": "TOR_U", "reason_name": "Urlop"}]
    text = build_confirm_text(schedule, time_off, WAW)
    assert "grafik:" in text
    assert "czas wolny: pt: Urlop" in text
    assert "Potwierdź" in text


def test_build_self_filled_text_mentions_week_and_thanks():
    text = build_self_filled_text("20.07–26.07")
    assert "20.07–26.07" in text
    assert "uzupełniony" in text.lower()
    assert "dzięku" in text.lower()


def test_nudge_mentions_known_off_weekdays_and_excludes_them_from_ask():
    member = Member("u1", "Ala Kowalska")
    text = build_nudge_text(
        member, WeekSchedule("u1", date(2026, 7, 20)), "20.07–26.07", WAW, off_weekdays=[4]
    )
    assert "piątek" in text
    assert "wolne" in text.lower()
    assert "pozostałe dni" in text.lower()


def test_nudge_without_off_weekdays_keeps_original_wording():
    member = Member("u1", "Ala Kowalska")
    text = build_nudge_text(member, WeekSchedule("u1", date(2026, 7, 20)), "20.07–26.07", WAW)
    assert "wolne" not in text.lower()
    assert "napisz proszę, kiedy pracujesz (np" in text


def test_nudge_with_proposal_and_off_weekdays_still_lists_shifts():
    member = Member("u1", "Mikołaj Anonimowicz")
    proposal = WeekSchedule(
        "u1",
        date(2026, 7, 20),
        (Shift("u1", datetime(2026, 7, 20, 6, tzinfo=UTC), datetime(2026, 7, 20, 14, tzinfo=UTC)),),
    )
    text = build_nudge_text(member, proposal, "20.07–26.07", WAW, off_weekdays=[4])
    assert "poniedziałek" in text
    assert "piątek" in text  # wspomniane jako dzień wolny


# 0.2.19 rozbija podsumowanie PO TYGODNIU DOCELOWYM (`LiczbyTygodnia`) zamiast podawać jedną
# zbiorczą sumę. Powód: suma mieszała świeże `awaiting_reply` z tygodnia właśnie otwartego
# z terminalnymi resztkami tygodnia zamykanego, więc „oczekuje: 4" nie mówiło administratorowi
# tego jednego, po co czyta ten raport — czy poprzedni tydzień się domknął.


def test_summary_text_includes_self_filled_count():
    text = build_summary_text(
        tygodnie=[LiczbyTygodnia(week_start="2026-07-20", oczekuje=1, zapisane=2, samodzielne=3)],
        nastepny_przebieg="2026-07-24 16:00",
    )
    assert "uzupełnione samodzielnie: 3" in text


def test_summary_text_self_filled_defaults_to_zero():
    text = build_summary_text(
        tygodnie=[LiczbyTygodnia(week_start="2026-07-20")],
        nastepny_przebieg="2026-07-24 16:00",
    )
    assert "uzupełnione samodzielnie: 0" in text


def test_summary_text_rozbija_liczby_po_tygodniach_od_najnowszego():
    """Sedno zmiany: dwa tygodnie w toku mają być dwoma blokami, a nie jedną sumą."""
    text = build_summary_text(
        tygodnie=[
            LiczbyTygodnia(week_start="2026-07-13", zapisane=5),
            LiczbyTygodnia(week_start="2026-07-20", oczekuje=2),
        ],
        nastepny_przebieg="2026-07-24 16:00",
    )
    assert text.index("2026-07-20") < text.index("2026-07-13")  # najnowszy pierwszy


def test_summary_text_pusty_stan_jest_INFORMACJA_a_nie_brakiem_wiadomosci():
    """Cisza znaczy „usługa nie żyje"; „zero spraw" musi wyglądać inaczej niż brak raportu."""
    text = build_summary_text(tygodnie=[], nastepny_przebieg="2026-07-24 16:00")
    assert "nikogo nie trzeba było zagadnąć" in text


def test_przypomnienie_mowi_o_terminie_i_o_najkrotszej_drodze():
    """B7: obietnicę terminu i jego egzekwowanie liczy ten sam kod. N13: tekst jest stałą."""
    termin = datetime(2026, 7, 20, 5, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
    tekst = build_przypomnienie_text(
        "20.07–24.07", termin, ZoneInfo("Europe/Warsaw"), ma_propozycje=True
    )
    assert "nie mam jeszcze Twojej odpowiedzi" in tekst
    assert "20.07–24.07" in tekst
    assert "odpisać „ok”" in tekst
    assert "poniedziałku 20.07, godz. 5:00" in tekst


def test_przypomnienie_bez_gotowca_nie_obiecuje_ze_jest_co_potwierdzic():
    """Przy pustym gotowcu zdanie „odpisz »ok«, żeby powtórzyć" byłoby nieprawdziwe —
    nie ma czego powtórzyć. Ten sam podział, który robi `build_nudge_text`."""
    tekst = build_przypomnienie_text(
        "20.07–24.07", None, ZoneInfo("Europe/Warsaw"), ma_propozycje=False
    )
    assert "„ok”" not in tekst
    assert "kiedy pracujesz" in tekst
    assert "Czekam do" not in tekst, "bez wyznaczalnego terminu nie obiecujemy godziny"


def test_podsumowanie_pokazuje_skutecznosc_przypomnien_i_odsetek():
    """Dwie dźwignie, których działania dotąd NIE BYŁO WIDAĆ: sobotnie przypomnienie (D5)
    i jakość interpretacji (E4). Bez nich ocena wymagała czytania logu — a A12 zakłada,
    że logów nikt nie czyta."""
    t = LiczbyTygodnia(
        week_start="2026-07-20",
        zapisane=2,
        wygasle=1,
        przypomnienia=3,
        przypomnienia_skuteczne=2,
        interpretacje=9,
        niejasnosci=2,
    )
    tresc = build_summary_text(tygodnie=[t], nastepny_przebieg="2026-07-24 16:00")
    assert "przypomnienia (sobota): 3, z tego z uzupełnionym grafikiem: 2" in tresc
    assert "zinterpretowane: 9, w tym niejasne: 2 (22%)" in tresc


def test_podsumowanie_milczy_o_miarach_ktorych_nie_ma():
    """Tydzień bez przypomnień i bez interpretacji nie dostaje wierszy z zerami.

    „przypomnienia: 0" i „niejasne: 0 (0%)" czytają się jak zmierzone zero, a znaczą »nie było
    czego mierzyć« — a raport z wierszami bez treści uczy administratora go przewijać."""
    t = LiczbyTygodnia(week_start="2026-07-20", zapisane=1)
    tresc = build_summary_text(tygodnie=[t], nastepny_przebieg="2026-07-24 16:00")
    assert "przypomnienia" not in tresc
    assert "zinterpretowane" not in tresc
