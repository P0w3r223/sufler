# How-to: produkcyjne M3 — transkrypt spotkania z Graph (live-smoke)

Date: 2026-07-28
Status: draft
Author: P0w3r223
Related to: [ADR 0009](../adr/0009-meeting-note-flow-and-write-surface.md) (M3), `roadmap-v1-gap-analysis.md` (B1)

---

Ten dokument opisuje, jak **nadać uprawnienia** i **zweryfikować** produkcyjny przepływ M3:
pobranie transkryptu prawdziwego spotkania Teams z Microsoft Graph → streszczenie przez Claude →
zapis notatki. Kod (`HttpxGraphTranscriptSource`, bramka, wiring CLI) jest gotowy i przetestowany.

> **STATUS 2026-07-28:** zakresy admina NADANE, device-code przeszło (token niesie nowe zakresy).
> Live-smoke ZAPARKOWANY — brak realnego joinWebUrl/id spotkania z gotowym transkryptem (próba na
> placeholderze zwróciła Graph HTTP 400, co NIE jest dowodem braku uprawnień). §1 poniżej zostaje jako
> zapis procedury nadania; do dokończenia wystarczy realny link spotkania i przestawienie flagi.

## 1. Uprawnienia do nadania (admin Entra ID)

Te same drzwi delegowane co reszta Graph (bot działa jako **zalogowany użytkownik**, device-code
MSAL). Do M3 potrzebne są **dwa NOWE zakresy delegowane** (NADANE 2026-07-28), oba z
**wymaganą zgodą administratora**:

| Zakres (delegated) | Po co | Zgoda admina |
|---|---|---|
| `OnlineMeetingTranscript.Read.All` | Odczyt TREŚCI transkryptu spotkania | **wymagana** |
| `OnlineMeetings.Read` | Rozwiązanie spotkania po `joinWebUrl` (`GET /me/onlineMeetings?$filter=JoinWebUrl eq '…'`) | **wymagana** |

Gdzie: **Microsoft Entra admin center → App registrations → (aplikacja WorkMate, ten sam
`client_id` co drzwi Teams) → API permissions → Add a permission → Microsoft Graph → Delegated
permissions** → zaznacz oba powyższe → **Grant admin consent for <tenant>**.

> Uwaga o zgodności aplikacji (application access policy): odczyt transkryptu spotkania delegowanym
> tokenem działa dla użytkownika będącego **organizatorem/uczestnikiem** spotkania. Jeśli tenant ma
> restrykcyjną politykę dostępu do online meetings, admin może potrzebować `New-CsApplicationAccessPolicy`
> (PowerShell Teams) — do potwierdzenia na pierwszym live-smoke.

## 2. Konfiguracja (operator, po nadaniu zgody)

W lokalnym `.env` drzwi Teams (patrz `.env.example`):

```dotenv
# Dopisz oba nowe zakresy do istniejącej listy SCOPES:
WORKMATE_TEAMS_GRAPH_SCOPES=ChannelMessage.Read.All,ChannelMessage.Send,Team.ReadBasic.All,Channel.ReadBasic.All,User.Read,Files.Read.All,Sites.Read.All,OnlineMeetingTranscript.Read.All,OnlineMeetings.Read
# Włącz bramkę M3 (domyślnie OFF, ADR 0006):
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true
```

Po zmianie zakresów **usuń cache tokenu MSAL**, by wymusić ponowną zgodę device-code
(nowy token musi nieść nowe zakresy):

```powershell
Remove-Item $env:USERPROFILE\.workmate\teams_token_cache.bin -ErrorAction SilentlyContinue
```

Fail-fast: przy `ENABLE_MEETING_TRANSCRIPT=true` bez tych zakresów w `SCOPES` konfiguracja
**nie wystartuje** (czytelny błąd wskazujący brakujący zakres) — zamiast „włączonej, ale martwej"
bramki (403 dopiero przy pobraniu).

## 3. Weryfikacja (jedno polecenie)

```bash
uv sync --extra teams-graph --extra agent   # jednorazowo
uv run workmate-meeting --source graph \
  --meeting "<joinWebUrl spotkania albo id spotkania>" \
  --project scada-integration --date 2026-07-28
```

- `--meeting` przyjmuje **joinWebUrl** (link „Dołącz do spotkania"; zaczyna się od `https://`) albo
  gotowe **id spotkania** (bez rozwiązywania po URL).
- Pierwsze uruchomienie: MSAL wypisze kod i adres `microsoft.com/devicelogin` — zaloguj się raz.
- O `project`/`date` decyduje **wywołujący** (flagi), nie treść transkryptu (ADR 0009 §3).
- Domyślnie zapis idzie do **katalogu tymczasowego** harnessu (nie zaśmieca `data/notes/`); świadomy
  zapis do bazy: `--out data/notes/`.

Oczekiwany wynik: raport `✓ Notatka M3 złożona i zapisana` z id, tytułem i policzonymi polami
strukturalnymi (decyzje/action items/pytania/tagi).

## 4. Diagnostyka

| Objaw | Przyczyna / działanie |
|---|---|
| `wymaga zakresów OnlineMeetingTranscript.Read.All, OnlineMeetings.Read` | Bramka ON, ale zakresów brak w `SCOPES` — dopisz i usuń cache tokenu |
| `spotkanie … nie ma jeszcze transkryptu` | Nagranie/transkrypcja nieprzetworzone — transkrypt pojawia się po zakończeniu i przetworzeniu |
| `nie znaleziono spotkania dla joinWebUrl` | Zły URL albo nie jesteś organizatorem/uczestnikiem — sprawdź link |
| HTTP 403 przy pobraniu | Zgoda admina nienadana lub polityka dostępu do online meetings blokuje — patrz §1 |

## 4a. Produkcyjny wyzwalacz z Teams — komenda `/notatka` (ADR 0041, gated)

Poza operatorskim CLI powyżej, notatkę można złożyć **wprost z kanału Teams** komendą (bramka
ZAPISU, domyślnie OFF — to decyzja zaufania Gate-2, ADR 0041 `proposed`):

```dotenv
# Obie bramki muszą być ON (zapis wymaga źródła transkryptu):
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_TRANSCRIPT=true
WORKMATE_TEAMS_GRAPH_ENABLE_MEETING_NOTE_WRITE=true
```

Użycie na kanale (bot słucha jako zalogowany użytkownik, `workmate-teams-graph`):

```
/notatka <joinWebUrl|id> | <projekt> | <RRRR-MM-DD>
```

- `projekt`, `data` i `ref` bierze się z ARGUMENTÓW komendy (wpisuje człowiek) — **nigdy** z treści
  transkryptu (ADR 0009 §3): transkrypt nie może przekierować notatki do cudzego projektu.
- Zapis idzie do prawdziwej bazy `data/notes/` przez `save_note` — **create-only**, nigdy nie
  nadpisuje (kolizja → sufiks `-2`/`-3`, ADR 0006).
- Read-only komendy (`/pomoc`, `/szukaj`, …) i pętla agenta działają bez zmian — `/notatka` to
  osobny, bramkowany router, nie część read-only dyspozytora (ADR 0017).

## 5. Co dalej (poza B1)

- **B2** — mapowanie tożsamości Entra/AD → model uprawnień rdzenia (wymaga `@architect`).
- **B3** — async „wrzuć-i-idź" + callback wyniku do wątku Teams; podpięcie zapisu wprost do drzwi
  Teams (dziś weryfikacja idzie przez CLI `workmate-meeting`).
