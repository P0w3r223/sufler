# Propozycja czasu pracy z historii commitów

Jak używać odczytowej części [ADR 0034](../adr/0034-jira-worklog-from-github-commits.md): agent czyta
commity z GitHuba i proponuje godziny w rozbiciu na dni i zgłoszenia Jira.

> **Nic tu nie zapisuje.** Ścieżka zapisu worklogu do Jiry została **usunięta** (2026-07-21).
> Godziny wchodzą do Jiry arkuszem WorklogPRO, który importuje sam pracownik — dzięki temu wpis ma
> prawdziwego autora. Patrz [`worklogi-weekly.md`](worklogi-weekly.md) i ADR 0035.

---

## 1. Co musi być skonfigurowane

Tylko GitHub — zdolność nie dotyka Jiry (klucze zgłoszeń wyłuskujemy regexem z treści commitów).

| Zmienna | Rola |
|---|---|
| `WORKMATE_GITHUB_TOKEN` | PAT do odczytu repo |
| `WORKMATE_GITHUB_OWNER` | właściciel repozytorium |
| `WORKMATE_GITHUB_REPO` | nazwa repozytorium |

**Bramki nie ma i nie będzie.** Narzędzie niczego nie mutuje, a repo bramkuje zapis, nie odczyt
(ADR 0006). Wystarczy skonfigurowany GitHub, żeby agent Teams dostał `propose_worklog`; klient jest
**read-only** i nie zależy od `WORKMATE_GITHUB_ENABLE_WRITE`.

Strojenie estymacji (`WORKMATE_GITHUB_WORKLOG_IDLE_GAP_MINUTES`, `…_RAMP_UP_MINUTES`,
`…_ROUND_MINUTES`, `…_MAX_SESSION_HOURS`, `…_MAX_RANGE_DAYS`, `…_TZ`) opisuje
[`docs/reference/config.md`](../reference/config.md). Absurdalna wartość = twardy błąd startu, nie
cichy clamp.

## 2. Przepływ

Poproś agenta o zestawienie, np. *„pokaż moją pracę z commitów za 13–19 lipca"*. Dostaniesz sesje,
sumy dzienne, sumy per zgłoszenie oraz:

- `confidence` — `high` (≥3 commity, sensowna rozpiętość), `medium`, `low` (jeden commit albo
  estymacja obcięta sufitem sesji),
- `unattributed_hours` — czas z sesji, w których żaden commit nie wspominał klucza Jira,
- `notes` — ostrzeżenia (pusty wynik, praca bez przypisania, ograniczenie gałęzi domyślnej,
  **ucięta historia commitów**),
- `disclaimer` — przypomnienie, że to estymacja.

**Zweryfikuj liczby.** Estymacja z commitów systematycznie zaniża czas przy rzadkim commitowaniu:
jeden commit na koniec dnia da tylko „rozbieg" (domyślnie 30 min), a nie osiem godzin.

Wynik jest materiałem do rozmowy i do ręcznego uzupełnienia arkusza — nie źródłem prawdy o czasie
pracy i nie wejściem do automatycznego importu.

## 3. Znane ograniczenia

| Ograniczenie | Skutek | Obejście |
|---|---|---|
| Tylko gałąź domyślna | Praca na niezmerge'owanych gałęziach niewidoczna | Zmerge'uj albo policz ręcznie |
| Dopasowanie autora po loginie/e-mailu | Inny `git config user.email` = cichy zerowy wynik | Podaj e-mail zamiast loginu w `author` |
| Sufit 500 commitów na zapytanie | Wypadają NAJSTARSZE dni okna, godziny zaniżone | Zawęź zakres dat albo podaj `author` (jest o tym nota w `notes`) |
| Estymacja, nie pomiar | Godziny bywają zaniżone | Traktuj jako punkt wyjścia, nie wynik |

Doba kalendarzowa liczy się w strefie z `WORKMATE_GITHUB_WORKLOG_TZ` (domyślnie `Europe/Warsaw`,
nazwa IANA), więc zmiana czasu nie przesuwa granic dni — dawny stały offset to robił.
