"""Wygląd wiadomości (0.2.26): KAŻDA wiadomość idzie do Teams jako HTML z tabelą.

Strażnik iteruje po builderach, a nie po pojedynczych przypadkach: nowa wiadomość dopisana bez
tabeli ma paść tutaj, a nie wyjść na czacie jako ściana tekstu. Wyjątki są wymienione z nazwy
i z powodem.
"""

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from powiadomienia_teams import messages as m
from powiadomienia_teams.domain.models import Member, Shift, WeekSchedule

UTC = timezone.utc
WAW = ZoneInfo("Europe/Warsaw")
TYDZIEN = date(2026, 10, 5)
GRAFIK = WeekSchedule(
    "u1",
    TYDZIEN,
    tuple(
        Shift(
            "u1",
            datetime(2026, 10, 5 + d, 6, tzinfo=UTC),
            datetime(2026, 10, 5 + d, 14, tzinfo=UTC),
            theme="blue" if d == 2 else "green",
        )
        for d in range(5)
    ),
)
TERMIN = datetime(2026, 10, 5, 3, tzinfo=UTC)
ETYKIETA = "05.10–11.10"

WSZYSTKIE = {
    "prośba": lambda: m.build_nudge_text(
        Member("u1", "Ala Nowak"), GRAFIK, ETYKIETA, WAW, (), TERMIN, "typowy grafik"
    ),
    "prośba bez propozycji z urlopem": lambda: m.build_nudge_text(
        Member("u1", "Ala Nowak"), WeekSchedule("u1", TYDZIEN), ETYKIETA, WAW, (4,), TERMIN
    ),
    "przypomnienie": lambda: m.build_przypomnienie_text(ETYKIETA, TERMIN, WAW, ma_propozycje=True),
    "potwierdzenie": lambda: m.build_confirm_text(
        GRAFIK, [{"weekday": 5, "reason_name": "Urlop"}], WAW, [{"weekday": 6, "powod": "x"}]
    ),
    "zapisano": lambda: m.build_applied_text(zapisane=GRAFIK, tz=WAW),
    "zapisano częściowo": lambda: m.build_applied_text(minione=1, zapisane=GRAFIK, tz=WAW),
    "odmowa": lambda: m.build_declined_text(ETYKIETA),
    "brak odpowiedzi": lambda: m.build_expired_text(ETYKIETA),
    "brak potwierdzenia": lambda: m.build_no_confirm_text(ETYKIETA),
    "tydzień już trwa": lambda: m.build_stale_week_text(ETYKIETA),
    "tydzień zamknięty": lambda: m.build_tydzien_zamkniety_text(ETYKIETA),
    "samouzupełnienie": lambda: m.build_self_filled_text(ETYKIETA),
    "nic do zapisania": lambda: m.build_nic_do_zapisania_text([4], ETYKIETA),
    "błąd zapisu": lambda: m.build_write_failed_text(ETYKIETA),
    "niejasne": lambda: m.build_unclear_text(),
    "podsumowanie": lambda: m.build_summary_text(
        tygodnie=[m.LiczbyTygodnia("2026-10-05", oczekuje=2, zapisane=3)], nastepny_przebieg="pt"
    ),
    "podsumowanie puste": lambda: m.build_summary_text(tygodnie=[], nastepny_przebieg="pt"),
}
# Wiadomości BEZ tabeli — celowo, bo nie mają ani danych, ani czego poprawiać:
# pytanie o grafik odsyła do zakładki »Zmiany«, a „inny tydzień"/„brak powodu" kierują do
# przełożonego. Tabela przykładów byłaby tam radą, której nie da się zastosować.
BEZ_TABELI = {"podsumowanie puste"}


@pytest.mark.parametrize("nazwa", sorted(WSZYSTKIE))
def test_kazda_wiadomosc_to_HTML_z_tabela(nazwa: str):
    tresc = WSZYSTKIE[nazwa]()
    assert isinstance(tresc, m.Tresc), nazwa
    html = m.to_html(tresc)
    assert html == tresc.html
    assert html.startswith("<p>"), nazwa
    if nazwa not in BEZ_TABELI:
        assert "<table>" in html and "</table>" in html, nazwa
    assert "\n" not in html  # Teams zwija znaki nowej linii — układ niosą znaczniki


def test_potwierdzenie_podaje_TYDZIEN_i_DATY_kazdego_dnia():
    """Symulacja 0.2.25: odpowiedź o mijającym tygodniu szła na następny bez żadnej wskazówki."""
    tresc = m.build_confirm_text(GRAFIK, [], WAW)
    assert "tydzień 05.10–11.10" in tresc
    for dzien in ("05.10", "06.10", "07.10", "08.10", "09.10"):
        assert f"<td>{dzien}</td>" in tresc.html
    assert "🔵 zdalnie" in tresc.html and "🟢 stacjonarnie" in tresc.html


def test_prosba_pokazuje_propozycje_z_opisem_podstawy_i_dni_wolne_w_tabeli():
    tresc = m.build_nudge_text(
        Member("u1", "Ala Nowak"),
        WeekSchedule("u1", TYDZIEN, GRAFIK.shifts[:4]),
        ETYKIETA,
        WAW,
        (4,),
        TERMIN,
        "typowy grafik policzony z 4 ostatnich tygodni",
    )
    assert "typowy grafik policzony z 4 ostatnich tygodni" in tresc.html
    assert "<td>Piątek</td><td>09.10</td><td>🏖️ wolne (już w grafiku)</td>" in tresc.html
    # Kolejność dni w tabeli: poniedziałek pierwszy, piątek (wolny) ostatni.
    assert tresc.html.index("Poniedziałek") < tresc.html.index("Piątek")


def test_tresc_dynamiczna_jest_escapowana():
    tresc = m.build_nudge_text(
        Member("u1", "<script>alert(1)</script> X"), GRAFIK, ETYKIETA, WAW, (), TERMIN
    )
    assert "<script>" not in tresc.html
    potw = m.build_confirm_text(GRAFIK, [{"weekday": 5, "reason_name": "<b>Urlop</b>"}], WAW)
    assert "<b>Urlop</b>" not in potw.html


def test_zwykly_tekst_nadal_renderuje_sie_bezpiecznie():
    assert m.to_html("a < b\nc") == "a &lt; b<br>c"


def test_tabela_grafiku_przy_przelomie_roku_idzie_od_poniedzialku():
    tydzien = date(2026, 12, 28)
    grafik = WeekSchedule(
        "u1",
        tydzien,
        tuple(
            Shift(
                "u1", datetime(2026, 12, 28, 7, tzinfo=UTC), datetime(2026, 12, 28, 15, tzinfo=UTC)
            )
            for _ in range(1)
        )
        + (Shift("u1", datetime(2027, 1, 1, 7, tzinfo=UTC), datetime(2027, 1, 1, 15, tzinfo=UTC)),),
    )
    html = m.build_confirm_text(grafik, [{"weekday": 2, "reason_name": "Urlop"}], WAW).html
    assert html.index("Poniedziałek") < html.index("Środa") < html.index("Piątek")


def test_potwierdzenie_oznacza_dni_ktore_juz_minely():
    """Środa 13:00: poniedziałek i wtorek już minęły — zapis je pominie, tabela mówi to z góry."""
    sroda = datetime(2026, 10, 7, 11, tzinfo=UTC)
    html = m.build_confirm_text(GRAFIK, [], WAW, teraz=sroda).html
    assert html.count("już minął") == 2
    assert "<td>07.10</td><td>08:00–16:00</td>" in html  # środa trwa — zostanie zapisana
