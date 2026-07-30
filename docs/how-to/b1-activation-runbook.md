# Runbook aktywacji B1 (M3 produkcyjnie) — kroki po nadaniu uprawnień

Date: 2026-07-28
Status: draft
Author: P0w3r223
Related to: [ADR 0009](../adr/0009-meeting-note-flow-and-write-surface.md) (M3), [ADR 0041](../adr/0041-production-m3-meeting-note-write-from-teams-door.md) (Gate-2 zapis z drzwi), `meeting-transcript-live-smoke.md` (live-smoke), `roadmap-v1-gap-analysis.md` (B1)

---

Kod B1 jest **gotowy i bramkowany OFF**. Ten dokument to lista kroków „krok po kroku",
którą wykonuje **operator/admin** dopiero **gdy uprawnienia są już nadane**. Dwie niezależne
bramki: (A) **odczyt** transkryptu, (B) **zapis** notatki z komendy Teams. B wymaga A.

> **STATUS 2026-07-28:** Krok 0 (zgoda admina) WYKONANY — zakresy nadane, device-code przeszło, blok
> `WORKMATE_TEAMS_GRAPH_*` przygotowany w `.env` (flaga OFF). Dokończenie A (live-smoke) ZAPARKOWANE:
> brak realnego joinWebUrl/id spotkania z transkryptem. Kroki niżej zostają jako procedura na później.
>
> **STATUS 2026-07-30 — ODCHYLENIE OD PROCEDURY:** Kroki 4–6 (akceptacja ADR 0041/0042/0043 + WŁĄCZENIE
> bramek `ENABLE_MEETING_TRANSCRIPT`/`_NOTE_WRITE`/`_NOTE_ASYNC` + `_IDENTITIES`) wykonane w
> `deploy/docker/env` **na wprost polecenie zespołu**, BEZ przejścia Kroku 3 (live-smoke transkryptu wciąż
> zaparkowany — patrz wyżej). Runbook wyraźnie zalecał „jeśli krok 3 nie przechodzi — NIE włączaj kroku
> 4"; ta kolejność została świadomie pominięta decyzją zespołu, nie błędem wykonania. Flaga `true` w
> configu floty ≠ zweryfikowane end-to-end na żywo — pierwsze prawdziwe użycie `/notatka` na produkcji
> będzie jednocześnie pierwszym live-smoke.

---

## Krok 0 — warunek wejścia (admin Entra ID)

Nadaj **dwa delegowane** zakresy w tej samej aplikacji (`client_id`) co drzwi Teams,
z **wymaganą zgodą admina**:

| Zakres (delegated) | Po co |
|---|---|
| `OnlineMeetingTranscript.Read.All` | odczyt TREŚCI transkryptu |
| `OnlineMeetings.Read` | rozwiązanie spotkania po `joinWebUrl` |

Gdzie: **Entra admin center → App registrations → (WorkMate) → API permissions →
Add a permission → Microsoft Graph → Delegated** → oba → **Grant admin consent**.

> Jeśli tenant ma restrykcyjną politykę online meetings, admin może potrzebować
> `New-CsApplicationAccessPolicy` (Teams PowerShell) — potwierdzić na pierwszym live-smoke.

---

## Krok 1 — dopisz zakresy do `.env` drzwi Teams

Do istniejącej listy `WORKMATE_TEAMS_GRAPH_SCOPES` **dopisz oba** nowe zakresy
(nie usuwaj dotychczasowych):

```dotenv
WORKMATE_TEAMS_GRAPH_SCOPES=...,OnlineMeetingTranscript.Read.All,OnlineMeetings.Read
```

## Krok 2 — usuń cache tokenu MSAL

Nowy token musi nieść nowe zakresy → wymuś ponowną zgodę device-code:

```powershell
Remove-Item $env:USERPROFILE\.workmate\teams_token_cache.bin -ErrorAction SilentlyContinue
```

## Krok 3 — włącz bramkę ODCZYTU (A) i zweryfikuj CLI

```dotenv
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true
```

Weryfikacja jednym poleceniem (bez zapisu do bazy — idzie do katalogu tymczasowego):

```bash
uv sync --extra teams-graph --extra agent    # jednorazowo
uv run workmate-meeting --source graph \
  --meeting "<joinWebUrl albo id spotkania>" \
  --project scada-integration --date 2026-07-28
```

- Pierwsze uruchomienie: MSAL wypisze kod + `microsoft.com/devicelogin` — zaloguj się raz.
- **Fail-fast:** bramka ON bez zakresów w `SCOPES` → konfiguracja nie wystartuje (czytelny błąd).
- Oczekiwane: `✓ Notatka M3 złożona i zapisana` z id, tytułem i policzonymi polami.

**Jeśli krok 3 nie przechodzi — NIE włączaj kroku 4.** Diagnostyka: tabela w §4
`meeting-transcript-live-smoke.md`.

---

## Krok 4 — akceptacja ADR 0041 (decyzja zaufania Gate-2) [WYKONANE 2026-07-30]

Produkcyjny zapis **wprost z kanału Teams** to decyzja zaufania (zapis z mniej zaufanych
drzwi, odłożony przez ADR 0009 §4). Przed włączeniem bramki ZAPISU:
przejrzyj i **zmień status ADR 0041 z `proposed` na `accepted`**
(`docs/adr/0041-production-m3-meeting-note-write-from-teams-door.md`).

## Krok 5 — włącz bramkę ZAPISU (B) z drzwi Teams + AUTORYZACJĘ nadawcy (B2) [WYKONANE 2026-07-30]

Bramka zapisu wymaga (fail-fast): źródła transkryptu (Krok 3) **oraz** mapy tożsamości
(**B2 / ADR 0042** — bez niej „każdy pisze do wszystkiego"). Zaakceptuj też **ADR 0042**.

```dotenv
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true
# Mapa AAD id -> członek pionu (może być TEN SAM plik co worklogi):
WORKMATE_TEAMS_GRAPH_IDENTITIES=/opt/sufler/identities.yaml
```

**Flota Docker:** bazowy `docker-compose.yml` montuje `data/` RO — bez override `save_note` padnie w
runtime na „Read-only file system". Odpal z narzuconym wąskim montażem RW:
`docker compose -f docker-compose.yml -f docker-compose.notatka.yml --profile bridge up -d`
(`deploy/docker/docker-compose.notatka.yml`, ADR 0041). Sam flip flagi w `env` to NIE wystarcza.

Restart drzwi (`workmate-teams-graph`). Użycie na kanale:

```
/notatka <joinWebUrl|id> | <projekt> | <RRRR-MM-DD>
```

- **Autoryzacja (B2):** nadawca musi być rozpoznanym członkiem pionu (AAD id w mapie); nieznany →
  odmowa **przed** pobraniem transkryptu. Wpis/usunięcie w `identities.yaml` = restart drzwi.
- `projekt`/`data`/`ref` z ARGUMENTÓW komendy (człowiek), **nigdy** z treści transkryptu (ADR 0009 §3).
- Zapis **idempotentny** (B3 / ADR 0043): deterministyczny id z `meeting_ref` — ponowienie tego
  samego spotkania nie tworzy duplikatu (kolizja → „już złożona"). Create-only, ADR 0006.
- Read-only komendy (`/pomoc`, `/szukaj`) i pętla agenta bez zmian — `/notatka` to osobny router.

## Krok 6 (opcjonalny) — async „wrzuć-i-idź" (B3 / ADR 0043) [WYKONANE 2026-07-30]

Domyślnie `/notatka` liczy inline (blokuje poller na czas transkrypt+Claude). Async odsyła **ACK
natychmiast**, liczy w tle i wrzuca wynik do wątku. Zaakceptuj **ADR 0043**, potem:

```dotenv
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_ASYNC=true    # wymaga bramki zapisu (Krok 5)
WORKMATE_TEAMS_GRAPH_MEETING_NOTE_ASYNC_WORKERS=2       # sufit równoległych łańcuchów
```

Idempotencja (wyżej) czyni ewentualne ponowienie po zgubionym zadaniu bezpiecznym.

---

## Rollback

Ustaw bramki (`ENABLE_MEETING_TRANSCRIPT`, `..._NOTE_WRITE`, `..._NOTE_ASYNC`) na `false` i zrestartuj
drzwi — zapis, async i odczyt transkryptu znikają, reszta WorkMate działa bez zmian (wszystkie seams
addytywne, domyślnie None/OFF).

## Poza B (kolejny etap)

Grupa **B (B1+B2+B3) domknięta w kodzie, bramki WŁĄCZONE w configu floty (2026-07-30)** — live-smoke
Kroku 3 nadal zaparkowany (brak realnego spotkania z transkryptem). Zostaje **grupa C — go-live**
(HTTP/IIS, Jira live-smoke, worklog dry-run→bojowy) — praca operatorska; patrz
`docs/roadmap-v1-gap-analysis.md`.
