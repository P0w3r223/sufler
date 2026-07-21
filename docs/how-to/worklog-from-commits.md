# Ewidencja czasu w Jirze z historii commitów

Jak uruchomić i przetestować zdolność z [ADR 0034](../adr/0034-jira-worklog-from-github-commits.md):
agent czyta commity z GitHuba, proponuje godziny, a po Twoim potwierdzeniu zapisuje wpis czasu
w zgłoszeniu Jira.

> **Przeczytaj najpierw:** Jira **zawsze** zapisuje autorem wpisu konto, którego tokenem lecimy.
> Wpis „w imieniu" innej osoby trafi do raportów czasu jako Twój czas — informacja o właściwej
> osobie żyje wyłącznie w treści wpisu. To ograniczenie API, nie konfiguracji.

---

## 1. Co musi być włączone

Ewidencja stoi na dwóch nogach i obie muszą być skonfigurowane, inaczej drzwi nie wystartują
(świadomy fail-fast — cicha „włączona, ale martwa" bramka to footgun).

| Noga | Zmienne |
|---|---|
| Jira (zapis wpisu) | `WORKMATE_JIRA_TOKEN`, `WORKMATE_JIRA_BASE_URL`, `WORKMATE_JIRA_WRITE_PROJECT`, `WORKMATE_JIRA_SELF_ACCOUNT` (+ `WORKMATE_JIRA_EMAIL` na Cloud) |
| GitHub (źródło commitów) | `WORKMATE_GITHUB_TOKEN`, `WORKMATE_GITHUB_OWNER`, `WORKMATE_GITHUB_REPO` |

Klient GitHub jest **tylko do odczytu** i nie zależy od `WORKMATE_GITHUB_ENABLE_WRITE` — ewidencja
czyta commity, nie pisze do repo.

Minimalny blok w `.env` (środowisko BIAP):

```dotenv
WORKMATE_JIRA_ENABLE_WORKLOG=true
WORKMATE_JIRA_WRITE_PROJECT=WT
# Domyślnie 'self' — jedyna zaimplementowana strategia autorstwa.
WORKMATE_JIRA_WORKLOG_AUTHOR_STRATEGY=self
```

Pełną listę pokręteł (`IDLE_GAP_MINUTES`, `RAMP_UP_MINUTES`, `ROUND_MINUTES`, `MAX_HOURS`,
`MAX_BACKDATE_DAYS`, `MAX_RANGE_DAYS`, `TZ_OFFSET_MINUTES`, `DUPLICATE_GUARD`) opisuje
[`docs/reference/config.md`](../reference/config.md).

## 2. Skąd wziąć `accountId`

Cross-user wymaga `accountId` z Atlassiana (format `712020:4788230b-…`), nie imienia ani e-maila.

```bash
# Twoje własne konto
curl -u "$EMAIL:$API_TOKEN" https://example.atlassian.net/rest/api/3/myself

# Wyszukanie innej osoby (np. Mikołaja)
curl -u "$EMAIL:$API_TOKEN" \
  "https://example.atlassian.net/rest/api/3/user/search?query=mikolaj"
```

W UI: profil użytkownika → `accountId` jest ostatnim segmentem adresu
`https://example.atlassian.net/jira/people/<accountId>`.

## 3. Przepływ — dwa kroki

**Krok 1 — propozycja (nic nie zapisuje).** Poproś agenta o zestawienie, np. *„pokaż moją pracę
z commitów za 13–19 lipca"*. Dostaniesz sesje, sumy dzienne, sumy per zgłoszenie i:

- `confidence` — `high` (≥3 commity, sensowna rozpiętość), `medium`, `low` (jeden commit albo
  estymacja obcięta sufitem),
- `unattributed_hours` — czas z sesji, w których żaden commit nie wspominał klucza Jira,
- `notes` — ostrzeżenia (pusty wynik, praca bez przypisania, ograniczenie gałęzi domyślnej),
- `disclaimer` — przypomnienie, że to estymacja.

**Zweryfikuj liczby.** Estymacja z commitów systematycznie zaniża czas przy rzadkim commitowaniu:
jeden commit na koniec dnia da tylko „rozbieg" (domyślnie 30 min), a nie osiem godzin.

**Krok 2 — zapis.** Podaj wprost zgłoszenie, godziny i dzień: *„zapisz 3 h na WT-12 za 17 lipca,
opis: przegląd kodu"*. Agent nie zapisze niczego na podstawie samej propozycji.

## 4. Test cross-user (Piotr → Mikołaj)

Domyślnie ta ścieżka jest **wyłączona osobną bramką**. Żeby ją przetestować:

```dotenv
WORKMATE_JIRA_WORKLOG_ALLOW_ON_BEHALF=true
```

Poproś o wpis z `on_behalf_of=<accountId Mikołaja>` i `display_name=Mikołaj`. Następnie sprawdź
w UI Jiry, w zakładce **Work log** zgłoszenia:

| Co zobaczysz | Dlaczego |
|---|---|
| Autor wpisu: **Piotr** | Jira przypisuje worklog kontu tokenu i ignoruje pole `author` |
| Treść zaczyna się od `w imieniu: Mikołaj` | Adnotacja wstawiona przez `SelfAuthorStrategy` |
| W odpowiedzi narzędzia pole `note` | Jawne ostrzeżenie o stratnej atrybucji — przekaż je dalej |

Jeśli potrzebna jest **prawdziwa** atrybucja (raporty czasu per osoba), trzeba wdrożyć jedną
z udokumentowanych strategii-slotów: `per_user_token` (token każdego pracownika) albo `tempo`
(Tempo przyjmuje `authorAccountId`). Obie opisuje ADR 0034 § Rejected alternatives.

## 5. Jak cofnąć wpis

**Tylko ręcznie, w UI Jiry** (zgłoszenie → Work log → Delete). Narzędzie jest create-only i nie
edytuje ani nie usuwa wpisów — to byłaby pierwsza nie-create-only mutacja Jiry i wymagałaby
własnego ADR. Dlatego drugi wpis tego samego konta na ten sam dzień w tym samym zgłoszeniu jest
**odrzucany** (strażnik duplikatów, `WORKMATE_JIRA_WORKLOG_DUPLICATE_GUARD`).

## 6. Znane ograniczenia

| Ograniczenie | Skutek | Obejście |
|---|---|---|
| Tylko gałąź domyślna | Praca na niezmerge'owanych gałęziach niewidoczna | Zmerge'uj albo policz ręcznie |
| Dopasowanie autora po loginie/e-mailu | Inny `git config user.email` = cichy zerowy wynik | Podaj e-mail zamiast loginu w `author` |
| Strefa jako stały offset | Zakres przez zmianę czasu przesunie doby o godzinę | Podziel zapytanie wokół zmiany czasu |
| Estymacja, nie pomiar | Godziny bywają zaniżone | Zawsze weryfikuj przed zapisem |
