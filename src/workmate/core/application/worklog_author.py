"""Szew autorstwa wpisu czasu (ADR 0034) — kto FAKTYCZNIE będzie autorem worklogu w Jirze.

Sedno problemu: **Jira Cloud nie pozwala ustawić autora worklogu.** ``POST /issue/{key}/worklog``
przypisuje wpis kontu uwierzytelnionemu tokenem i ignoruje jakiekolwiek pole ``author``. Ta sama
sytuacja jest na Server/DC (tam impersonacja wymagałaby osobnego mechanizmu, którego świadomie
nie wdrażamy — patrz ADR 0034). Wniosek: prośba „zapisz 2 h dla Mikołaja" jest wykonalna tylko
w jednym z trzech wariantów, a różnią się one tym, CZYIM tokenem lecimy.

Ten moduł nazywa te warianty i zamyka je za jednym Protocol-em, żeby serwis nie musiał wiedzieć,
który jest aktywny. Zaimplementowana jest dziś WYŁĄCZNIE ``SelfAuthorStrategy``; pozostałe dwie
to udokumentowane sloty rzucające ``NotImplementedError`` — istnieją, bo kształt rozwiązania jest
znany i nie chcemy, żeby ktoś odkrywał go od zera przy pierwszym zgłoszeniu.

Moduł mieszka w ``application/``, nie w ``domain/``, bo ``PerUserTokenStrategy`` urośnie
o zależność I/O (sejf tokenów), a to już nie jest czysta domena.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Tryby autorstwa — publiczne, bo egzekwuje je też walidacja konfiguracji (``JiraSettings``).
MODE_SELF = "self"
MODE_PER_USER_TOKEN = "per_user_token"
MODE_TEMPO = "tempo"


@dataclass(frozen=True)
class AuthorPlan:
    """Rozstrzygnięcie autorstwa dla JEDNEGO wpisu — co serwis ma faktycznie zrobić.

    ``effective_author`` mówi PRAWDĘ: konto, które zobaczy Jira w kolumnie autora. Gdy różni się
    od ``on_behalf_of``, atrybucja jest STRATNA i wołający musi to zakomunikować użytkownikowi —
    dlatego ``annotated`` jest osobnym, jawnym sygnałem, a nie domysłem z porównania stringów.
    """

    mode: str
    effective_author: str
    on_behalf_of: str = ""
    comment_prefix: str = ""
    annotated: bool = False


class WorklogAuthorStrategy(Protocol):
    """Rozstrzyga autorstwo wpisu czasu; ``plan`` nie robi I/O i nie mutuje niczego."""

    name: str

    def plan(self, *, on_behalf_of: str = "", display_name: str = "") -> AuthorPlan:
        """Zwróć plan autorstwa dla wpisu w imieniu ``on_behalf_of`` (pusty = własny czas)."""
        ...


class SelfAuthorStrategy:
    """Zapis kontem tokenu + ADNOTACJA „w imieniu" w treści wpisu (jedyna działająca dziś).

    UWAGA, to nie jest atrybucja: w Jirze autorem wpisu pozostaje ``self_account``, a informacja
    o osobie, której praca dotyczy, żyje wyłącznie w treści komentarza worklogu. Raporty czasu
    per osoba POKAŻĄ ten czas jako czas właściciela tokenu. Dlatego ścieżka cross-user ma własną
    bramkę konfiguracyjną (``WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF``, domyślnie OFF) — świadoma
    decyzja operatora, a nie efekt uboczny włączenia ewidencji.
    """

    name = MODE_SELF

    def __init__(self, self_account: str) -> None:
        self._self_account = self_account.strip()

    def plan(self, *, on_behalf_of: str = "", display_name: str = "") -> AuthorPlan:
        target = on_behalf_of.strip()
        # Pusty cel albo cel == właściciel tokenu to zwykły wpis własny — bez adnotacji,
        # bo „w imieniu: ja" byłoby szumem w każdym wpisie.
        if not target or target == self._self_account:
            return AuthorPlan(mode=self.name, effective_author=self._self_account)
        label = display_name.strip() or target
        return AuthorPlan(
            mode=self.name,
            effective_author=self._self_account,
            on_behalf_of=target,
            comment_prefix=f"w imieniu: {label}\n\n",
            annotated=True,
        )


class PerUserTokenStrategy:
    """SLOT (niezaimplementowany): zapis WŁASNYM tokenem pracownika — prawdziwa atrybucja.

    Kształt rozwiązania: OAuth 3LO per użytkownik albo sejf tokenów API za nowym portem
    (``WorklogCredentialPort``: ``accountId`` → poświadczenia), plus przepływ zgody i rotacja.
    Serwis wybierałby klienta HTTP per ``on_behalf_of``, więc worklog byłby autorstwa Mikołaja.
    Blokuje to nie kod, tylko nowa klasa sekretów (token na pracownika) — osobny projekt,
    świadomie poza zakresem szkieletu (ADR 0034 § Rejected).
    """

    name = MODE_PER_USER_TOKEN

    def plan(self, *, on_behalf_of: str = "", display_name: str = "") -> AuthorPlan:
        raise NotImplementedError(
            "strategia autorstwa 'per_user_token' jest udokumentowanym SZKIELETEM, jeszcze "
            "niezaimplementowanym — wymaga sejfu tokenów per pracownik (ADR 0034)."
        )


class TempoWorklogStrategy:
    """SLOT (niezaimplementowany): zapis przez Tempo, które JAKO JEDYNE przyjmuje autora.

    Tempo (``POST https://api.tempo.io/4/worklogs``) przyjmuje ``authorAccountId``, więc daje
    prawdziwą atrybucję bez tokenu pracownika. Koszt: licencja Tempo, osobny token, osobna
    baza URL — czyli DRUGI adapter wychodzący, nie metoda w istniejącym kliencie Jiry.
    Poza zakresem szkieletu (ADR 0034 § Rejected).
    """

    name = MODE_TEMPO

    def plan(self, *, on_behalf_of: str = "", display_name: str = "") -> AuthorPlan:
        raise NotImplementedError(
            "strategia autorstwa 'tempo' jest udokumentowanym SZKIELETEM, jeszcze "
            "niezaimplementowanym — wymaga licencji i osobnego adaptera Tempo (ADR 0034)."
        )


def build_author_strategy(name: str, *, self_account: str) -> WorklogAuthorStrategy:
    """Zbuduj strategię po nazwie z konfiguracji; nieznana nazwa → ``ValueError`` (fail-fast).

    Sloty też budujemy — rzucą dopiero przy ``plan``. Konfiguracja odrzuca je jednak wcześniej
    (``JiraSettings.validate``), żeby operator dowiedział się o tym przy starcie, a nie w środku
    rozmowy z użytkownikiem.
    """
    if name == MODE_SELF:
        return SelfAuthorStrategy(self_account)
    if name == MODE_PER_USER_TOKEN:
        return PerUserTokenStrategy()
    if name == MODE_TEMPO:
        return TempoWorklogStrategy()
    raise ValueError(
        f"nieznana strategia autorstwa worklogu: {name!r} "
        f"(dozwolone: {MODE_SELF!r}, {MODE_PER_USER_TOKEN!r}, {MODE_TEMPO!r})."
    )
