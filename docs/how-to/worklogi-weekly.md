# Cotygodniowe karty czasu → arkusz WorklogPRO + wiadomość na Teams

Jak uruchomić drzwi `workmate-worklogi` z [ADR 0035](../adr/0035-weekly-per-person-worklogpro-sheets-and-teams-dm.md):
w piątek program bierze godziny za **tydzień zamknięty** (poprzedni poniedziałek–niedziela),
generuje każdej osobie arkusz importu WorklogPRO i wysyła jej prywatną wiadomość z zestawieniem.

> **Tryb bojowy nie wystartuje**, dopóki nie potwierdzisz schematu importu (§1) i nie ustawisz
> `WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true`. Nagłówki w kodzie to HIPOTEZA z dokumentacji
> producenta, a dopasowanie kolumn idzie po nazwie — jedna literówka unieważnia każdy plik.
> Przebieg PRÓBNY działa bez tego i to on generuje arkusz do porównania.

---

## 1. Bramka: potwierdzenie schematu WorklogPRO

W Jirze: **Apps → WorklogPRO → Import worklogs**

1. Pobierz szablon (jest generowany pod atrybuty Waszej instancji) i porównaj nagłówki z
   `WORKLOGPRO_HEADERS` w `src/workmate/core/domain/timesheet_sheet.py`.
2. Zaimportuj ręcznie 2–3 wiersze — czy przechodzi?
3. Zaimportuj **te same** wiersze drugi raz — czy powstają duplikaty? (Dokumentacja milczy;
   zakładamy, że tak.)
4. **Czy import działa z konta bez uprawnień admina?** Jeśli nie, model „plik per osoba" trzeba
   zamienić na jeden zbiorczy arkusz dla admina — to inna aplikacja.

Rozjazd nagłówków poprawia się w dwóch miejscach: stała `WORKLOGPRO_HEADERS` w module i test
`test_headers_are_not_changed_by_accident` (to DETEKTOR ZMIANY, nie bramka poprawności — nie wie,
jak wyglądają prawdziwe nagłówki, pilnuje tylko, żeby nikt nie zmienił naszych mimochodem).

Gdy wszystkie cztery punkty przejdą, zapisz to w konfiguracji:

```dotenv
WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true
```

Bez tego `WORKMATE_WORKLOGI_DRY_RUN=false` kończy się twardym błędem startu — świadomie, żeby
pierwszy przebieg bojowy nie rozesłał kilkunastu bezużytecznych plików.

## 2. Konfiguracja

```dotenv
WORKMATE_WORKLOGI_ENABLED=true
# Katalog arkuszy — MUSI leżeć poza data/ i poza repo (dane osobowe, nie baza wiedzy).
# Obie kontrole są egzekwowane przy starcie; ścieżka w drzewie roboczym = twardy błąd.
WORKMATE_WORKLOGI_OUTPUT_DIR=D:/worklogi
WORKMATE_WORKLOGI_IDENTITIES=D:/worklogi/identities.yaml
WORKMATE_WORKLOGI_TEAM_ID=9c76036b-...
WORKMATE_WORKLOGI_HOURS_PATH=D:/worklogi/hours.json
# Pilotaż: zacznij od siebie.
WORKMATE_WORKLOGI_ONLY_SOURCE_IDS=EMP-017
```

Tożsamość Graph reużywa `WORKMATE_TEAMS_PUSH_CLIENT_ID`/`_TENANT_ID` i wspólny cache MSAL — jedno
logowanie. Pełna lista pokręteł: [`reference/config.md`](../reference/config.md).

## 3. Mapa tożsamości

Graph da AAD user-id i nazwę, ale **nie konto Jiry** (bywa nim prywatny adres spoza tenanta).
Dlatego mapowanie jest jawne:

```yaml
EMP-042:
  aad_user_id: 712020-...          # z Graph / scripts/lista_czlonkow.py
  jira_user: mikolaj@example.org        # e-mail albo accountId Atlassiana
  git_email: mikolaj@example.org        # opcjonalnie (ADR 0036): most do commitów i claude_summary
  display_name: Mikołaj Anonimowicz # opcjonalnie, nadpisuje nazwę z Graph
```

`git_email` jest OPCJONALNY — używa go dopiero źródło `shifts` (ADR 0036), by przypisać czyjeś
commity danego dnia do kluczy Jira i wpiąć opis z `claude_summary`. Puste = godziny tej osoby
w całości na koszykowe issue. Współdzielony `git_email` u dwóch osób = twardy błąd startu.

**Fail-closed:** nieznany `source_id`, brak `jira_user` albo osoba spoza zespołu = brak pliku i brak
wiadomości, z wpisem w raporcie przebiegu. Nigdy nie zgadujemy po nazwisku — zły `jira_user`
zaimportuje czyjeś godziny na cudze konto Jiry, a tego (ADR 0034) nie da się cofnąć narzędziem.

## 4. Źródło godzin

Wybiera je `WORKMATE_WORKLOGI_HOURS_SOURCE`: `json` (atrapa) albo `shifts` (realne dane, ADR 0036).

### 4a. `json` — atrapa

Prosty plik do testów i pilotażu bez Graph/GitHub:

```json
[
  {"source_id": "EMP-042", "day": "2026-07-15", "issue_key": "WT-12", "hours": 3.5},
  {"source_id": "EMP-017", "day": "2026-07-16", "issue_key": "WT-14", "minutes": 90,
   "comment": "przegląd kodu"}
]
```

### 4b. `shifts` — realne dane (Microsoft Shifts + commity + claude_summary, ADR 0036)

Składa wpis karty czasu z trzech źródeł, bez estymacji ilości godzin:

1. **Godziny** = realne, opublikowane zmiany z **Microsoft Shifts** (Graph, ten sam zespół i
   logowanie co wyżej), pocięte na doby lokalne.
2. **Zgłoszenie** = klucze Jira z **commitów** osoby danego dnia (gałąź domyślna); realne minuty
   dnia dzielone równo między dotknięte klucze, a czas dni bez klucza → **koszykowe issue**.
3. **Komentarz** = opis dnia z **`claude_summary`** (co osoba robiła), ten sam na wszystkich
   wierszach zgłoszeń danego dnia.

```dotenv
WORKMATE_WORKLOGI_HOURS_SOURCE=shifts
# Koszyk na czas dni bez klucza z commitów (kształt PROJ-123).
WORKMATE_WORKLOGI_FALLBACK_ISSUE=BIAP-1
# Katalog zebranych wyników claude_summary (JSON). POZA repo (prywatna treść).
WORKMATE_WORKLOGI_SUMMARY_DIR=D:/worklogi/summaries
```

**Warunki:**
- `identities.yaml` musi mieć `git_email` osoby (§3) — bez niego jej godziny idą w całości na koszyk,
  a komentarze są puste (degradacja, nie błąd).
- **GitHub** (opcjonalny): `WORKMATE_GITHUB_TOKEN`/`_OWNER`/`_REPO`. Bez niego wszystkie godziny na
  koszyk (klucze issue wymagają commitów).
- **claude_summary**: wrzuć do `SUMMARY_DIR` pliki JSON z narzędzia `claude_summary` (pole `person`
  = e-mail git osoby). Na pilotaż **operator umieszcza je ręcznie** — katalog jest INPUTEM ZAUFANYM
  (plik jest przypisywany osobie z pola `person`, więc dbaj, kto do niego pisze). Docelowe,
  uwierzytelnione zbieranie (Teams DM z wiązaniem po nadawcy) projektuje ADR 0037; build odłożony
  do czasu, aż rdzeń pójdzie bojowo.

Wszystkie trzy wejścia degradują niezależnie: brak Shifts/GitHub/claude_summary nie wywraca
przebiegu, tylko zubaża wynik (mniej godzin / koszyk / pusty komentarz).

## 5. Uruchomienie

```bash
uv sync --extra worklogi
uv run workmate-worklogi --login    # jednorazowo (także po dodaniu TeamMember.Read.All)
uv run workmate-worklogi --once     # jeden przebieg
uv run workmate-worklogi            # pętla: czeka na kolejne piątki
```

**Domyślnie tryb PRÓBNY** (`WORKMATE_WORKLOGI_DRY_RUN=true`): arkusze powstają, wiadomości nie
wychodzą, stan się nie zapisuje. Obejrzyj pliki, zanim ustawisz `false`.

Blokada jednej instancji jest automatyczna — dwa procesy wysłałyby ludziom po dwie wiadomości.

## 6. Co dostaje pracownik

Prywatna wiadomość na Teams z tabelą (dzień, zgłoszenie, czas), sumą tygodnia, ścieżką do pliku
i przypomnieniem, że **ponowny import tego samego arkusza zdubluje wpisy**. Czas w tabeli jest
w tej samej notacji co w arkuszu (`2h 30m`), więc oba dokumenty da się porównać wiersz po wierszu,
a suma zgadza się co do minuty. Nazwa pliku niesie identyfikator osoby i etykietę tygodnia
(`worklog_mikolaj-anonimowicz_emp-042_2026-w29.xlsx`) — identyfikator jest tam, bo dwie osoby
o tej samej nazwie nadpisywały sobie arkusze.

## 7. Znane ograniczenia

| Ograniczenie | Skutek | Status |
|---|---|---|
| Brak załącznika w Teams | Ścieżka jako tekst, plik na udziale | Scope `Files.ReadWrite.All` nadany (2026-07-27); czeka na build `TeamsFileSender` (ADR 0026/0027) |
| Ponowny import dubluje wpisy | Człowiek importuje sam | Mitygacja: etykieta tygodnia + ostrzeżenie |
| Schemat niepotwierdzony | Zły nagłówek = plik nie do importu | Bramka §1 |
| Import może być admin-only | Model „plik per osoba" upada | Do sprawdzenia w §1 |
| Raport dotyczy tygodnia zamkniętego | Zestawienie sprzed 3–12 dni | Świadomy wybór: okno bieżące gubiło weekend BEZPOWROTNIE |
| `shifts`: >500 commitów autora/tydzień | Najstarsze dni bez klucza → koszyk | Uczciwa degradacja; praktycznie niemożliwe dla 1 osoby/tydzień (ADR 0036) |
| `shifts`: zbieranie claude_summary | Operator wrzuca pliki ręcznie | Automatyzacja odłożona (ADR 0036 § roadmap S6) |
| `shifts`: tylko commity gałęzi domyślnej | Praca na niezmerge'owanych gałęziach → koszyk | Ograniczenie GitHub API (ADR 0034) |
