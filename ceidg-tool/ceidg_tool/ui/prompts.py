"""Pytania do użytkownika za protokołem `Prompter` — jedyny moduł znający `questionary`.

Dzięki wymiennemu pytającemu ten sam przepływ obsługuje kreator (pytania na konsoli),
flagi CLI, tryb `--tak` (decyzje domyślne) i testy (odpowiedzi ze skryptu, bez terminala).
Każde pytanie ma stały identyfikator ASCII, więc testy nie zależą od brzmienia zdania.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..errors import ConfigError
from . import texts


@dataclass(frozen=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True)
class Question:
    """Pytanie z zadeklarowaną odpowiedzią domyślną.

    `safe_default=False` oznacza decyzję, której nie wolno podjąć za użytkownika
    (zgoda na produkcję, start pobrania powyżej progu) — tryb `--tak` ją odrzuca.
    """

    id: str
    text: str
    options: tuple[Option, ...] = ()
    default: str = ""
    safe_default: bool = True
    hint: str = ""

    def __post_init__(self) -> None:
        """Domyślna odpowiedź musi być **jedną z opcji** — inaczej nie da się jej wybrać.

        Pierwsza wersja tego sprawdzenia wymagała, żeby domyślna była opcją *pierwszą*, i to
        było za mocne: `CO_DALEJ` wylicza domyślną z kryteriów, które operator już podał, więc
        raz jest nią „lista", a raz „szczegóły". Kolejność, w jakiej opcje trafiają na ekran,
        porządkuje `_domyslna_na_czele` w chwili zadawania pytania — tam, gdzie znana jest
        wartość domyślna tej konkretnej tury."""
        if self.options and self.default not in self.values():
            raise ValueError(
                f"Pytanie {self.id!r}: domyślna {self.default!r} nie jest żadną z opcji "
                f"({', '.join(self.values())})."
            )

    def values(self) -> tuple[str, ...]:
        return tuple(option.value for option in self.options)

    def check(self, answer: str) -> str:
        if self.options and answer not in self.values():
            raise ConfigError(
                f"Nieznana odpowiedź {answer!r} na pytanie {self.id!r}. "
                f"Dozwolone: {', '.join(self.values())}."
            )
        return answer


# Operator jest polski, więc odpowiada „tak"; nawyk bywa angielski, więc bywa też „yes".
# Dopasowanie jest **dokładne**, nie po pierwszej literze. Przedrostek wydawał się sprytny
# i był groźny: „teraz nie" i „to nie" zaczynają się od „t", więc odmowa czytana przedrostkiem
# kasowała bazę. Zbiór dokładnych odpowiedzi tego nie potrafi.
CONFIRM_YES = frozenset({"t", "tak", "y", "yes"})
CONFIRM_NO = frozenset({"n", "nie", "no"})
CONFIRM_RETRY = 'Nie rozumiem odpowiedzi — wpisz „tak" albo „nie".'


def confirm_suffix(default: bool) -> str:
    """Podpowiedź akceptowanych odpowiedzi; wielka litera to wybór domyślny."""
    return "T/n" if default else "t/N"


def confirm_line(text: str, *, default: bool) -> str:
    """Pytanie razem z podpowiedzią — jedno miejsce, żeby kreator i wiersz poleceń
    pokazywały tę samą klamrę."""
    return f"{text} [{confirm_suffix(default)}]"


def is_yes(answer: str, *, default: bool) -> bool:
    """Pusta odpowiedź to wartość domyślna, poza tym liczy się dokładne brzmienie.

    Odpowiedź nierozpoznana znaczy „nie", a nie „powtórz pytanie": te potwierdzenia stoją
    przed decyzjami nieodwracalnymi (produkcja, skasowanie bazy), więc niejasność ma
    prowadzić w stronę bezpieczną.
    """
    cleaned = answer.strip().lower()
    if not cleaned:
        return default
    return cleaned in CONFIRM_YES


def understood(answer: str) -> bool:
    """Czy odpowiedź w ogóle czyta się jako tak albo nie. Pusta czyta się: znaczy domyślną.

    Rozróżnienie „nie" od „nie rozumiem" ma sens tylko tam, gdzie da się dopytać. `is_yes`
    zawsze rozstrzyga w stronę bezpieczną, więc „tak." albo „jasne" to odmowa — poprawna
    przy decyzji nieodwracalnej, ale przy pytaniu o zapis skoroszytu to cicha utrata zgody.
    """
    cleaned = answer.strip().lower()
    return not cleaned or cleaned in CONFIRM_YES or cleaned in CONFIRM_NO


class Prompter(Protocol):
    """Protokół pytającego. Implementacje: konsola, decyzje domyślne, skrypt testowy."""

    def ask(self, question: Question) -> str: ...

    def confirm(self, question: Question, *, default: bool) -> bool: ...

    def text(self, question: Question) -> str: ...


def _default_or_fail(question: Question) -> str:
    if not question.safe_default:
        raise ConfigError(
            f"{question.text} Tryb nieinteraktywny nie podejmuje tej decyzji za Ciebie — "
            "podaj ją flagą albo uruchom program bez --tak."
        )
    return question.default


class DefaultsPrompter:
    """Tryb `--tak` i harmonogram: każda odpowiedź to zadeklarowana wartość domyślna."""

    def ask(self, question: Question) -> str:
        return question.check(_default_or_fail(question))

    def confirm(self, question: Question, *, default: bool) -> bool:
        if not question.safe_default:
            _default_or_fail(question)
        return default

    def text(self, question: Question) -> str:
        return _default_or_fail(question)


class OverridePrompter:
    """Odpowiedzi narzucone flagą (np. `--partie`), reszta pytań idzie do pytającego niżej."""

    def __init__(self, base: Prompter, overrides: Mapping[str, str]) -> None:
        self._base = base
        self._overrides = dict(overrides)

    def ask(self, question: Question) -> str:
        if question.id in self._overrides:
            return question.check(self._overrides[question.id])
        return self._base.ask(question)

    def confirm(self, question: Question, *, default: bool) -> bool:
        if question.id in self._overrides:
            # Te same słowa co przy pytaniu na konsoli, plus zapis maszynowy z flagi.
            raw = self._overrides[question.id]
            cleaned = raw.strip().lower()
            if cleaned in ("true", "1"):
                return True
            if cleaned in ("false", "0"):
                return False
            if not cleaned or not understood(raw):
                # Tak samo jak `ask`: zła wartość z flagi ma się wywalić na walidacji,
                # a nie wejść do przepływu jako cicha odmowa.
                raise ConfigError(
                    f"Nieznana odpowiedź {raw!r} na pytanie {question.id!r}. "
                    f"Dozwolone: {', '.join(sorted(CONFIRM_YES | CONFIRM_NO))}."
                )
            return is_yes(raw, default=default)
        return self._base.confirm(question, default=default)

    def text(self, question: Question) -> str:
        if question.id in self._overrides:
            return self._overrides[question.id]
        return self._base.text(question)


class ScriptedPrompter:
    """Pytający dla testów: odpowiedzi po identyfikatorze pytania, bez terminala."""

    def __init__(self, answers: Mapping[str, object]) -> None:
        self._answers = dict(answers)
        self.asked: list[str] = []

    def _pop(self, question: Question) -> object:
        self.asked.append(question.id)
        if question.id not in self._answers:
            raise AssertionError(f"Test nie przewidział pytania {question.id!r}: {question.text}")
        value = self._answers[question.id]
        if isinstance(value, list):
            if not value:
                raise AssertionError(f"Skończyły się odpowiedzi na pytanie {question.id!r}.")
            return value.pop(0)
        return value

    def ask(self, question: Question) -> str:
        return question.check(str(self._pop(question)))

    def confirm(self, question: Question, *, default: bool) -> bool:
        """Wyłącznie `True`/`False`.

        Wcześniej było `bool(...)`, więc odpowiedź `"nie"` — napis niepusty, czyli prawdziwy —
        czytała się jako **zgoda**, a test sprawdzający odmowę przechodził, testując akceptację.
        Ta sama pomyłka co przedrostkowe dopasowanie w `is_yes`, tyle że w atrapie, gdzie nie
        wywala programu, tylko cicho unieważnia test."""
        value = self._pop(question)
        if not isinstance(value, bool):
            raise AssertionError(
                f"Odpowiedź na potwierdzenie {question.id!r} musi być True albo False, "
                f"jest {value!r}."
            )
        return value

    def text(self, question: Question) -> str:
        return str(self._pop(question))


class CancelledError(ConfigError):
    """Operator przerwał pytanie (Ctrl+C albo koniec wejścia) — akcja wraca do menu."""


def _domyslna_na_czele(question: Question) -> tuple[Option, ...]:
    """Opcje z domyślną na pierwszym miejscu — bo to pierwsza pozycja wyznacza start kursora.

    Nie podajemy `questionary` parametru `default=` (powód w `_select`), a bez niego kursor
    startuje na pozycji pierwszej. Żeby zaczynał na odpowiedzi domyślnej, to ona musi tam
    stanąć. Porządkowanie siedzi tutaj, a nie w deklaracjach pytań, bo `CO_DALEJ` wylicza
    domyślną z kryteriów podanych wcześniej — deklaracja nie zna jej z góry."""
    domyslna = [o for o in question.options if o.value == question.default]
    reszta = [o for o in question.options if o.value != question.default]
    return (*domyslna, *reszta)


def _select(backend: Any, tekst: str, choices: list[str]) -> Any:
    """Lista wyboru bez `default=` i z jawnym stylem — obie połowy jednej poprawki.

    **Bez `default=`**, bo `questionary` wkłada tę wartość do `selected_options`, a klasa
    `selected` wygrywa przy rysowaniu z `pointed_at`: wiersz domyślny zostawał oznaczony na
    stałe i operator widział dwa zaznaczenia naraz (bramka 3, 2026-09-09). Kursor startuje
    wtedy na pozycji pierwszej — i dlatego `Question` wymaga, żeby domyślna nią była.

    **Z jawnym stylem**, bo domyślny motyw podświetla kolorem, którego ten terminal nie
    pokazywał: strzałka szła w dół, podświetlenie stało. `reverse` nie potrzebuje palety.

    Klasy `selected` w stylu nie ma celowo. Skoro nie podajemy `default=`, nic jej nie
    użyje — a nadanie jej wyglądu przywróciłoby dokładnie ten defekt, gdyby `default=`
    kiedyś wróciło."""
    styl = getattr(backend, "Style", None)
    if styl is None:
        # Atrapa w testach albo starsza biblioteka: brak stylu nie jest powodem, żeby
        # rezygnować z listy — `_run` traktuje wyjątek jako awarię rysowania i wyłącza
        # backend na stałe, co byłoby lekarstwem gorszym od choroby.
        return backend.select(tekst, choices=choices)
    return backend.select(
        tekst,
        choices=choices,
        style=styl([("pointer", "reverse bold"), ("highlighted", "reverse bold")]),
    )


class ConsolePrompter:
    """Pytania na konsoli. `questionary` daje strzałki i podpowiedzi; gdy się nie uruchomi
    ani nie narysuje (stara konsola Windows, przekierowane wejście), schodzimy do `input`.

    `questionary` przechwytuje Ctrl+C i zwraca `None`, więc bez rozróżnienia „przerwano”
    od „pusta odpowiedź” pętla pytań o kryteria nie miałaby wyjścia."""

    def __init__(self, *, backend: Any | None = None) -> None:
        self._backend: Any = backend if backend is not None else _load_questionary()

    def _run(self, build: Callable[[Any], Any]) -> Any:
        """Wywołuje `questionary`; awaria rysowania wyłącza backend na stałe i wraca do `input`."""
        if self._backend is None:
            return _MISSING
        try:
            return build(self._backend).ask()
        except Exception:  # pragma: no cover - zależne od konsoli
            self._backend = None
            return _MISSING

    def ask(self, question: Question) -> str:
        if not question.options:
            return question.check(self.text(question))
        opcje = _domyslna_na_czele(question)
        choices = [f"{o.value} — {o.label}" for o in opcje]
        answer = self._run(lambda backend: _select(backend, question.text, choices))
        if answer is None:
            raise CancelledError("Przerwano wybór.")
        if answer is not _MISSING:
            return question.check(str(answer).split(" — ", 1)[0])
        # Ta sama kolejność co na liście: ścieżka awaryjna nie może pokazywać opcji
        # w innym porządku niż ta, której operator przed chwilą nie mógł narysować.
        labels = ", ".join(f"{o.value} ({o.label})" for o in opcje)
        raw = _input(f"{question.text} [{labels}] (domyślnie {question.default}): ")
        return question.check(raw or question.default)

    def confirm(self, question: Question, *, default: bool) -> bool:
        """Pytanie tak/nie jako zwykły tekst, a nie przez `questionary.confirm`.

        `questionary.confirm` wiąże na sztywno klawisze `y` i `n`, a każdy inny znak po cichu
        pomija. Operator, który na polskie pytanie wpisywał „tak", dostawał odpowiedź domyślną
        i nie miał jak się zorientować: przy „Zapisać wynik do skoroszytu?" (domyślnie „nie")
        znaczyło to, że plik się nie zapisuje mimo wyraźnej zgody. Pytanie tekstowe kosztuje
        jeden Enter więcej i jest jedynym sposobem, żeby wszystkie trzy wejścia rozumiały
        „tak" tak samo.
        """
        line = confirm_line(question.text, default=default)
        answer = self._ask_line(line)
        if not understood(answer):
            # Kreator ma czas dopytać, a operator wpisujący „tak." albo „jasne" wyraża zgodę,
            # której nie wolno po cichu zamienić w odmowę. Pytamy raz; dalej obowiązuje
            # kierunek bezpieczny. W `cli` tego kroku nie ma: tamte dwa pytania stoją przed
            # decyzją nieodwracalną, więc niejasność ma tam kończyć się odmową od razu.
            answer = self._ask_line(f"{CONFIRM_RETRY} {line}")
        return is_yes(answer, default=default)

    def _ask_line(self, line: str) -> str:
        """Jedno pytanie tekstowe: `questionary`, a gdy nie umie rysować — zwykły `input`."""
        answer = self._run(lambda backend: backend.text(f"{line}: "))
        if answer is None:
            raise CancelledError("Przerwano pytanie.")
        if answer is _MISSING:
            return _input(f"{line}: ")
        return str(answer)

    def text(self, question: Question) -> str:
        prompt = question.text if not question.hint else f"{question.text} ({question.hint})"
        if question.default:
            prompt = f"{prompt} [domyślnie: {question.default}]"
        answer = self._run(lambda backend: backend.text(f"{prompt}: ", default=question.default))
        if answer is None:
            raise CancelledError("Przerwano wpisywanie.")
        if answer is not _MISSING:
            return str(answer).strip()
        raw = _input(f"{prompt}: ")
        return raw or question.default


_MISSING = object()
"""Znacznik „backend niedostępny” — odróżniony od `None`, które znaczy „przerwano”."""


def _input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError as exc:  # zamknięte wejście: traktujemy jak przerwanie, nie jak pustą odpowiedź
        raise CancelledError("Koniec wejścia — przerywam.") from exc


def _load_questionary() -> Any | None:
    """Brak biblioteki albo konsola bez obsługi klawiszy nie może zablokować programu."""
    try:
        import questionary
    except Exception:  # pragma: no cover - zależne od konsoli
        return None
    return questionary


def interactive_available(*, stdin_tty: bool, stdout_tty: bool, yes: bool) -> bool:
    """Kreator ma sens tylko przy prawdziwym terminale i bez `--tak` (harmonogram)."""
    return bool(stdin_tty and stdout_tty and not yes)


def make_prompter(*, yes: bool, interactive: bool) -> Prompter:
    if yes or not interactive:
        return DefaultsPrompter()
    return ConsolePrompter()


# ----------------------------------------------------------------------------- katalog pytań

CO_DALEJ = Question(
    id="co_dalej",
    text="Co dalej?",
    options=(
        Option("lista", "sama lista podstawowa"),
        Option("szczegoly", "lista z pełnymi szczegółami"),
        Option("popraw", "popraw kryteria"),
        Option("wyjdz", "wyjdź bez pobierania"),
    ),
    default="lista",
)

UZYC_RAPORTU = Question(
    id="uzyc_raportu",
    text="Użyć gotowego raportu dziennego zamiast tysięcy zapytań?",
    default="tak",
)

WZNOWIC = Question(
    id="wznowic",
    text="W bazie jest niedokończone pobranie z tymi kryteriami. Wznowić je?",
    default="tak",
)

AKTUALIZOWAC = Question(
    id="aktualizowac",
    text="Rozpocząć aktualizację?",
    # Domyślnie „tak": operator wybrał tę pozycję świadomie, a `--tak` w harmonogramie
    # bierze domyślną odpowiedź — inaczej zmiana zatrzymałaby zaplanowane zadania.
    default="tak",
)

PODZIAL = Question(
    id="podzial",
    text="Trafień jest więcej niż próg. Co zrobić?",
    options=(
        Option("partie", "podziel na partie i pobierz po kolei"),
        Option("popraw", "popraw kryteria"),
        Option("wyjdz", "wyjdź"),
    ),
    default="popraw",
    safe_default=False,
)

NIP = Question(id="nip", text="Podaj NIP firmy", hint="10 cyfr, myślniki są dozwolone")

OPIS = Question(
    id="opis",
    text="Opisz jednym zdaniem, czego szukasz",
    # Konkretny przykład zamiast samej zachęty. „Opisz, czego szukasz” nie mówi
    # operatorowi, **jak dużo** wolno napisać — czy jedna branża, czy jedno miasto.
    # Przykład z dwiema branżami i dwoma miastami pokazuje zakres jednym zdaniem, a pochodzi
    # z przebiegu przez prawdziwego asystenta, nie z wyobraźni.
    hint=f"np. „{texts.PRZYKLAD_OPISU}”; Enter = pytania po kolei",
)

# `safe_default=False`, więc `--tak` tego nie podejmie za operatora. Harmonogram nie ma prawa
# działać na interpretacji, której nikt nie przeczytał — jego drogą jest plik YAML, który
# kreator zapisze z wyniku asystenta.
ZATWIERDZ_INTERPRETACJE = Question(
    id="zatwierdz_interpretacje",
    text="Czy tak rozumiem Twoje zapytanie?",
    options=(
        Option("tak", "tak, szukaj"),
        Option("popraw", "popraw opis"),
        Option("pytania", "przejdź do pytań po kolei"),
        Option("wyjdz", "wróć do menu"),
    ),
    default="tak",
    safe_default=False,
)

ROCZNIK_PKD = Question(
    id="rocznik_pkd",
    text="Szukać też po starych kodach PKD?",
    options=(
        Option("waskie", "nie — tylko dokładnie ta branża (mniej firm)"),
        Option("szerokie", "tak — wszystkie firmy, także z inną branżą w komplecie"),
        Option("wyjdz", "wróć do menu"),
    ),
    default="waskie",
    # Bezpieczna domyślna jest **węższa**, bo wybór szerszy zmienia zawartość wyniku o branże,
    # o które nikt nie prosił. Harmonogram bez tej decyzji ma zachować dzisiejsze zachowanie,
    # a nie po cichu zmienić populację po aktualizacji narzędzia (ADR-0012, sub-decyzja 5).
    safe_default=True,
)

MENU = Question(id="menu", text="Wybierz działanie", default="1")

PONOW_KRYTERIA = Question(
    id="ponow_kryteria",
    text="Kryteria wymagają poprawki. Wypełnić je jeszcze raz?",
    default="tak",
)

CEL = Question(
    id="cel",
    text="Cel pobrania",
    hint="trafia do arkusza Metadane, na przykład: analiza rynku dla klienta X",
)

ZAPISZ_EXCEL = Question(
    id="zapisz_excel",
    text="Zapisać wynik do skoroszytu Excel?",
    default="nie",
)

ZAPISZ_YAML = Question(
    id="zapisz_yaml",
    text="Zapisać te kryteria jako plik zapytania YAML (do powtórzeń i harmonogramu)?",
    default="nie",
)


@dataclass
class CriteriaAnswers:
    """Surowe odpowiedzi kreatora, zanim trafią do walidacji `Criteria`."""

    values: dict[str, object] = field(default_factory=dict)

    def set_list(self, name: str, raw: str) -> None:
        items = tuple(part.strip() for part in raw.split(",") if part.strip())
        if items:
            self.values[name] = items

    def set_scalar(self, name: str, raw: str) -> None:
        if raw.strip():
            self.values[name] = raw.strip()


def criteria_questions() -> Sequence[Question]:
    """Pytania o kryteria — kolejność od najczęściej używanych do rzadkich."""
    return (
        Question(id="wojewodztwo", text="Województwo", hint="pusto = bez filtra"),
        Question(id="miasto", text="Miejscowość", hint="można kilka po przecinku"),
        Question(id="pkd", text="Kod PKD", hint="np. 62.10.B"),
        Question(id="status", text="Status", hint="np. AKTYWNY"),
        Question(id="data_od", text="Data rozpoczęcia od", hint="RRRR-MM-DD"),
        Question(id="data_do", text="Data rozpoczęcia do", hint="RRRR-MM-DD"),
        Question(id="nazwa", text="Fragment nazwy firmy"),
        Question(id="max_rekordow", text="Maksymalna liczba rekordów", hint="pusto = bez limitu"),
    )
