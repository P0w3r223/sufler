"""Wspólne kroki decyzyjne (`ui/flow`) — jedno miejsce, w którym zapada kolejność.

ADR-0008: wznowienie → raport → zapytanie o `count` → tabela kosztów → wybór → ewentualny
podział → pobranie. Flagi, YAML, `--tak` i kreator różnią się tylko podstawionym
`Prompter`-em, więc te testy pilnują kontraktu dla wszystkich czterech naraz.

Niezmiennik liczby żądań brzmiał „dokładnie jedno `count`" i został **przeformułowany**, nie
osłabiony (ADR-0012, sub-decyzja 4): **najwyżej dwa, po jednym na populację, wyłącznie przed
zgodą, zero po wyborze.** Drugie pada tylko tam, gdzie okres przejściowy PKD daje operatorowi
wybór — i jest ceną za to, że pytanie niesie liczby zamiast zdania „wynik może być niepełny".
Zapytania bez takiego wyboru kosztują jedno, jak dotąd; sekcja „okres przejściowy PKD" niżej
pilnuje obu granic naraz.

Licznik żądań w `FakeApi` jest tu równie ważny jak zwracana decyzja: obietnica o liczbie
zapytań po podaniu kryteriów jest stwierdzeniem o żądaniach, nie o tekście.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

import httpx
import pytest

from ceidg_tool.config import Settings
from ceidg_tool.criteria import Criteria
from ceidg_tool.errors import ConfigError
from ceidg_tool.pipeline import Deps, build_deps
from ceidg_tool.pkdmap import load_pkd_map
from ceidg_tool.ui import flow
from ceidg_tool.ui.prompts import DefaultsPrompter, Question, ScriptedPrompter
from tests.conftest import FakeClock
from tests.support import FakeApi, RecordingView, criteria, pkd_map

BASE = "https://test-dane.biznes.gov.pl/api/ceidg/v3"
TODAY = date(2026, 9, 5)
THRESHOLD = 50_000

# Bez województwa raport nie pokrywa zapytania, więc ścieżka raportu nie miesza się do testów
# skupionych na `count`. Testy raportu podają województwo jawnie.
PLAIN = criteria(miasto="Białystok")


def counting_api(count: int) -> FakeApi:
    """API, które na `count` (limit=1) odpowiada zadaną liczbą i niczego więcej nie obsługuje."""
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        if "/firmy" in str(request.url) and request.url.params.get("limit") == "1":
            return httpx.Response(200, json={"count": count, "firmy": []})
        raise AssertionError(f"nieoczekiwane żądanie w fazie decyzji: {request.url}")

    api.fallback = fallback
    return api


def deps_for(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    settings = Settings(token="tok", environment="test", data_dir=tmp_path / "dane")
    return build_deps(settings, clock=clock, http=api.client())


def count_requests(api: FakeApi) -> list[str]:
    return [r for r in api.requests if "limit=1" in r]


# ----------------------------------------------------------------------------- jedno `count`


def test_exactly_one_count_request_precedes_the_cost_table(
    tmp_path: Path, clock: FakeClock
) -> None:
    """§A: zapytanie bez wyboru rocznika PKD kosztuje **jeden** `count` i tabelę kosztów.

    Górna granica dwóch żądań (ADR-0012) nie zwalnia z tej dolnej: kryteria bez filtru PKD
    nie mają dwóch populacji, więc drugie zapytanie byłoby wydatkiem na wiedzę, którą już
    mamy. Brak pytania o rocznik jest tu widoczny w `prompter.asked`.
    """
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    decision, plan = flow.prepare_fetch(PLAIN, deps, prompter, view, threshold=THRESHOLD)

    assert decision == "lista"
    assert plan.count == 1_240
    assert len(count_requests(api)) == 1
    assert prompter.asked == ["co_dalej"]
    assert "Znaleziono 1 240 firm" in view.block_titled("Znaleziono").title
    deps.store.close()


def test_choosing_details_flips_the_criteria_flag_without_a_second_count(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wybór „szczegóły” zmienia kryteria, ale nie unieważnia policzonego `count`."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)

    decision, plan = flow.prepare_fetch(
        PLAIN,
        deps,
        ScriptedPrompter({"co_dalej": "szczegoly"}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert decision == "szczegoly"
    assert plan.criteria.szczegoly is True
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_the_default_answer_follows_the_criteria_already_given(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`pobierz --szczegoly --tak` nie może cofnąć się do listy podstawowej."""
    api = counting_api(100)
    deps = deps_for(tmp_path, clock, api)

    decision, _ = flow.prepare_fetch(
        criteria(miasto="Białystok", szczegoly=True),
        deps,
        DefaultsPrompter(),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert decision == "szczegoly"
    deps.store.close()


def test_leaving_issues_no_fetch_request(tmp_path: Path, clock: FakeClock) -> None:
    """„Wyjdź” kończy się na jednym `count` — żadnej strony listy ani szczegółu."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)

    decision, _ = flow.prepare_fetch(
        PLAIN, deps, ScriptedPrompter({"co_dalej": "wyjdz"}), RecordingView(), threshold=THRESHOLD
    )

    assert decision == "wyjdz"
    assert len(api.requests) == 1  # tylko `count`
    assert deps.store.list_runs() == []  # nic nie powstało w bazie
    deps.store.close()


def test_a_zero_hit_query_stops_before_the_choice(tmp_path: Path, clock: FakeClock) -> None:
    """Zero trafień to nie błąd, ale i nie ma o co pytać — przepływ kończy się komunikatem."""
    api = counting_api(0)
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({})

    decision, plan = flow.prepare_fetch(PLAIN, deps, prompter, view, threshold=THRESHOLD)

    assert decision == "wyjdz" and plan.count == 0
    assert prompter.asked == []  # żadnego pytania
    assert "Brak firm spełniających kryteria." in view.messages
    deps.store.close()


def test_empty_criteria_are_refused_before_the_count_request(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Bez filtra zapytanie objęłoby cały rejestr — odmowa zapada przed siecią."""
    api = counting_api(1)
    deps = deps_for(tmp_path, clock, api)

    with pytest.raises(ConfigError, match="przynajmniej jedno kryterium"):
        flow.prepare_fetch(Criteria(), deps, DefaultsPrompter(), RecordingView())

    assert api.requests == []
    deps.store.close()


# ----------------------------------------------------------------------------- „popraw kryteria”


def test_correcting_the_criteria_returns_the_decision_to_the_caller(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`flow` nie zbiera kryteriów sam — oddaje decyzję „popraw” temu, kto go wywołał."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)

    decision, plan = flow.prepare_fetch(
        PLAIN, deps, ScriptedPrompter({"co_dalej": "popraw"}), RecordingView(), threshold=THRESHOLD
    )

    assert decision == "popraw"
    assert plan.criteria == PLAIN  # kryteria wracają nietknięte
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_a_second_pass_over_new_criteria_counts_exactly_once_again(
    tmp_path: Path, clock: FakeClock
) -> None:
    """„Jedno zapytanie na wersję kryteriów”: poprawka kosztuje dokładnie jeden `count` więcej."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    prompter = ScriptedPrompter({"co_dalej": ["popraw", "lista"]})
    view = RecordingView()

    first, _ = flow.prepare_fetch(PLAIN, deps, prompter, view, threshold=THRESHOLD)
    second, _ = flow.prepare_fetch(
        criteria(miasto="Łomża"), deps, prompter, view, threshold=THRESHOLD
    )

    assert (first, second) == ("popraw", "lista")
    assert len(count_requests(api)) == 2
    assert prompter.asked == ["co_dalej", "co_dalej"]
    deps.store.close()


# ----------------------------------------------------------------------------- powyżej progu


def test_above_the_threshold_no_fetch_starts_and_a_split_is_proposed(
    tmp_path: Path, clock: FakeClock
) -> None:
    """§C i scenariusz 9: powyżej progu program proponuje podział i sam nie startuje."""
    api = counting_api(400_000)
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"podzial": "partie"})

    decision, plan = flow.prepare_fetch(
        PLAIN, deps, prompter, view, threshold=THRESHOLD, today=TODAY
    )

    assert decision == "partie"
    assert prompter.asked == ["podzial"]  # `co_dalej` w ogóle nie padło
    assert plan.batches is not None and len(plan.batches.batches) > 1
    assert len(api.requests) == 1  # nadal tylko `count`
    assert "Propozycja podziału" in view.block_titled("Propozycja podziału").title
    deps.store.close()


def test_above_the_threshold_the_question_has_no_safe_default(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`--tak` bez `--maks` i bez `--partie` musi odmówić, a nie ruszyć 16-godzinne pobranie."""
    api = counting_api(400_000)
    deps = deps_for(tmp_path, clock, api)

    with pytest.raises(ConfigError) as caught:
        flow.prepare_fetch(
            PLAIN, deps, DefaultsPrompter(), RecordingView(), threshold=THRESHOLD, today=TODAY
        )

    assert caught.value.exit_code == 3
    assert len(api.requests) == 1
    deps.store.close()


def test_an_explicit_max_records_takes_the_query_back_under_the_threshold(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`--maks` to świadoma decyzja operatora — wtedy próg nie blokuje i pyta się normalnie."""
    api = counting_api(400_000)
    deps = deps_for(tmp_path, clock, api)

    decision, plan = flow.prepare_fetch(
        criteria(miasto="Białystok", max_rekordow=1_000),
        deps,
        ScriptedPrompter({"co_dalej": "lista"}),
        RecordingView(),
        threshold=THRESHOLD,
        today=TODAY,
    )

    assert decision == "lista"
    assert plan.estimate is not None and plan.estimate.requests_list == 40  # 1 000 / 25
    deps.store.close()


def test_the_split_table_is_shown_before_the_question_is_asked(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Operator nie może wybierać podziału, zanim zobaczy, ile partii i ile czasu to znaczy."""
    api = counting_api(400_000)
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(
        PLAIN,
        deps,
        ScriptedPrompter({"podzial": "wyjdz"}),
        view,
        threshold=THRESHOLD,
        today=TODAY,
    )

    split = view.block_titled("Propozycja podziału")
    assert split.headers == ("partia", "zakres dat", "szacowane trafienia", "zapytania", "czas")
    assert len(split.rows) > 1
    deps.store.close()


# ----------------------------------------------------------------------------- ścieżka raportu


REPORT_ITEM = {
    "id": "r1",
    "nazwa": "Zarejestrowane działalności - województwo podlaskie",
    "format": ".csv",
    "raport": f"{BASE}/raport/r1",
    "data-utworzenia": "2026-09-04 06:40:59",
}


def report_api(count: int, *, reports: list[dict[str, str]] | None = None) -> FakeApi:
    """API z listą raportów i `count` — dwie ścieżki, które `prepare_fetch` może wybrać.

    Kształt elementu listy jest 1:1 z próbką sondy (`tests/fixtures/raporty.json`):
    klucze to `format`, `raport` i `data-utworzenia`, nie ich potoczne odpowiedniki.
    """
    api = FakeApi()
    body = {"raporty": [REPORT_ITEM] if reports is None else reports}

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/raporty" in url:
            return httpx.Response(200, json=body)
        if "/firmy" in url and request.url.params.get("limit") == "1":
            return httpx.Response(200, json={"count": count, "firmy": []})
        raise AssertionError(f"nieoczekiwane żądanie: {url}")

    api.fallback = fallback
    return api


def test_the_report_is_offered_before_the_count_and_skips_it_when_accepted(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Raport nie potrzebuje `count`, więc przyjęcie oferty oszczędza nawet to jedno żądanie."""
    api = report_api(400_000)
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"uzyc_raportu": True})

    decision, plan = flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie"), deps, prompter, view, threshold=THRESHOLD
    )

    assert decision == "raport"
    assert plan.report is not None and plan.report.id == "r1"
    assert prompter.asked == ["uzyc_raportu"]
    assert count_requests(api) == []  # `count` nie padł w ogóle
    assert "Dostępny raport dzienny" in view.text()
    deps.store.close()


def test_declining_the_report_falls_through_to_the_count(tmp_path: Path, clock: FakeClock) -> None:
    """Odmowa raportu nie kończy przepływu — schodzimy do zwykłej ścieżki API."""
    api = report_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    prompter = ScriptedPrompter({"uzyc_raportu": False, "co_dalej": "lista"})

    decision, plan = flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie"), deps, prompter, RecordingView(), threshold=THRESHOLD
    )

    assert decision == "lista"
    assert plan.count == 1_240
    assert prompter.asked == ["uzyc_raportu", "co_dalej"]
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_the_report_offer_names_its_generation_date_and_its_known_gaps(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wybór między raportem a API jest świadomy tylko wtedy, gdy widać, czego w raporcie brak."""
    api = report_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie"),
        deps,
        ScriptedPrompter({"uzyc_raportu": False, "co_dalej": "wyjdz"}),
        view,
        threshold=THRESHOLD,
    )

    offer = view.text()
    assert "2026-09-04 06:40:59" in offer
    assert "WYKREŚLONYCH" in offer
    deps.store.close()


def test_a_query_the_report_cannot_cover_never_asks_about_it(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Raport jest per województwo — dla dwóch województw oferta byłaby kłamstwem."""
    api = report_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    flow.prepare_fetch(
        criteria(wojewodztwo=("podlaskie", "mazowieckie")),
        deps,
        prompter,
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert prompter.asked == ["co_dalej"]
    assert not any("/raporty" in r for r in api.requests)
    deps.store.close()


def test_asking_for_the_report_source_when_none_exists_is_a_configuration_error(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`--zrodlo raport` bez pokrywającego raportu ma powiedzieć wprost, co zrobić."""
    api = report_api(1_240, reports=[])
    deps = deps_for(tmp_path, clock, api)

    with pytest.raises(ConfigError, match="--zrodlo api"):
        flow.prepare_fetch(
            criteria(wojewodztwo="podlaskie"),
            deps,
            DefaultsPrompter(),
            RecordingView(),
            source="raport",
        )

    assert count_requests(api) == []
    deps.store.close()


def test_the_report_source_does_not_ask_for_confirmation(tmp_path: Path, clock: FakeClock) -> None:
    """`--zrodlo raport` to już decyzja — pytanie „użyć raportu?” byłoby pytaniem o to samo."""
    api = report_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    prompter = ScriptedPrompter({})

    decision, _ = flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie"),
        deps,
        prompter,
        RecordingView(),
        source="raport",
    )

    assert decision == "raport"
    assert prompter.asked == []
    deps.store.close()


def test_the_api_source_skips_the_report_offer_entirely(tmp_path: Path, clock: FakeClock) -> None:
    """`--zrodlo api` pomija nawet listowanie raportów — to żądanie, którego nikt nie zamawiał."""
    api = report_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie"),
        deps,
        prompter,
        RecordingView(),
        source="api",
        threshold=THRESHOLD,
    )

    assert prompter.asked == ["co_dalej"]
    assert not any("/raporty" in r for r in api.requests)
    deps.store.close()


# ----------------------------------------------------------------------------- wznowienie


def seed_unfinished_run(deps: Deps, source: Criteria, *, run_id: str = "run-przerwany") -> str:
    """Niedokończone pobranie z tym samym odciskiem kryteriów, jakie poda test."""
    deps.store.start_run(
        run_id=run_id,
        criteria_json=source.model_dump_json(),
        criteria_hash=source.fingerprint(),
        profile_hash=deps.profile.profile_hash(),
        mode="lista",
        tool_version="0.1.0",
        cursor_mode="links",
    )
    deps.store.save_page(run_id, page_index=0, records=[], next_cursor=f"{BASE}/firmy?page=1")
    deps.store.update_run_status(run_id, "przerwany")
    return run_id


def test_an_unfinished_run_is_offered_before_anything_is_counted(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wznowienie idzie pierwsze — dopytywanie o `count` dla pracy już rozpoczętej to strata."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    run_id = seed_unfinished_run(deps, PLAIN)
    prompter = ScriptedPrompter({"wznowic": True})
    view = RecordingView()

    decision, plan = flow.prepare_fetch(PLAIN, deps, prompter, view, threshold=THRESHOLD)

    assert decision == "wznow"
    assert plan.resumable is not None and plan.resumable.run_id == run_id
    assert prompter.asked == ["wznowic"]
    assert api.requests == []  # ani `count`, ani lista raportów
    assert run_id in view.text()
    deps.store.close()


def test_declining_the_resume_falls_through_to_a_normal_count(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Odmowa wznowienia to nie koniec — zaczynamy zwykłą ścieżkę od `count`."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    seed_unfinished_run(deps, PLAIN)
    prompter = ScriptedPrompter({"wznowic": False, "co_dalej": "lista"})

    decision, plan = flow.prepare_fetch(PLAIN, deps, prompter, RecordingView(), threshold=THRESHOLD)

    assert decision == "lista" and plan.count == 1_240
    assert prompter.asked == ["wznowic", "co_dalej"]
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_an_unfinished_run_with_other_criteria_is_not_offered(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wznowienie rozpoznaje się po odcisku kryteriów — cudza praca nie może się podszyć."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    seed_unfinished_run(deps, criteria(miasto="Łomża"))
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    decision, _ = flow.prepare_fetch(PLAIN, deps, prompter, RecordingView(), threshold=THRESHOLD)

    assert decision == "lista"
    assert prompter.asked == ["co_dalej"]  # o wznowienie nie zapytano
    deps.store.close()


def test_the_noninteractive_mode_resumes_by_default(tmp_path: Path, clock: FakeClock) -> None:
    """Harmonogram ma dokończyć wczorajszą pracę, a nie zaczynać od zera co dobę."""
    api = counting_api(1_240)
    deps = deps_for(tmp_path, clock, api)
    run_id = seed_unfinished_run(deps, PLAIN)

    decision, plan = flow.prepare_fetch(
        PLAIN, deps, DefaultsPrompter(), RecordingView(), threshold=THRESHOLD
    )

    assert decision == "wznow"
    assert plan.resumable is not None and plan.resumable.run_id == run_id
    deps.store.close()


# ------------------------------------------------------- okres przejściowy PKD (ADR-0012)

# Kryteria dobrane pod trzy sytuacje, które tablica przejścia rozróżnia. Kody są prawdziwe
# (patrz `tests/support.pkd_map`), więc test nie opiera się na klasyfikacji, której nie ma.
FRYZJER = criteria(miasto="Łomża", pkd="9621Z")  # poprzednik wciąga kosmetykę → pytanie
ODZIEZ = criteria(miasto="Łomża", pkd="1423Z")  # poprzednik prowadzi tylko tu → bez pytania
BEZ_PRZEJSCIA = criteria(miasto="Łomża", pkd="0111Z")  # kod spoza tablicy → nic się nie zmienia

WASKI_COUNT = 2_266
SZEROKI_COUNT = 9_077


def vintage_api(*, waski: int = WASKI_COUNT, szeroki: int = SZEROKI_COUNT) -> FakeApi:
    """`count` różny dla obu populacji — inaczej nie widać, która liczba trafiła do planu.

    Rozpoznanie po obecności kodu PKD 2007 w URL, bo to jedyna różnica między zapytaniami;
    gdyby `to_params` przestało go wysyłać, ten test zobaczy wąską liczbę tam, gdzie oczekuje
    szerokiej, zamiast przejść na zgodnej atrapie.
    """
    api = FakeApi()

    def fallback(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/firmy" in url and request.url.params.get("limit") == "1":
            return httpx.Response(200, json={"count": szeroki if "9602Z" in url else waski})
        raise AssertionError(f"nieoczekiwane żądanie w fazie decyzji: {url}")

    api.fallback = fallback
    return api


def deps_with_map(tmp_path: Path, clock: FakeClock, api: FakeApi) -> Deps:
    """Zależności z miniaturową tablicą przejścia zamiast pełnej (264 kody)."""
    deps = deps_for(tmp_path, clock, api)
    deps.pkd_map = pkd_map()
    return deps


class WatchfulPrompter(ScriptedPrompter):
    """`ScriptedPrompter`, który przy każdym pytaniu notuje, ile żądań już poszło.

    Bez tego „oba `count` **przed** zgodą i żadnego po wyborze" jest niesprawdzalne: sama
    liczba żądań na końcu nie mówi, po której stronie pytania padły. A kolejność jest tu
    całą treścią niezmiennika — zapytanie wydane po zgodzie znaczy, że operator zgodził się
    na tabelę kosztów policzoną z czegoś innego.
    """

    def __init__(self, answers: Mapping[str, object], api: FakeApi) -> None:
        super().__init__(answers)
        self._api = api
        self.requests_when_asked: dict[str, int] = {}

    def ask(self, question: Question) -> str:
        self.requests_when_asked[question.id] = len(self._api.requests)
        return super().ask(question)


def test_an_ambiguous_vintage_costs_two_counts_both_before_the_consent(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Niezmiennik w nowej postaci: dwa `count`, oba przed wyborem, ani jednego po nim.

    Drugie żądanie jest jedynym powodem, dla którego pytanie o rocznik niesie liczby —
    a liczby są jedynym powodem, dla którego to pytanie da się uczciwie zadać.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    prompter = WatchfulPrompter({"rocznik_pkd": "szerokie", "co_dalej": "lista"}, api)

    decision, plan = flow.prepare_fetch(
        FRYZJER, deps, prompter, RecordingView(), threshold=THRESHOLD
    )

    assert decision == "lista"
    assert prompter.asked == ["rocznik_pkd", "co_dalej"]
    assert prompter.requests_when_asked["rocznik_pkd"] == 2  # obie populacje policzone
    assert prompter.requests_when_asked["co_dalej"] == 2  # po wyborze ani jednego więcej
    assert len(count_requests(api)) == 2
    deps.store.close()


def test_the_chosen_population_reuses_the_count_it_was_priced_with(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Tabela kosztów pokazuje liczbę **wybranej** populacji, nie tej, od której zaczęto."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)

    decision, plan = flow.prepare_fetch(
        FRYZJER,
        deps,
        ScriptedPrompter({"rocznik_pkd": "szerokie", "co_dalej": "lista"}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert decision == "lista"
    assert plan.count == SZEROKI_COUNT
    assert plan.criteria.pkd_2007 == ("9602Z",)
    assert len(count_requests(api)) == 2  # trzeciego, „na wybraną populację", nie ma
    deps.store.close()


def test_the_narrow_choice_keeps_todays_population_and_its_own_count(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Odmowa poszerzenia zostawia kryteria nietknięte — i wycenia to, co naprawdę poleci."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)

    _, plan = flow.prepare_fetch(
        FRYZJER,
        deps,
        ScriptedPrompter({"rocznik_pkd": "waskie", "co_dalej": "lista"}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert plan.count == WASKI_COUNT
    assert plan.criteria.pkd_2007 == ()
    assert plan.criteria.wszystkie_pkd() == ("9621Z",)
    deps.store.close()


def test_the_vintage_question_carries_both_numbers_and_the_industry_it_drags_in(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Ekran ma pokazać **rozmiar** wyboru; bez liczb zostaje pytanie „szerzej czy węziej?"."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(
        FRYZJER,
        deps,
        ScriptedPrompter({"rocznik_pkd": "waskie", "co_dalej": "wyjdz"}),
        view,
        threshold=THRESHOLD,
    )

    oferta = view.block_titled("Stare kody PKD").as_text()
    assert "9602Z" in oferta
    assert "Fryzjerstwo i pozostałe zabiegi kosmetyczne" in oferta
    assert "pielęgnacji urody" in oferta  # branża, która wpadnie razem z naszą
    assert "2 266" in oferta and "9 077" in oferta
    deps.store.close()


def test_leaving_at_the_vintage_question_never_reaches_the_cost_table(
    tmp_path: Path, clock: FakeClock
) -> None:
    """„Wróć do menu" na pytaniu o rocznik kończy przepływ przed zgodą i przed pobieraniem.

    Decyzja to `anuluj`, nie `wyjdz`, i ta różnica ma konsekwencję: `wyjdz` znaczy „widziałem
    koszt i rezygnuję", więc kryteria są rozstrzygnięte i kreator proponuje ich zapis. Tu nic
    nie zapadło — zapis niósłby wybór rocznika, którego operator właśnie odmówił dokonać
    (audyt 2026-09-07). Pilnuje tego `test_wizard_vintage_yaml.py`.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"rocznik_pkd": "wyjdz"})

    decision, plan = flow.prepare_fetch(FRYZJER, deps, prompter, view, threshold=THRESHOLD)

    assert decision == "anuluj"
    assert plan.count == 0
    assert prompter.asked == ["rocznik_pkd"]  # o `co_dalej` nie zapytano
    assert not any("Znaleziono" in title for title in view.titles())
    assert len(count_requests(api)) == 2  # i ani jednego więcej
    deps.store.close()


def test_a_clean_expansion_is_applied_without_a_question_and_without_a_second_count(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Rozszerzenie czyste to naprawa, nie wybór — nie ma dwóch populacji do wyceny."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    decision, plan = flow.prepare_fetch(ODZIEZ, deps, prompter, view, threshold=THRESHOLD)

    assert decision == "lista"
    assert prompter.asked == ["co_dalej"]
    assert plan.criteria.pkd_2007 == ("1412Z",)
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_a_clean_expansion_is_still_shown_on_screen(tmp_path: Path, clock: FakeClock) -> None:
    """Ciche poszerzenie zapytania daje wynik, którego operator nie umie wytłumaczyć.

    Ten sam defekt co cicha podmiana kodu przez model (przebieg A5): liczba w tabeli kosztów
    przestaje odpowiadać kryteriom, które operator ma przed oczami.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(
        ODZIEZ, deps, ScriptedPrompter({"co_dalej": "wyjdz"}), view, threshold=THRESHOLD
    )

    blok = view.block_titled("Doliczam stare kody PKD").as_text()
    assert "1412Z" in blok
    assert "Produkcja odzieży roboczej" in blok
    deps.store.close()


def test_a_pkd_outside_the_transition_table_changes_nothing(
    tmp_path: Path, clock: FakeClock
) -> None:
    """464 z 728 podklas nie mają poprzednika — dla nich przepływ ma zostać taki, jak był."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    _, plan = flow.prepare_fetch(BEZ_PRZEJSCIA, deps, prompter, view, threshold=THRESHOLD)

    assert prompter.asked == ["co_dalej"]
    assert plan.criteria == BEZ_PRZEJSCIA
    assert len(count_requests(api)) == 1
    assert view.warnings == []  # ostrzeżenie zawsze prawdziwe uczy je ignorować
    deps.store.close()


def test_a_run_without_the_transition_table_behaves_like_before(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Brak tablicy wyłącza rozszerzanie, a nie narzędzie — `build_deps` ostrzega osobno."""
    api = vintage_api()
    deps = deps_for(tmp_path, clock, api)
    deps.pkd_map = None
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    _, plan = flow.prepare_fetch(FRYZJER, deps, prompter, RecordingView(), threshold=THRESHOLD)

    assert prompter.asked == ["co_dalej"]
    assert plan.criteria.pkd_2007 == ()
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_the_noninteractive_run_keeps_todays_population_and_names_what_it_skips(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Sub-decyzja 5: harmonogram nie zmienia populacji dlatego, że wyszła nowa wersja.

    Milczenie byłoby jednak całym defektem, więc pominięcie ma zostać nazwane — razem
    z flagą, którą operator włączy poszerzenie, gdy tego chce.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()

    _, plan = flow.prepare_fetch(
        FRYZJER,
        deps,
        DefaultsPrompter(),
        view,
        threshold=THRESHOLD,
        rocznik_2007=False,
    )

    assert plan.criteria.pkd_2007 == ()
    assert len(count_requests(api)) == 1  # bez wyboru nie ma po co liczyć drugiej populacji
    (ostrzezenie,) = view.warnings
    assert "9602Z" in ostrzezenie and "--pkd-2007" in ostrzezenie
    deps.store.close()


def test_the_explicit_wide_flag_spends_one_count_on_the_population_it_names(
    tmp_path: Path, clock: FakeClock
) -> None:
    """`--pkd-2007` to już decyzja — drugi `count` liczyłby populację, której nikt nie wybierze."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    _, plan = flow.prepare_fetch(
        FRYZJER, deps, prompter, RecordingView(), threshold=THRESHOLD, rocznik_2007=True
    )

    assert prompter.asked == ["co_dalej"]
    assert plan.criteria.pkd_2007 == ("9602Z",)
    assert plan.count == SZEROKI_COUNT
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_the_explicit_wide_flag_still_shows_which_industries_it_drags_in(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Flaga jest zgodą na dołożenie kodów, nie na nieoglądanie tego, co dokładają.

    Znalezisko z przeglądu testów 2026-09-07: `--pkd-2007` wysyłało `pkd=9602Z`, a kod nie padał
    na żadnym ekranie — `vintage_applied` pokazywano wyłącznie dla rozszerzeń **czystych**,
    a przy fryzjerstwie ten zbiór jest pusty. Blok kryteriów w `cli.py` też go nie pokazywał,
    bo drukuje kryteria sprzed rozszerzenia. Ta sama cisza, którą docstring `vintage_applied`
    nazywa defektem, tylko na drugiej gałęzi.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()

    flow.prepare_fetch(
        FRYZJER,
        deps,
        ScriptedPrompter({"co_dalej": "lista"}),
        view,
        threshold=THRESHOLD,
        rocznik_2007=True,
    )

    tekst = " ".join(b.as_text() for b in view.blocks)
    assert "9602Z" in tekst, "dołożony kod nie pada na żadnym ekranie"
    # Operator bierze na siebie właśnie cudzą branżę, więc ona też musi być nazwana.
    assert "9622Z" in tekst
    deps.store.close()


def test_criteria_that_already_carry_the_vintage_are_not_expanded_again(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Plik zapytania ma powtórzyć przebieg co do kodu — przeliczenie mogłoby go poszerzyć."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    z_pliku = criteria(miasto="Łomża", pkd="9621Z", pkd_2007="9602Z")
    prompter = ScriptedPrompter({"co_dalej": "lista"})

    _, plan = flow.prepare_fetch(z_pliku, deps, prompter, RecordingView(), threshold=THRESHOLD)

    assert prompter.asked == ["co_dalej"]
    assert plan.criteria.pkd_2007 == ("9602Z",)
    assert len(count_requests(api)) == 1
    deps.store.close()


def test_an_interrupted_wide_run_is_found_from_a_narrow_entry(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Wybór rocznika zmienia odcisk palca, więc wznowienia szuka się dla obu kandydatów.

    Bez tego przerwane pobranie szerokie byłoby niewidoczne dla wąskiego wejścia i operator
    zaczynałby od zera, mając połowę roboty w bazie. Baza, nie sieć — zero żądań.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    szerokie = criteria(miasto="Łomża", pkd="9621Z", pkd_2007="9602Z")
    run_id = seed_unfinished_run(deps, szerokie)

    decision, plan = flow.prepare_fetch(
        FRYZJER,
        deps,
        ScriptedPrompter({"wznowic": True}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert decision == "wznow"
    assert plan.resumable is not None and plan.resumable.run_id == run_id
    assert plan.criteria.pkd_2007 == ("9602Z",)  # wznawiamy tę populację, którą przerwano
    assert api.requests == []
    deps.store.close()


def test_an_interrupted_narrow_run_is_still_found_when_a_wide_one_is_possible(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Kandydat wąski ma pierwszeństwo — to on odpowiada kryteriom, które operator podał."""
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    run_id = seed_unfinished_run(deps, FRYZJER)

    decision, plan = flow.prepare_fetch(
        FRYZJER,
        deps,
        ScriptedPrompter({"wznowic": True}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert decision == "wznow"
    assert plan.resumable is not None and plan.resumable.run_id == run_id
    assert plan.criteria.pkd_2007 == ()
    assert api.requests == []
    deps.store.close()


def test_the_report_path_asks_about_the_vintage_without_spending_a_count(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Raport filtrujemy lokalnie, więc poszerzenie nie kosztuje żadnego żądania.

    Liczb tu nie ma i nie wolno ich wymyślić: cała wartość tego ekranu polega na tym,
    że jego liczby są mierzone.
    """
    api = report_api(1_240)
    deps = deps_with_map(tmp_path, clock, api)
    view = RecordingView()
    prompter = ScriptedPrompter({"uzyc_raportu": True, "rocznik_pkd": "szerokie"})

    decision, plan = flow.prepare_fetch(
        criteria(wojewodztwo="podlaskie", pkd="9621Z"), deps, prompter, view, threshold=THRESHOLD
    )

    assert decision == "raport"
    assert plan.criteria.pkd_2007 == ("9602Z",)
    assert count_requests(api) == []
    oferta = view.block_titled("Stare kody PKD").as_text()
    assert "wiadomo dopiero po pobraniu raportu" in oferta
    assert "Tylko PKD 2025:" not in oferta  # liczb nie ma, więc się ich nie podaje
    deps.store.close()


# --------------------------------------------------- audyt 2026-09-07: trzy defekty rocznika


def test_declining_a_resume_does_not_silently_swap_the_population(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Odmowa wznowienia ma zostawić kryteria takie, jakie ekran przed chwilą zapowiedział.

    `criteria = kandydat` zapadało **przed** pytaniem o wznowienie, więc odmowa zostawiała
    kryteria przy znalezionym kandydacie i reszta przepływu leciała jego populacją. Przy
    `1423Z` blok „Doliczam stare kody PKD" mówił o `1412Z`, a do API szło zapytanie bez niego.
    W ostrzejszej postaci odmowa kasowała działanie jawnej flagi `--pkd-2007`.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    # Przerwany przebieg zapisany pod kryteriami **sprzed** rozszerzenia — tak zapisuje się
    # `--tak` i `--bez-pkd-2007`, czyli najczęstsze wejście harmonogramu.
    seed_unfinished_run(deps, ODZIEZ)

    _, plan = flow.prepare_fetch(
        ODZIEZ,
        deps,
        ScriptedPrompter({"wznowic": False, "co_dalej": "lista"}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert plan.criteria.pkd_2007 == ("1412Z",), "odmowa cofnęła zapowiedziane rozszerzenie"
    deps.store.close()


def test_a_predecessor_the_operator_already_asked_for_is_not_added_again(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Kod, który operator sam wybrał, nie jest „dołożeniem" i nie ma o czym pytać.

    `rozszerz` pomijało poprzednika tylko wtedy, gdy już go widziało, a nie wtedy, gdy stał
    w wybranym zestawie. Zapytanie o `{9313Z, 8551Z}` dokładało `8551Z` do `pkd_2007`, choć
    `pkd=8551Z` i tak leci — więc oba warianty dawały **identyczny URL**, program wydawał
    drugi `count` na populację nieodróżnialną od pierwszej i pytał o wybór, którego nie ma.
    """
    mapa = pkd_map()

    rozsz = mapa.rozszerz(["9313Z", "8551Z"])

    assert rozsz.kody_2007 == (), "dołożono kod, który operator już wybrał"
    assert not rozsz.wymaga_pytania, "pytanie o wybór, który niczego nie zmienia"


def test_the_wide_candidate_survives_a_round_trip_through_the_query_file(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Odcisk kandydata szerokiego musi przeżyć zapis do pliku zapytania i odczyt z niego.

    `model_copy(update=…)` w pydanticu v2 **nie waliduje**, więc `_dedupe` — sortujące właśnie
    po to, żeby odcisk nie zależał od kolejności — nie odpalało się. Ta sama treść wczytana
    z YAML-a szła przez walidację i dawała inny odcisk, więc przerwany szeroki przebieg był
    niewidoczny dla wznowienia z zapisanego pliku. To ta klasa, którą pętla trzech kandydatów
    miała zamknąć.
    """
    # Prawdziwa tablica, bo przypadek rozróżniający wymaga kodu o rozszerzeniu **mieszanym** —
    # czystym i niejednoznacznym naraz — a takich w trzyelementowej atrapie nie ma. `2366Z`
    # daje `('2369Z', '2223Z')`: posortowane wewnątrz każdej połówki, nieposortowane razem.
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    deps.pkd_map = load_pkd_map()

    _, plan = flow.prepare_fetch(
        criteria(miasto="Łomża", pkd="2366Z"),
        deps,
        ScriptedPrompter({"rocznik_pkd": "szerokie", "co_dalej": "lista"}),
        RecordingView(),
        threshold=THRESHOLD,
    )
    z_pliku = Criteria.model_validate(plan.criteria.model_dump())

    assert plan.criteria.pkd_2007 == z_pliku.pkd_2007, "kolejność nie przeżyła walidacji"
    assert plan.criteria.fingerprint() == z_pliku.fingerprint()
    deps.store.close()


def test_the_flag_and_the_query_file_cannot_contradict_each_other_in_silence(
    tmp_path: Path, clock: FakeClock
) -> None:
    """Plik zapytania z `pkd_2007` plus `--bez-pkd-2007` to sprzeczność, nie sytuacja domyślna.

    Strażnik istniał od 2026-09-07 i **nie wykonał się ani razu**: żaden test nie podawał
    naraz `criteria.pkd_2007` i `rocznik_2007=False` (audyt 2026-09-07). Wcześniej wygrywał
    plik, po cichu — program pobierał populację szerszą, niż operator przed chwilą zażądał,
    czyli dokładnie tę ciszę o poszerzonym zapytaniu, dla której powstał ADR-0012.
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    z_pliku = criteria(miasto="Łomża", pkd="9621Z", pkd_2007="9602Z")

    with pytest.raises(ConfigError) as zlapany:
        flow.prepare_fetch(
            z_pliku,
            deps,
            ScriptedPrompter({}),
            RecordingView(),
            threshold=THRESHOLD,
            rocznik_2007=False,
        )

    tresc = str(zlapany.value)
    assert "9602Z" in tresc and "--bez-pkd-2007" in tresc
    assert api.requests == [], "odmowa ma paść przed jakimkolwiek żądaniem"
    deps.store.close()


def test_a_run_stored_before_any_expansion_is_still_found(tmp_path: Path, clock: FakeClock) -> None:
    """Przebieg zapisany bez rozszerzenia — tak zapisuje `--tak` — musi być odnaleziony.

    Wznowienia szuka się dla trzech postaci kryteriów, bo każda ma własny odcisk: wąskiej
    (z rozszerzeniem czystym), szerokiej i **wejściowej**, czyli bez żadnego rozszerzenia.
    Trzeciej brakowało do przeglądu 2026-09-07: harmonogram z `--tak`, przerwany w połowie,
    był niewidoczny dla ręcznego wejścia z tym samym `--pkd`, więc operator zaczynał od zera,
    mając robotę w bazie.

    Przypadek rozróżniający wymaga kodu o rozszerzeniu **czystym** (`1423Z`), bo tylko wtedy
    `waskie` różni się od `wejsciowe`. Dwa starsze testy używały `9621Z`, gdzie zbiór czystych
    jest pusty i trzeci kandydat jest tożsamy z pierwszym — dlatego usunięcie go z pętli nie
    czerwieniło niczego (audyt 2026-09-07).
    """
    api = vintage_api()
    deps = deps_with_map(tmp_path, clock, api)
    run_id = seed_unfinished_run(deps, ODZIEZ)

    decision, plan = flow.prepare_fetch(
        ODZIEZ,
        deps,
        ScriptedPrompter({"wznowic": True}),
        RecordingView(),
        threshold=THRESHOLD,
    )

    assert decision == "wznow"
    assert plan.resumable is not None and plan.resumable.run_id == run_id
    # Wznawiamy **tę** populację, która leży w bazie, a nie tę, którą właśnie zbudowaliśmy.
    assert plan.criteria.pkd_2007 == ()
    assert api.requests == [], "wznowienie szuka w bazie, nie w sieci"
    deps.store.close()
