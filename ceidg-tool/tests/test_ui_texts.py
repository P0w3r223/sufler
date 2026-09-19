"""Modele widoku z `ui/texts` — treść ekranów wymagana przez uzupelnienie-01.md §A.

`texts` jest czysty, więc każdy ekran da się sprawdzić bez terminala, bez bazy i bez sieci.
Testy pilnują tego, co obiecano użytkownikowi: pierwszy ekran mówi dokąd lecą dane i jakim
tokenem, tabela kosztów liczy z `Estimate` (a nie z prozy), a zdanie o `link_ceidg` zależy
od źródła danych, nie od stanu bazy.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ceidg_tool.apiprofile import ApiProfile
from ceidg_tool.batching import plan_batches
from ceidg_tool.config import TOKEN_SERVICE_URL, Environment, Settings
from ceidg_tool.criteria import Criteria
from ceidg_tool.estimating import estimate
from ceidg_tool.normalizer import NormalizedRecord, normalize
from ceidg_tool.records import RawRecord, Report, RowContext
from ceidg_tool.ui import texts
from tests.conftest import detail_record, list_record
from tests.support import criteria

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"
PROD_BASE = "https://dane.biznes.gov.pl/api/ceidg/v3"
VERSION = "0.1.0"


def settings_for(environment: Environment = "test", *, data_dir: Path = Path("/dane")) -> Settings:
    return Settings(token="token-testowy", environment=environment, data_dir=data_dir)


def fields_of(block: texts.Block) -> dict[str, str]:
    """Blok klucz-wartość jako słownik. `Block.rows` to krotki zmiennej długości,
    więc samo `dict()` nie wystarcza — a testom potrzebny jest dostęp po nazwie pola."""
    return {row[0]: row[1] for row in block.rows}


def profile() -> ApiProfile:
    return ApiProfile(base_url=BASE, max_limit_firmy=25, ids_batch_size=5)


# ----------------------------------------------------------------------------- pierwszy ekran


def test_first_screen_answers_the_four_questions_from_section_a() -> None:
    """§A: co robi, dokąd wysyła dane, w jakim środowisku pracuje, jaki token, gdzie pliki."""
    block = texts.first_screen(settings_for(), now=NOW, version=VERSION)

    fields = fields_of(block)
    assert fields["co robi"] == texts.PROGRAM_PURPOSE
    # Wiersz rozdzielił się 2026-09-07: rekordy idą wyłącznie do CEIDG, a do modelu — treść
    # pytania i słownik PKD. Jeden wiersz o „wysyłaniu danych" przestawał być prawdą w chwili
    # użycia asystenta, a jest to kryterium odbioru §A.
    assert "test-dane.biznes.gov.pl" in fields["dokąd wysyłam rekordy"]
    assert "brak telemetrii" in fields["dokąd wysyłam rekordy"]
    assert "asystent wyłączony" in fields["dokąd wysyła asystent"]
    assert fields["środowisko"] == "TEST"
    assert "brak daty wygaśnięcia w tokenie" in fields["token"]
    assert "źródło: env" in fields["token"]
    assert fields["dane i wyniki"] == str(Path("/dane"))
    assert VERSION in block.title


def test_first_screen_on_production_names_the_personal_data_risk() -> None:
    """Operator musi widzieć bez czytania dokumentacji, że pracuje na danych osobowych."""
    block = texts.first_screen(settings_for("prod"), now=NOW, version=VERSION)

    fields = fields_of(block)
    assert fields["środowisko"] == "PRODUKCJA (prawdziwe dane osobowe)"
    assert "dane.biznes.gov.pl" in fields["dokąd wysyłam rekordy"]
    # Na produkcji nie namawiamy już na produkcję.
    assert texts.PROD_HINT not in block.notes


def test_first_screen_on_test_points_at_the_production_command_and_token_service() -> None:
    block = texts.first_screen(settings_for("test"), now=NOW, version=VERSION)

    assert texts.PROD_HINT in block.notes
    assert any(TOKEN_SERVICE_URL in note for note in block.notes)


def test_first_screen_never_shows_the_token_value() -> None:
    """§B: na ekranie jest ważność i źródło tokenu, nigdy sam sekret."""
    settings = Settings(token="sekretny-token-123", environment="test", data_dir=Path("/dane"))

    block = texts.first_screen(settings, now=NOW, version=VERSION)

    assert "sekretny-token-123" not in block.as_text()


# ----------------------------------------------------------------------------- tabela kosztów


def test_cost_table_has_the_section_a_columns_and_derives_numbers_from_the_estimate() -> None:
    """Liczby w tabeli muszą pochodzić z `Estimate`, nie z osobnego rachunku w tekście."""
    est = estimate(1_240, profile())

    block = texts.cost_table(est, threshold=50_000)

    assert block.headers == ("zakres danych", "liczba zapytań", "szacowany czas", "zawartość")
    rows = {row[0]: row for row in block.rows}
    assert rows["lista podstawowa"][1] == str(est.requests_list) == "50"
    assert rows["z pełnymi szczegółami"][1] == str(est.requests_list + est.requests_details)
    assert "1 240" in block.title
    assert not block.notes  # poniżej progu nie ma ostrzeżenia


def test_cost_table_shows_the_details_row_as_the_more_expensive_option() -> None:
    """Wybór „lista vs szczegóły” ma sens tylko wtedy, gdy widać różnicę w koszcie."""
    est = estimate(1_240, profile())

    rows = {row[0]: row for row in texts.cost_table(est, threshold=50_000).rows}

    assert int(rows["z pełnymi szczegółami"][1]) > int(rows["lista podstawowa"][1])
    assert "PKD" in rows["z pełnymi szczegółami"][3]
    assert "NIP" in rows["lista podstawowa"][3]


def test_cost_table_above_the_threshold_states_that_nothing_starts_by_itself() -> None:
    """§C: powyżej progu tabela musi wprost mówić, że program sam nie ruszy."""
    est = estimate(400_000, profile())

    block = texts.cost_table(est, threshold=50_000)

    note = " ".join(block.notes)
    assert "nie zacznie" in note
    assert "50 000" in note
    assert "Zawęź kryteria albo podziel je na partie" in note


def test_the_report_offer_states_its_cost_and_its_gaps() -> None:
    """Raport pada przed zapytaniem o `count`, więc ma własny blok — ale wybór ma sens
    tylko wtedy, gdy widać i cenę (2 zapytania), i czego w raporcie brakuje."""
    report = Report("r1", "Zarejestrowane działalności", ".csv", f"{BASE}/raport/r1", "2026-09-04")

    block = texts.report_offer(report)
    values = {row[0]: row[1] for row in block.rows}

    assert values["nazwa"] == "Zarejestrowane działalności"
    assert values["utworzony"] == "2026-09-04"
    assert "2 zapytania" in values["koszt"]
    assert "WYKREŚLONYCH" in values["czego brakuje"]


# ------------------------------------------------------- rocznik PKD 2007 (ADR-0012)

FRYZJERSTWO = (
    "9602Z",
    "Fryzjerstwo i pozostałe zabiegi kosmetyczne",
    (("9622Z", "Działalność w zakresie pielęgnacji urody i pozostała działalność kosmetyczna"),),
    "",  # `9602Z` nie istnieje w PKD 2025, więc nie ma dzisiejszego znaczenia
)

# Druga postać niejednoznaczności: kod, który w PKD 2025 **nadal istnieje**, ale znaczy co
# innego. Dokładając go do zapytania o kluby fitness, bierzemy dzisiejszą edukację sportową.
FITNESS = (
    "8551Z",
    "Pozaszkolne formy edukacji sportowej oraz zajęć sportowych i rekreacyjnych",
    (),
    "Pozostałe formy edukacji sportowej oraz zajęć sportowych i rekreacyjnych",
)


def test_the_vintage_offer_names_the_industry_that_comes_along() -> None:
    """Operator ma zobaczyć **nazwę** cudzej branży, a nie usłyszeć, że „wynik może być szerszy".

    Sam kod `9622Z` nic mu nie mówi; nazwa jest jedyną kontrolą nad tym, czy zgadza się
    na kosmetyczki w zestawieniu salonów fryzjerskich.
    """
    block = texts.vintage_offer([FRYZJERSTWO], waskie=2_266, szerokie=9_077)

    tekst = block.as_text()
    assert "9602Z" in tekst
    assert "Fryzjerstwo i pozostałe zabiegi kosmetyczne" in tekst
    assert "9622Z" in tekst and "pielęgnacji urody" in tekst
    assert "obejmuje też" in tekst


def test_the_vintage_offer_prices_both_populations_and_names_the_difference() -> None:
    """Różnica jest liczbą, o którą toczy się wybór; sam iloraz brzmi abstrakcyjnie."""
    block = texts.vintage_offer([FRYZJERSTWO], waskie=2_266, szerokie=9_077)

    notes = "\n".join(block.notes)
    assert "2 266" in notes  # polski separator tysięcy, jak w tabeli kosztów
    assert "9 077" in notes
    assert "6 811" in notes  # 9 077 − 2 266
    assert "31.12.2026" in notes  # kiedy problem znika sam


def test_the_vintage_offer_without_numbers_says_they_are_unknown_rather_than_inventing_them() -> (
    None
):
    """Ścieżka raportowa filtruje lokalnie, więc liczb tu jeszcze nie ma.

    Cała wartość tego ekranu polega na tym, że jego liczby są **mierzone** — wstawienie
    oszacowania z jednego województwa byłoby dokładnie tym błędem, przed którym broni
    `scripts/build_pkd_transition.py`.
    """
    block = texts.vintage_offer([FRYZJERSTWO])

    notes = "\n".join(block.notes)
    assert "wiadomo dopiero po pobraniu raportu" in notes
    assert "nie kosztuje ani jednego dodatkowego zapytania" in notes
    assert "Tylko PKD 2025:" not in notes


def test_the_applied_expansion_says_it_was_added_without_asking_and_why() -> None:
    """Rozszerzenie czyste jest zastosowane bez pytania — więc musi być przynajmniej widoczne."""
    block = texts.vintage_applied([("1412Z", "Produkcja odzieży roboczej", (), "")])

    tekst = block.as_text()
    assert "1412Z" in tekst and "Produkcja odzieży roboczej" in tekst
    assert "bez pytania" in tekst
    assert "PKD 2025" in tekst
    # Bez cudzej branży zdanie nie może straszyć poszerzeniem, którego nie ma.
    assert "obejmuje też" not in tekst


def test_an_expansion_applied_by_flag_still_names_the_industries_it_drags_in() -> None:
    """`--pkd-2007` jest zgodą na dołożenie kodów, nie na nieoglądanie tego, co dokładają.

    Do 2026-09-07 ta gałąź milczała: `9602Z` leciał w zapytaniu i nie padał na żadnym ekranie,
    mimo że komentarz nad `vintage_applied` nazywał ciche poszerzenie defektem. Tester zmierzył
    to końcem do końca na prawdziwym URL-u, więc test trzyma **treść**, nie sam fakt bloku.
    """
    block = texts.vintage_applied(
        [("9602Z", "Fryzjerstwo i pozostałe zabiegi kosmetyczne", (("9622Z", "Uroda"),), "")]
    )

    tekst = block.as_text()
    assert "9602Z" in tekst
    assert "9622Z" in tekst and "Uroda" in tekst
    assert "obejmuje też" in tekst
    # Przy poszerzeniu nie wolno twierdzić, że kody znaczą „dokładnie to samo".
    assert "dokładnie to samo" not in tekst


def test_a_code_that_still_exists_today_says_what_it_means_now() -> None:
    """Druga postać niejednoznaczności czyta się inaczej i musi mieć własne zdanie.

    Znalezisko przeglądu 2026-09-07: generator odrzucał poprzedników, którzy sami są żywymi
    kodami PKD 2025, z uzasadnieniem „filtr 2025 już go obejmuje". Filtr obejmuje wtedy
    **rekord**, a nie **branżę, o którą pyta operator** — przez to 93 kody, w tym kluby fitness
    i piekarnie, nie dostawały żadnego poprzednika. Teraz dostają, a ekran mówi wprost, że
    dokładany kod znaczy dziś co innego. Zlanie tego z „prowadzi też do" pokazywało ten sam
    kod dwa razy w jednym wierszu, co czytało się jak usterka.
    """
    block = texts.vintage_offer([FITNESS], waskie=1_000, szerokie=4_000)

    tekst = block.as_text()
    assert "8551Z" in tekst
    assert "istnieje też dziś" in tekst
    assert "Pozostałe formy edukacji sportowej" in tekst
    # To nie jest ta sama sytuacja co „prowadzi też do innego kodu" i nie może się tak czytać.
    assert "obejmuje też" not in tekst


def test_a_code_whose_meaning_did_not_change_does_not_repeat_its_own_name() -> None:
    """Gdy nazwa jest ta sama w obu rocznikach, zdanie o „dzisiejszym znaczeniu" jest szumem.

    Znalezione dopiero po **wyrenderowaniu** ekranu przez `rich` (2026-09-07): przy `1086Z`
    nazwa z 2007 i z 2025 jest identyczna, więc ekran mówił dwa razy to samo zdanie i czytało
    się to jak usterka programu. Model widoku wyglądał przy tym poprawnie i żaden test
    asertujący `Block` tego nie widział — dlatego ten test patrzy na **tekst**, nie na pola.
    """
    ta_sama = (
        "1086Z",
        "Produkcja artykułów spożywczych homogenizowanych",
        (),
        "Produkcja artykułów spożywczych homogenizowanych",
    )

    tekst = texts.vintage_offer([ta_sama]).as_text()

    assert tekst.count("Produkcja artykułów spożywczych homogenizowanych") == 1
    assert "nadal w użyciu" in tekst
    assert "znaczy:" not in tekst


def test_the_skipped_expansion_names_the_codes_and_the_flag_that_turns_it_on() -> None:
    """Ostrzeżenie bez sposobu działania jest teatrem; ma podać kody i flagę."""
    zdanie = texts.vintage_skipped(("9602Z", "1412Z"))

    assert "9602Z" in zdanie and "1412Z" in zdanie
    assert "--pkd-2007" in zdanie
    assert "PKD 2025" in zdanie


def test_the_cost_table_does_not_scold_when_a_record_limit_is_set() -> None:
    """Powyżej progu, ale z `--maks`, program pobiera — więc nie może twierdzić, że nie zacznie."""
    est = estimate(400_000, profile(), max_rekordow=100)

    capped = texts.cost_table(est, threshold=50_000, capped=True)
    uncapped = texts.cost_table(est, threshold=50_000)

    assert not any("nie zacznie" in note for note in capped.notes)
    assert "limit rekordów" in " ".join(capped.notes)
    assert any("nie zacznie" in note for note in uncapped.notes)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 s"),
        (89, "89 s"),
        (90, "2 min"),
        (5_340, "89 min"),
        (5_400, "1.5 h"),
        (60_000, "16.7 h"),
    ],
    ids=["zero", "sekundy", "granica_sekund", "minuty", "granica_minut", "scenariusz_9"],
)
def test_format_duration_switches_unit_at_ninety(seconds: float, expected: str) -> None:
    """Próg 90 jest wyłączny w obie strony: 89 min zostaje minutami, 90 min to już godziny."""
    assert texts.format_duration(seconds) == expected


def test_format_number_uses_a_space_as_the_thousands_separator() -> None:
    """Polski zapis niezależny od ustawień lokalnych maszyny — inaczej CI i laptop się różnią."""
    assert texts.format_number(400_000) == "400 000"
    assert texts.format_number(999) == "999"


def test_estimate_text_and_cost_table_report_the_same_request_counts() -> None:
    """Tryb cichy i tryb interaktywny nie mogą podawać innych liczb dla tych samych kryteriów."""
    est = estimate(1_240, profile())

    text = texts.estimate_text(est)
    rows = {row[0]: row for row in texts.cost_table(est, threshold=50_000).rows}

    assert f"~{rows['lista podstawowa'][1]} zapytań" in text
    assert f"~{rows['z pełnymi szczegółami'][1]} zapytań" in text


# ----------------------------------------------------------------------------- podział


def test_split_table_has_one_row_per_batch_with_its_own_time_estimate() -> None:
    """§C wymaga „podziału na partie z szacunkiem czasu dla każdej” — wiersz na partię."""
    plan = plan_batches(
        criteria(wojewodztwo="mazowieckie"),
        400_000,
        today=datetime(2026, 9, 5, tzinfo=UTC).date(),
        threshold=50_000,
    )
    estimates = [estimate(plan.share, profile()) for _ in plan.batches]

    block = texts.split_table(plan, estimates)

    assert block.headers == ("partia", "zakres dat", "szacowane trafienia", "zapytania", "czas")
    assert len(block.rows) == len(plan.batches)
    assert all(row[4] for row in block.rows)  # każda partia ma czas
    assert "latami" in block.title and f"{len(plan.batches)} partii" in block.title


def test_split_table_says_the_numbers_are_estimates_and_the_work_is_resumable() -> None:
    plan = plan_batches(
        criteria(wojewodztwo="mazowieckie", data_od="2020-01-01", data_do="2029-12-31"),
        500_000,
        today=datetime(2026, 9, 5, tzinfo=UTC).date(),
        threshold=50_000,
    )
    estimates = [estimate(plan.share, profile()) for _ in plan.batches]

    notes = " ".join(texts.split_table(plan, estimates).notes)

    assert "szacunkiem" in notes
    assert "własny checkpoint" in notes and "wznowić" in notes


def test_split_table_admits_when_even_monthly_batches_are_too_big() -> None:
    """Bez tego zdania operator dostałby 120 partii i żadnej wskazówki, co dalej."""
    plan = plan_batches(
        criteria(wojewodztwo="mazowieckie", data_od="2020-01-01", data_do="2029-12-31"),
        6_000_001,
        today=datetime(2026, 9, 5, tzinfo=UTC).date(),
        threshold=50_000,
    )
    estimates = [estimate(plan.share, profile()) for _ in plan.batches]

    notes = " ".join(texts.split_table(plan, estimates).notes)

    assert plan.exhausted
    assert "zawężenie kryteriów" in notes


# ----------------------------------------------------------------------------- podsumowanie


def summary_input(
    tmp_path: Path, *, kind: str = "firmy", records: int = 4, bez_kontaktow: bool = False
) -> texts.SummaryInput:
    path = tmp_path / "wynik.xlsx"
    path.write_bytes(b"x" * 2048)
    return texts.SummaryInput(
        paths=(path,),
        records=records,
        by_status={"AKTYWNY": 3, "WYKRESLONY": 1},
        with_phone=1,
        with_email=2,
        # Domyślnie „kontakty mogły się tu znaleźć", bo odsetek ma wtedy sens. Przypadek
        # przeciwny — tryb listy, w którym nikt telefonów nie pobierał — ma własny test.
        bez_kontaktow=bez_kontaktow,
        sheets=("Firmy", "PKD", "Slownik", "Metadane"),
        kind=kind,
        run_ids=("run-1",),
        log_path=tmp_path / "logi" / "ceidg-tool.log",
    )


def test_summary_reports_file_size_status_counts_contact_share_sheets_and_log(
    tmp_path: Path,
) -> None:
    """§A: plik i rozmiar, firmy wg statusu, odsetek kontaktów, arkusze, ścieżka logu."""
    block = texts.summary_table(summary_input(tmp_path))

    fields = fields_of(block)
    assert "wynik.xlsx" in fields["plik"] and "2 KB" in fields["plik"]
    assert fields["firm"] == "4"
    assert fields["  AKTYWNY"] == "3" and fields["  WYKRESLONY"] == "1"
    assert fields["z telefonem"] == "1 (25%)"
    assert fields["z e-mailem"] == "2 (50%)"
    assert fields["arkusze"] == "Firmy, PKD, Slownik, Metadane"
    assert "ceidg-tool.log" in fields["log"]
    assert fields["run_id"] == "run-1"


def test_summary_of_an_empty_result_does_not_divide_by_zero(tmp_path: Path) -> None:
    """Zero rekordów to normalny wynik, nie awaria — odsetki po prostu znikają."""
    block = texts.summary_table(summary_input(tmp_path, records=0))

    fields = fields_of(block)
    assert fields["firm"] == "0"
    assert "z telefonem" not in fields


def test_summary_note_about_the_link_column_depends_on_the_data_source(tmp_path: Path) -> None:
    """ADR-0008, decyzja 5: zdanie wybiera `ExportSummary.kind`, nie zapytanie do bazy."""
    from_api = texts.summary_notes(summary_input(tmp_path, kind="firmy"))
    from_report = texts.summary_notes(summary_input(tmp_path, kind="raport"))

    assert "prowadzi do wpisu" in from_api[0]
    assert "pusta" in from_report[0] and "NIP w wyszukiwarce CEIDG" in from_report[0]
    assert from_api != from_report


def test_summary_lists_every_batch_run_id(tmp_path: Path) -> None:
    """Pobranie w partiach ma kilka runów — wszystkie muszą być w podsumowaniu do wznowienia."""
    base = summary_input(tmp_path)
    many = texts.SummaryInput(**{**base.__dict__, "run_ids": ("run-1", "run-2", "run-3")})

    fields = fields_of(texts.summary_table(many))

    assert fields["run_id"] == "run-1, run-2, run-3"


def test_summary_extra_notes_are_appended_as_rows(tmp_path: Path) -> None:
    """Różnica między sumą partii a pierwotnym `count` ma być widoczna, nie ukryta."""
    note = ("uwaga", "Partie objęły 399 000 z 400 000 trafień")
    base = summary_input(tmp_path)
    with_note = texts.SummaryInput(**{**base.__dict__, "extra": (note,)})

    assert note in texts.summary_table(with_note).rows


def test_missing_output_file_is_reported_instead_of_raising(tmp_path: Path) -> None:
    """Podsumowanie nie może wywrócić się na pliku, którego ktoś w międzyczasie nie zapisał."""
    assert texts.format_size(tmp_path / "nie-ma-mnie.xlsx") == "brak pliku"


# ----------------------------------------------------------------------------- karta firmy


def normalized(**overrides: object) -> NormalizedRecord:
    raw = RawRecord(
        id=list_record(1)["id"],
        list_json=list_record(1, **overrides),
        detail_json=detail_record(1, **overrides),
        list_utc="2026-09-05T10:00:00Z",
        detail_utc="2026-09-05T10:00:00Z",
        detail_state="pobrany",
        zrodlo="CEIDG_API",
    )
    return normalize(raw, RowContext(srodowisko="test", pobrano_utc="2026-09-05T10:00:00Z"))


def test_firm_card_shows_the_identity_and_contact_fields() -> None:
    record = normalized()

    block = texts.firm_card(record, nip="3563457932")

    fields = fields_of(block)
    assert block.title == "NIP 3563457932"
    assert fields["NIP"] == "3563457932"
    assert fields["województwo"]
    assert "wpis w CEIDG" in fields  # link z GUID-a jest wypełniony dla rekordu z API


def test_firm_card_omits_fields_the_record_does_not_have() -> None:
    """Pusta komórka w karcie byłaby szumem — brakujące pola po prostu nie mają wiersza."""
    record = normalized(email=None)

    fields = fields_of(texts.firm_card(record, nip="3563457932"))

    assert all(value not in ("None", "") for value in fields.values())


def test_firm_card_for_a_missing_company_explains_it_is_not_a_typo() -> None:
    """Brak trafienia to normalny wynik: suma kontrolna przeszła, więc wpisu po prostu nie ma."""
    block = texts.firm_card(None, nip="3563457932")

    assert fields_of(block)["wynik"] == "brak wpisu o tym numerze NIP w rejestrze CEIDG"
    note = " ".join(block.notes)
    assert "sumy kontrolnej" in note and "nie literówka" in note
    assert "KRS" in note  # podpowiedź, gdzie szukać spółek


# ----------------------------------------------------------------------------- bloki pomocnicze


def test_menu_block_numbers_items_from_one() -> None:
    items = [texts.MenuItem("a", "Pierwsze"), texts.MenuItem("b", "Drugie", "podpowiedź")]

    block = texts.menu_block(items)

    assert block.rows == (("1", "Pierwsze", ""), ("2", "Drugie", "podpowiedź"))


def test_hints_block_lists_the_closed_value_sets_from_criteria() -> None:
    """Kreator pyta o województwo i status — dozwolone wartości muszą pochodzić z `criteria`."""
    from ceidg_tool.criteria import STATUSY, WOJEWODZTWA

    fields = fields_of(texts.hints_block())

    assert all(w in fields["województwo"] for w in WOJEWODZTWA)
    assert all(s in fields["status"] for s in STATUSY)


def test_block_as_text_renders_title_headers_rows_and_notes() -> None:
    """`as_text` jest kontraktem dla logu i asercji — musi zawierać wszystkie części bloku."""
    block = texts.Block(title="Tytuł", headers=("a", "b"), rows=(("1", "2"),), notes=("uwaga",))

    assert block.as_text() == "Tytuł\na | b\n1 | 2\nuwaga"


# ---------------------------------------------- polecenie powtarzające (ADR-0022)


@pytest.mark.parametrize(
    ("wartosc", "oczekiwane"),
    [
        ("Poznań", "--nazwisko Poznań"),  # bez znaków specjalnych: bez cudzysłowu
        ("Stara Łomża przy Szosie", "--nazwisko 'Stara Łomża przy Szosie'"),
        ("O'Brien", '--nazwisko "O\'Brien"'),  # apostrof w środku wymusza podwójny cytat
        ("Kowalski & Syn", "--nazwisko 'Kowalski & Syn'"),
        ("firma $HOME", "--nazwisko 'firma $HOME'"),  # apostrof nie rozwija zmiennej
    ],
)
def test_polecenie_cytuje_wartosc_tak_by_przetrwala_powloke(wartosc: str, oczekiwane: str) -> None:
    """Jeden zapis ma działać i w bashu, i w PowerShellu — to dwie powłoki tego projektu.

    Apostrof jest domyślny, bo żadna z nich nie rozwija w nim `$` ani backticka. Wartość
    z apostrofem w środku (`O'Brien` — nazwisko, nie przypadek egzotyczny) dostaje cudzysłów
    podwójny, bo apostrofu w apostrofach obie powłoki składają inaczej (`'\''` kontra `''`).
    """
    polecenie = texts.polecenie_powtarzajace(criteria(nazwisko=wartosc))

    assert polecenie is not None and oczekiwane in polecenie


@pytest.mark.parametrize(
    "wartosc",
    [
        "apostrof ' i $zmienna",  # apostrof wymusza podwójny cytat, a ten rozwinąłby `$`
        'apostrof \' i "cytat"',  # obie klamry naraz — nie ma czym objąć
        "z\ttabem",  # znak sterujący nie przechodzi żadnym cytowaniem
    ],
)
def test_polecenie_odmawia_gdy_cytowanie_zmieniloby_znaczenie(wartosc: str) -> None:
    """Lepiej powiedzieć „nie da się", niż wypisać linię, która po wklejeniu znaczy co innego.

    To jest ta sama zasada, dla której `--zrodlo raport` odmawia zamiast po cichu pobrać przez
    API: wynik niezgodny z tym, co operator przeczytał na ekranie, jest gorszy od odmowy.

    Czego tu **nie** ma i dlaczego: sam cudzysłów w wartości (`nazwa "tak"`) odmowy nie
    wywołuje, bo apostrof obejmuje go dosłownie w obu powłokach. Odmowa jest dla przypadków,
    w których żadna z dwóch klamer nie wystarcza.
    """
    assert texts.polecenie_powtarzajace(criteria(nazwisko=wartosc)) is None

    blok = texts.repeat_command_block(criteria(nazwisko=wartosc))
    assert blok.rows == ()
    assert texts.POLECENIE_NIE_DO_ZAPISANIA in blok.notes


def test_polecenie_niesie_wszystkie_rodzaje_pol() -> None:
    """Pole pominięte w budowaniu polecenia zawęża albo poszerza powtórzenie bez ostrzeżenia."""
    pelne = criteria(
        wojewodztwo="podlaskie",
        miasto="Łomża",
        ulica="Kwiatowa",
        budynek="12A",
        lokal="3",
        kod="18-400",
        nazwa="Fryzjer",
        imie="Anna",
        nazwisko="Kowalska",
        nip="3563457932",
        regon="618155359",
        nip_sc="5252248481",
        regon_sc="146988099",
        pkd="9621Z",
        status="AKTYWNY",
        data_od="2020-01-01",
        data_do="2020-12-31",
        szczegoly=True,
        max_rekordow=500,
    )

    polecenie = texts.polecenie_powtarzajace(pelne)

    assert polecenie is not None
    for oczekiwane in (
        "--wojewodztwo podlaskie",
        "--miasto Łomża",
        "--ulica Kwiatowa",
        "--budynek 12A",
        "--lokal 3",
        "--kod 18-400",
        "--nazwa Fryzjer",
        "--imie Anna",
        "--nazwisko Kowalska",
        "--nip 3563457932",
        "--regon 618155359",
        "--nip-sc 5252248481",
        "--regon-sc 146988099",
        "--pkd 9621Z",
        "--status AKTYWNY",
        "--od 2020-01-01",
        "--do 2020-12-31",
        "--szczegoly",
        "--maks 500",
        "--tak",
    ):
        assert oczekiwane in polecenie, f"polecenie gubi {oczekiwane}"


def test_spis_flag_pokrywa_kazde_pole_listowe_kryteriow() -> None:
    """`FLAGI_KRYTERIOW` ma znać każde pole listowe — pominięte znika z powtórzenia po cichu.

    Wiązanie z **nazwami flag** sprawdza `tests/test_cli.py`; tutaj chodzi o drugą stronę:
    czy mapa obejmuje wszystkie pola, które mogą nieść wartości.
    """
    # `pkd_2007` zostaje poza mapą świadomie: nie ma flagi listowej, tylko przełącznik
    # `--pkd-2007` (ADR-0012 przez ADR-0022). `status` ma własny typ `Literal`, więc nie
    # wpada w test po adnotacji — i to jest powód, dla którego dokładamy go tu jawnie,
    # zamiast pisać warunek, który akurat go łapie.
    listowe = {
        nazwa
        for nazwa, pole in Criteria.model_fields.items()
        if pole.annotation == tuple[str, ...] and nazwa != "pkd_2007"
    } | {"status"}

    assert set(texts.FLAGI_KRYTERIOW) == listowe
