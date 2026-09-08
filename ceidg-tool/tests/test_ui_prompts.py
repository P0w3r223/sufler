"""Pytający (`ui/prompts`) — kontrakt, na którym stoi „ta sama logika w każdym wejściu”.

ADR-0008, decyzja 2: flagi, YAML, `--tak` i kreator różnią się **wyłącznie** podstawionym
`Prompter`-em. Najważniejsze są tu dwie rzeczy: `DefaultsPrompter` nigdy nie podejmuje
decyzji oznaczonej `safe_default=False` (zgoda na produkcję, start powyżej progu), a
`ScriptedPrompter` psuje test głośno, gdy padnie pytanie, którego test nie przewidział —
inaczej nowe pytanie w przepływie przeszłoby niezauważone.
"""

from __future__ import annotations

import pytest

from ceidg_tool.errors import ConfigError
from ceidg_tool.ui.prompts import (
    CO_DALEJ,
    NIP,
    PODZIAL,
    PONOW_KRYTERIA,
    ROCZNIK_PKD,
    UZYC_RAPORTU,
    WZNOWIC,
    ZAPISZ_EXCEL,
    ZAPISZ_YAML,
    CancelledError,
    ConsolePrompter,
    CriteriaAnswers,
    DefaultsPrompter,
    Option,
    OverridePrompter,
    Question,
    ScriptedPrompter,
    confirm_suffix,
    criteria_questions,
    interactive_available,
    is_yes,
    make_prompter,
)

SAFE = Question(
    id="pytanie_bezpieczne",
    text="Czy kontynuować?",
    options=(Option("tak", "tak"), Option("nie", "nie")),
    default="tak",
)
UNSAFE = Question(
    id="pytanie_niebezpieczne",
    text="Decyzja bez bezpiecznej wartości domyślnej.",
    default="nie",
    safe_default=False,
)


# ----------------------------------------------------------------------------- Question


def test_question_rejects_an_answer_outside_its_options() -> None:
    """Literówka w skrypcie testu albo w `--partie` nie może przejść jako cicha decyzja."""
    with pytest.raises(ConfigError) as caught:
        SAFE.check("moze")

    assert "pytanie_bezpieczne" in str(caught.value)
    assert "tak, nie" in str(caught.value)  # komunikat wymienia dozwolone odpowiedzi


def test_question_without_options_accepts_free_text() -> None:
    assert NIP.check("3563457932") == "3563457932"


# ----------------------------------------------------------------------------- DefaultsPrompter


def test_defaults_prompter_returns_the_declared_default() -> None:
    """Tryb `--tak` i harmonogram: odpowiedź to zadeklarowana wartość, nie zgadywanie."""
    prompter = DefaultsPrompter()

    assert prompter.ask(SAFE) == "tak"
    assert prompter.text(NIP) == ""


@pytest.mark.parametrize("method", ["ask", "text"], ids=["ask", "text"])
def test_defaults_prompter_refuses_a_question_with_no_safe_default(method: str) -> None:
    """Sedno bramki nieinteraktywnej: brak bezpiecznej domyślnej = błąd konfiguracji (exit 3)."""
    prompter = DefaultsPrompter()

    with pytest.raises(ConfigError) as caught:
        getattr(prompter, method)(UNSAFE)

    assert caught.value.exit_code == 3
    assert "--tak" in str(caught.value)  # mówi, jak to obejść świadomie


def test_defaults_prompter_refuses_an_unsafe_confirmation_too() -> None:
    """`confirm` nie może być furtką omijającą `safe_default=False`."""
    with pytest.raises(ConfigError):
        DefaultsPrompter().confirm(UNSAFE, default=True)


def test_defaults_prompter_confirm_takes_the_callers_default_for_safe_questions() -> None:
    prompter = DefaultsPrompter()

    assert prompter.confirm(WZNOWIC, default=True) is True
    assert prompter.confirm(UZYC_RAPORTU, default=False) is False


def test_the_split_question_has_no_safe_default() -> None:
    """§C, scenariusz 9: powyżej progu `--tak` musi odmówić, a nie wystartować pobranie."""
    assert PODZIAL.safe_default is False

    with pytest.raises(ConfigError):
        DefaultsPrompter().ask(PODZIAL)


def test_the_ordinary_questions_do_have_safe_defaults() -> None:
    """Gdyby wznowienie albo raport zabrakło domyślnej, harmonogram przestałby działać."""
    for question in (CO_DALEJ, UZYC_RAPORTU, WZNOWIC):
        assert question.safe_default is True
        assert DefaultsPrompter().ask(question) == question.default


def test_the_vintage_question_defaults_to_the_narrower_population() -> None:
    """ADR-0012, sub-decyzja 5: bezpieczne domyślne jest to, które **nie** zmienia populacji.

    Szerszy wybór wciąga branże, o które nikt nie prosił, więc harmonogram bez jawnej decyzji
    ma zachować dzisiejsze zachowanie — a nie zmienić zawartość wyniku dlatego, że narzędzie
    się zaktualizowało. Odwrotna domyślna byłaby cichą zmianą danych w cudzym raporcie.
    """
    assert ROCZNIK_PKD.safe_default is True
    assert ROCZNIK_PKD.default == "waskie"
    assert DefaultsPrompter().ask(ROCZNIK_PKD) == "waskie"


def test_the_vintage_question_offers_a_way_back_to_the_menu() -> None:
    """Wybór między dwiema populacjami bywa decyzją, której operator nie chce podjąć teraz."""
    assert [option.value for option in ROCZNIK_PKD.options] == ["waskie", "szerokie", "wyjdz"]


# ----------------------------------------------------------------------------- ScriptedPrompter


def test_scripted_prompter_answers_by_question_id_and_records_the_sequence() -> None:
    """Testy zależą od identyfikatora pytania, nie od brzmienia zdania — to celowe."""
    prompter = ScriptedPrompter({"pytanie_bezpieczne": "nie", "nip": "3563457932"})

    assert prompter.ask(SAFE) == "nie"
    assert prompter.text(NIP) == "3563457932"
    assert prompter.asked == ["pytanie_bezpieczne", "nip"]


def test_scripted_prompter_fails_loudly_on_a_question_the_test_did_not_expect() -> None:
    """Nowe pytanie w przepływie ma wywrócić test, a nie po cichu wziąć wartość domyślną."""
    prompter = ScriptedPrompter({})

    with pytest.raises(AssertionError) as caught:
        prompter.ask(SAFE)

    assert "pytanie_bezpieczne" in str(caught.value)
    assert prompter.asked == ["pytanie_bezpieczne"]  # zapisane mimo błędu


def test_scripted_prompter_serves_a_list_of_answers_in_order() -> None:
    """Pętla „popraw kryteria” pyta o to samo kilka razy — kolejne odpowiedzi z listy."""
    prompter = ScriptedPrompter({"pytanie_bezpieczne": ["tak", "nie"]})

    assert prompter.ask(SAFE) == "tak"
    assert prompter.ask(SAFE) == "nie"


def test_scripted_prompter_reports_a_script_that_ran_out_of_answers() -> None:
    prompter = ScriptedPrompter({"pytanie_bezpieczne": ["tak"]})
    prompter.ask(SAFE)

    with pytest.raises(AssertionError, match="Skończyły się odpowiedzi"):
        prompter.ask(SAFE)


def test_scripted_prompter_still_validates_the_answer_against_the_options() -> None:
    """Skrypt testu też nie może wstawić odpowiedzi, której przepływ nie zna."""
    prompter = ScriptedPrompter({"pytanie_bezpieczne": "moze"})

    with pytest.raises(ConfigError):
        prompter.ask(SAFE)


# ----------------------------------------------------------------------------- OverridePrompter


def test_override_prompter_answers_the_overridden_question_without_asking_below() -> None:
    """`--partie` to właśnie to: jedna odpowiedź narzucona flagą, reszta jak zwykle."""
    base = ScriptedPrompter({"pytanie_bezpieczne": "tak"})
    prompter = OverridePrompter(base, {"podzial": "partie"})

    assert prompter.ask(PODZIAL) == "partie"
    assert base.asked == []  # pytanie nie zeszło niżej


def test_override_prompter_passes_other_questions_to_the_base() -> None:
    base = ScriptedPrompter({"pytanie_bezpieczne": "nie"})
    prompter = OverridePrompter(base, {"podzial": "partie"})

    assert prompter.ask(SAFE) == "nie"
    assert base.asked == ["pytanie_bezpieczne"]


def test_override_prompter_validates_the_overridden_value() -> None:
    """Zła wartość z flagi ma się wywalić na walidacji pytania, nie wejść do przepływu."""
    prompter = OverridePrompter(DefaultsPrompter(), {"podzial": "nieistniejaca"})

    with pytest.raises(ConfigError):
        prompter.ask(PODZIAL)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("tak", True), ("TAK", True), ("true", True), ("1", True), ("nie", False), ("0", False)],
    ids=["tak", "wielkie_litery", "true", "jedynka", "nie", "zero"],
)
def test_override_prompter_reads_confirmations_case_insensitively(raw: str, expected: bool) -> None:
    prompter = OverridePrompter(DefaultsPrompter(), {"wznowic": raw})

    assert prompter.confirm(WZNOWIC, default=not expected) is expected


# ----------------------------------------------------------------- wybór trybu interaktywnego


@pytest.mark.parametrize(
    ("stdin_tty", "stdout_tty", "yes", "expected"),
    [
        (True, True, False, True),
        (True, True, True, False),
        (False, True, False, False),
        (True, False, False, False),
        (False, False, True, False),
    ],
    ids=["terminal", "terminal_z_tak", "wejscie_z_pliku", "wyjscie_do_pliku", "harmonogram"],
)
def test_interactive_is_available_only_in_a_real_terminal_without_yes(
    stdin_tty: bool, stdout_tty: bool, yes: bool, expected: bool
) -> None:
    """Kreator w harmonogramie albo z przekierowanym wejściem zawisłby na pytaniu."""
    assert interactive_available(stdin_tty=stdin_tty, stdout_tty=stdout_tty, yes=yes) is expected


@pytest.mark.parametrize(
    ("yes", "interactive"),
    [(True, True), (True, False), (False, False)],
    ids=["tak_w_terminalu", "tak_bez_terminala", "bez_terminala"],
)
def test_make_prompter_falls_back_to_defaults_whenever_asking_is_impossible(
    yes: bool, interactive: bool
) -> None:
    assert isinstance(make_prompter(yes=yes, interactive=interactive), DefaultsPrompter)


# ----------------------------------------------------------------------------- CriteriaAnswers


def test_criteria_answers_splits_a_comma_separated_list() -> None:
    answers = CriteriaAnswers()

    answers.set_list("miasto", "Białystok, Łomża ,  Suwałki")

    assert answers.values["miasto"] == ("Białystok", "Łomża", "Suwałki")


@pytest.mark.parametrize("raw", ["", "   ", " , , "], ids=["puste", "spacje", "same_przecinki"])
def test_criteria_answers_ignores_an_empty_list_answer(raw: str) -> None:
    """Pusta odpowiedź znaczy „bez filtra”, a nie „filtr o pustej wartości”."""
    answers = CriteriaAnswers()

    answers.set_list("miasto", raw)

    assert "miasto" not in answers.values


def test_criteria_answers_trims_a_scalar_and_skips_it_when_blank() -> None:
    answers = CriteriaAnswers()

    answers.set_scalar("data_od", "  2020-01-01 ")
    answers.set_scalar("data_do", "   ")

    assert answers.values == {"data_od": "2020-01-01"}


def test_criteria_questions_cover_the_fields_the_wizard_promises() -> None:
    """Kreator ma zebrać komplet kryteriów — brak pytania = pole nieosiągalne z menu."""
    ids = {q.id for q in criteria_questions()}

    assert {"wojewodztwo", "miasto", "pkd", "status", "data_od", "data_do"} <= ids
    assert "max_rekordow" in ids  # bez tego nie da się obejść progu z kreatora


def test_every_catalogue_question_has_a_stable_ascii_id() -> None:
    """Identyfikatory trafiają do skryptów testów i do `--partie`; muszą być stabilne i ASCII."""
    for question in (CO_DALEJ, UZYC_RAPORTU, WZNOWIC, PODZIAL, NIP, *criteria_questions()):
        assert question.id.isascii() and question.id.islower()
        assert " " not in question.id


# ------------------------------------------------------- przerwanie i awaria backendu


class _Answer:
    """Atrapa obiektu `questionary`: `ask()` zwraca to, co podano."""

    def __init__(self, value: object) -> None:
        self._value = value

    def ask(self) -> object:
        return self._value


class _Backend:
    """Atrapa `questionary` — `None` udaje Ctrl+C, wyjątek udaje konsolę bez obsługi."""

    def __init__(self, value: object, *, explode: bool = False) -> None:
        self._value = value
        self._explode = explode
        self.calls = 0
        self.last_prompt = ""

    def _make(self, *args: object, **kwargs: object) -> _Answer:
        self.calls += 1
        self.last_prompt = str(args[0]) if args else ""
        if self._explode:
            raise RuntimeError("konsola nie obsługuje rysowania")
        return _Answer(self._value)

    select = text = confirm = _make


def test_a_cancelled_prompt_is_not_an_empty_answer() -> None:
    """`questionary` łapie Ctrl+C i zwraca `None`. Bez rozróżnienia pętla pytań o kryteria
    nie miałaby wyjścia: pusta odpowiedź wracałaby do tych samych ośmiu pytań w kółko."""
    prompter = ConsolePrompter(backend=_Backend(None))

    with pytest.raises(CancelledError):
        prompter.text(Question(id="nazwa", text="Fragment nazwy"))
    with pytest.raises(CancelledError):
        prompter.confirm(WZNOWIC, default=True)
    with pytest.raises(CancelledError):
        prompter.ask(CO_DALEJ)


def test_cancellation_is_a_configuration_error_so_the_menu_catches_it() -> None:
    """Kreator wraca do menu na `CeidgError`; przerwanie ma wejść w tę samą ścieżkę."""
    assert issubclass(CancelledError, ConfigError)


def test_a_backend_that_cannot_draw_falls_back_to_plain_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ADR-0008 obiecuje degradację na starej konsoli Windows; awaria pada przy rysowaniu,
    nie przy imporcie, więc sam `try` wokół importu jej nie łapie."""
    backend = _Backend(None, explode=True)
    prompter = ConsolePrompter(backend=backend)
    monkeypatch.setattr("builtins.input", lambda prompt="": "podlaskie")

    answer = prompter.text(Question(id="wojewodztwo", text="Województwo"))

    assert answer == "podlaskie"
    assert backend.calls == 1  # backend wyłączony po pierwszej awarii...
    assert prompter.text(Question(id="miasto", text="Miejscowość")) == "podlaskie"
    assert backend.calls == 1  # ...i nie próbuje ponownie


def test_a_closed_input_ends_the_question_instead_of_answering_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zamknięte wejście to przerwanie, nie zgoda na wartość domyślną."""
    prompter = ConsolePrompter(backend=None)

    def refuse(prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", refuse)

    with pytest.raises(CancelledError):
        prompter.text(Question(id="nazwa", text="Fragment nazwy"))


# ------------------------------------------------------- „tak" znaczy tak, w każdym wejściu


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("tak", True),
        ("t", True),
        ("TAK", True),
        ("  Tak  ", True),
        ("yes", True),
        ("y", True),
        ("nie", False),
        ("n", False),
        ("N", False),
        ("moze", False),
        # Odmowy zaczynające się od „t" — dokładnie te przypadki wywróciło dopasowanie
        # po pierwszej literze, które czytało je jako zgodę i kasowało bazę.
        ("teraz nie", False),
        ("to nie", False),
        ("tylko logi", False),
        ("takze nie", False),
    ],
    ids=lambda value: str(value).replace(" ", "_"),
)
def test_a_polish_answer_is_understood(answer: str, expected: bool) -> None:
    """Operator jest polski i pisze „tak"; nawyk bywa angielski, więc „yes" też ma działać.

    Odpowiedź nierozpoznana znaczy „nie", a nie „powtórz": te potwierdzenia stoją przed
    decyzjami nieodwracalnymi, więc niejasność ma prowadzić w stronę bezpieczną.
    """
    assert is_yes(answer, default=False) is expected


@pytest.mark.parametrize("default", [True, False], ids=["domyslnie_tak", "domyslnie_nie"])
def test_an_empty_answer_takes_the_default(default: bool) -> None:
    """Sam Enter to zgoda na to, co program proponuje — i tylko na to."""
    assert is_yes("", default=default) is default
    assert is_yes("   ", default=default) is default


@pytest.mark.parametrize(
    ("default", "suffix"), [(True, "T/n"), (False, "t/N")], ids=["domyslnie_tak", "domyslnie_nie"]
)
def test_the_hint_shows_which_answer_is_the_default(default: bool, suffix: str) -> None:
    """Wielka litera mówi, co się stanie po samym Enterze — bez czytania dokumentacji."""
    assert confirm_suffix(default) == suffix


def test_the_wizard_confirmation_understands_a_polish_answer() -> None:
    """Kreator pyta tekstem, nie `questionary.confirm` — i to jest sedno poprawki.

    `questionary.confirm` wiąże klawisze `y` i `n` na sztywno, resztę pomija. Operator,
    który wpisywał „tak" przy pytaniu o zapis skoroszytu (domyślnie „nie"), dostawał „nie"
    i nie miał jak się zorientować, dlaczego plik nie powstał.
    """
    prompter = ConsolePrompter(backend=_Backend("tak"))

    assert prompter.confirm(ZAPISZ_EXCEL, default=False) is True


def test_the_wizard_confirmation_still_refuses_on_anything_else() -> None:
    """Kierunek bezpieczny zostaje: odpowiedź nierozpoznana to „nie", nie „powtórz"."""
    assert ConsolePrompter(backend=_Backend("teraz nie")).confirm(ZAPISZ_EXCEL, default=False) is (
        False
    )
    assert ConsolePrompter(backend=_Backend("")).confirm(ZAPISZ_EXCEL, default=False) is False


def test_the_wizard_confirmation_shows_which_answer_enter_picks() -> None:
    """Klamra `[t/N]` jest jedyną informacją o tym, co zrobi sam Enter."""
    backend = _Backend("")
    ConsolePrompter(backend=backend).confirm(ZAPISZ_EXCEL, default=False)

    assert "[t/N]" in backend.last_prompt


class _Sequence:
    """Backend oddający kolejne odpowiedzi — do sprawdzenia dopytania."""

    def __init__(self, *answers: str) -> None:
        self._answers = list(answers)
        self.prompts: list[str] = []

    def text(self, prompt: str, **_: object) -> _Answer:
        self.prompts.append(prompt)
        return _Answer(self._answers.pop(0) if self._answers else "")


def test_an_answer_that_is_neither_yes_nor_no_is_asked_again() -> None:
    """„tak." i „jasne" to zgoda, której w kreatorze nie wolno po cichu zamienić w odmowę.

    Dopytanie jest tylko tutaj. W `cli` te same wątpliwości kończą się odmową od razu,
    bo tamte dwa pytania stoją przed decyzją nieodwracalną.
    """
    backend = _Sequence("tak.", "tak")
    prompter = ConsolePrompter(backend=backend)

    assert prompter.confirm(ZAPISZ_EXCEL, default=False) is True
    assert len(backend.prompts) == 2
    assert "Nie rozumiem" in backend.prompts[1]


def test_the_second_unclear_answer_still_means_no() -> None:
    """Dopytanie jest jedno. Uparcie niejasna odpowiedź prowadzi w stronę bezpieczną."""
    backend = _Sequence("jasne", "no chyba")
    prompter = ConsolePrompter(backend=backend)

    assert prompter.confirm(ZAPISZ_EXCEL, default=False) is False
    assert len(backend.prompts) == 2


@pytest.mark.parametrize("answer", ["tak", "nie", "", "  ", "N"], ids=lambda v: repr(v))
def test_a_clear_answer_is_never_questioned(answer: str) -> None:
    """Dopytanie nie może dotknąć odpowiedzi, która jest zrozumiała — także pustej."""
    backend = _Sequence(answer)
    ConsolePrompter(backend=backend).confirm(ZAPISZ_EXCEL, default=False)

    assert len(backend.prompts) == 1


def test_a_confirm_override_with_a_typo_stops_instead_of_meaning_no() -> None:
    """`ask` odrzuca nieznaną wartość z flagi; `confirm` robi teraz to samo.

    Wcześniej literówka we fladze cicho znaczyła „nie" — czyli dokładnie to, przed czym
    broni walidacja przy pytaniach z listą odpowiedzi.
    """
    prompter = OverridePrompter(DefaultsPrompter(), {"wznowic": "moze"})

    with pytest.raises(ConfigError) as caught:
        prompter.confirm(WZNOWIC, default=True)

    assert "wznowic" in str(caught.value)


CONFIRM_QUESTIONS = [
    (WZNOWIC, True),
    (UZYC_RAPORTU, True),
    (PONOW_KRYTERIA, True),
    (ZAPISZ_EXCEL, False),
    (ZAPISZ_YAML, False),
]


@pytest.mark.parametrize(
    ("question", "expected"), CONFIRM_QUESTIONS, ids=[q.id for q, _ in CONFIRM_QUESTIONS]
)
def test_the_declared_default_matches_the_one_callers_pass(
    question: Question, expected: bool
) -> None:
    """`Question.default` (napis, dla trybu `--tak`) i argument `default` (bool, dla konsoli)
    to dwa zapisy tej samej decyzji. Rozjazd oznaczałby, że harmonogram i kreator odpowiadają
    inaczej na to samo pytanie — cicho, bo żaden z nich nie widzi drugiego."""
    assert (question.default == "tak") is expected
