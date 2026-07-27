# Worklog na żądanie (self-service) → arkusz WorklogPRO + wiadomość na Teams

Jak uruchomić drzwi `workmate-worklog-selfservice` z [ADR 0038](../adr/0038-selfservice-on-demand-worklog-via-teams-dm.md):
osoba przysyła swój wynik `claude_summary`, drzwi dobierają jej REALNE godziny z Microsoft Shifts,
składają arkusz importu WorklogPRO i (z `--send`) odsyłają jej prywatną wiadomość ze ścieżką pliku.
Import robi CZŁOWIEK (worklog ma prawdziwego autora), jak w [drzwiach wsadowych](worklogi-weekly.md).

> **Etap pilotażu = OPERATOR (ADR 0037).** Live, uwierzytelnione zbieranie z czatu 1:1 (nadawca =
> tożsamość) oraz dostawa pliku ZAŁĄCZNIKIEM (ADR 0026/0027) są jeszcze niezbudowane. Scope admina
> `Files.ReadWrite.All` jest już nadany (2026-07-27) — załącznik nie czeka na zgodę, tylko na build
> `TeamsFileSender`. Do tego czasu operator podaje submisję i osobę ręcznie, a plik dociera ŚCIEŻKĄ
> (fallback), nie załącznikiem.

---

## 1. Osoba: wygeneruj swoją historię (na swojej maszynie)

```bash
claude-summary --consent --repo <ścieżka-repo> --since 2026-07-13 --until 2026-07-19 --format json
```

Powstaje ZREDAGOWANY JSON (`~/.claude-summary/summary_2026-07-13_2026-07-19.json`): pole `person`
= Twój `git config user.email`, `days[]` z commitami i promptami, okno `since`/`until` (oba WŁĄCZNIE).
To jest „przygotowana historia" — przekaż ten plik operatorowi (pilotaż) albo, docelowo, wyślesz go
botowi w czacie 1:1 (M5).

## 2. Konfiguracja (operator)

Drzwi reużywają konfiguracji wsadowych kart czasu ([`worklogi-weekly.md`](worklogi-weekly.md) §2–3):

```dotenv
WORKMATE_WORKLOGI_OUTPUT_DIR=D:/worklogi          # poza data/ i poza repo (dane osobowe)
WORKMATE_WORKLOGI_IDENTITIES=D:/worklogi/identities.yaml
WORKMATE_WORKLOGI_TEAM_ID=9c76036b-...
WORKMATE_WORKLOGI_FALLBACK_ISSUE=BIAP-1            # koszyk na dni bez klucza z commitów
WORKMATE_TEAMS_PUSH_CLIENT_ID=...                 # tożsamość Graph (wspólny cache MSAL)
WORKMATE_TEAMS_PUSH_TENANT_ID=...
# Wysyłka (--send) wymaga potwierdzonych nagłówków (jak w trybie bojowym wsadowym):
WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true
```

**Warunek atrybucji:** osoba MUSI mieć `git_email` w `identities.yaml` — bez niego drzwi odrzucą
submisję (nie da się zweryfikować, że plik należy do nadawcy; ADR 0037 reg. 3). `git_email` musi
zgadzać się z polem `person` w przysłanym JSON-ie, inaczej: odrzucenie („nie przyjmuję cudzego
eksportu").

## 3. Uruchomienie (operator)

```bash
uv sync --extra worklogi
uv run workmate-worklog-selfservice --login                              # raz (device-code)
uv run workmate-worklog-selfservice --submission summary.json --source-id EMP-1   # PRÓBNY
uv run workmate-worklog-selfservice --submission summary.json --source-id EMP-1 --send  # wysyłka
```

**Domyślnie PRÓBNY** (bez `--send`): arkusz powstaje w `OUTPUT_DIR`, wiadomość nie wychodzi. Obejrzyj
plik, zanim wyślesz. Godziny to REALNE minuty opublikowanych zmian Shifts; klucze issue i opisy dni
pochodzą z przysłanego JSON-a (dzień bez klucza w commitach → koszyk `FALLBACK_ISSUE`).

Idempotencja: udana submisja jest zapamiętywana pod kluczem `<source_id>:<tydzień>` (obok pliku
stanu wsadowego) — ponowne `--send` tej samej osoby za ten sam tydzień nic nie wyśle drugi raz.

## 4. Co dostaje osoba

Prywatna wiadomość na Teams z tabelą (dzień, zgłoszenie, czas), sumą tygodnia, ŚCIEŻKĄ do pliku na
udziale i ostrzeżeniem, że **ponowny import tego samego arkusza zdubluje wpisy**. Osoba pobiera plik
z udziału i importuje: **Jira → Apps → WorklogPRO → Import worklogs** (autor = ona sama).

Gdy nadawca nie ma opublikowanych godzin w oknie — zamiast arkusza dostaje podpowiedź, żeby sprawdzić,
czy zmiany w Shifts są opublikowane.

## 5. Znane ograniczenia (ten etap)

| Ograniczenie | Skutek | Status |
|---|---|---|
| Plik dociera ścieżką, nie załącznikiem | Osoba pobiera z udziału | M4: build `TeamsFileSender` (ADR 0026/0027); scope `Files.ReadWrite.All` już nadany (2026-07-27) |
| Operator podaje submisję i osobę | Brak live 1:1 (nadawca = tożsamość) | M5: subskrypcja/`/me/chats` + wiązanie po nadawcy (ADR 0037/0038) |
| Załącznik `.json` od osoby | Na razie plik przekazywany poza Teams | M5: intake załącznika (ADR 0016, scope `Files.Read.All`) |
| Okno bierze się z submisji | Musi zgadzać się strefą z `WORKMATE_WORKLOGI_TZ` | Świadome: `claude_summary` grupuje po Europe/Warsaw |
