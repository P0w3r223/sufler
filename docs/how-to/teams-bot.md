# How-to: bot Teams (Faza 2, M2 — Teams na runtime agenta)

Ten przewodnik prowadzi od zera do bota w Microsoft Teams. Stos: Microsoft 365
Agents SDK for Python (patrz
[`docs/research/teams-bot-setup-2026.md`](../research/teams-bot-setup-2026.md)).

Kolejność jest celowa: **najpierw test lokalny bez Azure** (Etap A — kilka minut),
potem Azure + Teams (Etapy B–F). Każdy etap ma punkt „gotowe, gdy".

> **Aktualny stan (M2 code-complete):** drzwi Teams domyślnie odpowiadają **runtime
> agenta** rdzenia (katalog READ-ONLY, ADR 0006) — nie samym echem. Etapy B–F
> (Azure/tunel/manifest) są identyczne; zmienia się tylko treść odpowiedzi: zamiast
> „Odebrałem notatkę: …" bot zwraca odpowiedź agenta opartą o notatki. Runtime wymaga
> `uv sync --extra teams --extra agent` + `ANTHROPIC_API_KEY` (brak → twardy błąd
> startu). Aby izolować **sam transport**, w `app.py` można chwilowo wrócić na
> `EchoResponder()` (jedna linia) — wtedy obowiązują dosłowne „echo" punkty niżej.

## Wymagania

- Python 3.10+ i `uv`; instalacja drzwi Teams: **`uv sync --extra teams`**
  (serwer MCP zostaje lekki bez tego extra).
- Do Etapów B–F: subskrypcja Azure (prawo tworzenia App Registration + Azure Bot)
  oraz — do sideloadu — włączony „custom app upload" w Teams (uprawnienie admina;
  patrz Etap E, jeśli zablokowane).

---

## Etap A — test lokalny bez Azure (Bot Framework Emulator)

Najszybszy dowód, że kod działa. Nie potrzeba Azure ani internetu.

1. Zainstaluj drzwi Teams:
   ```powershell
   uv sync --extra teams
   ```
2. Uruchom bota w trybie anonimowym (tylko loopback — bota bez auth nie wolno
   wystawić na sieć; kod tego pilnuje):
   ```powershell
   $env:WORKMATE_TEAMS_ANONYMOUS = "true"
   uv run workmate-teams
   ```
   W logu zobaczysz `Drzwi Teams nasłuchują na http://localhost:3978/api/messages (anonymous=True)`.
3. Pobierz i uruchom **Bot Framework Emulator** (repo zarchiwizowane, ale działa).
   → **Open Bot** → adres `http://localhost:3978/api/messages`, pola **Microsoft
   App ID** i **App Password** zostaw **puste** → Connect.
4. Wpisz w Emulatorze np. `notatka ze spotkania z mpwik`.

**Gotowe, gdy:** bot odpisuje `Odebrałem notatkę: notatka ze spotkania z mpwik`.

> To samo potwierdza automatyczny smoke-test w repo (POST aktywności „message" →
> echo na serviceUrl), więc jeśli tu coś nie gra, to konfiguracja Emulatora, nie kod.

---

## Etap B — Azure: tożsamość bota + zasób Azure Bot

Emulator nie obsługuje single-tenant — auth zweryfikujesz dopiero w Teams. Tu
tworzymy tożsamość i zasób.

1. **App Registration (single-tenant).** W portalu Azure → *Microsoft Entra ID* →
   *App registrations* → *New registration*: nazwa np. `workmate-teams`, typ konta
   **„Accounts in this organizational directory only" (single-tenant)** → Register.
   Zapisz **Application (client) ID** i **Directory (tenant) ID**.
   *(Multi-tenant jest wycofany dla nowych botów — używamy single-tenant.)*
2. **Client secret.** W tej App Registration → *Certificates & secrets* → *New
   client secret* → skopiuj **Value** od razu (potem nie zobaczysz). To
   `WORKMATE_TEAMS_APP_PASSWORD`.
3. **Zasób Azure Bot.** *Create a resource* → szukaj „Azure Bot" → Create:
   - Bot handle: np. `workmate-bot`; warstwa cenowa **F0 (darmowa)**.
   - *Type of App*: **Single Tenant**; wskaż istniejące **App ID** z kroku 1.
4. **Messaging endpoint** (na razie placeholder — uzupełnisz w Etapie C): w zasobie
   Azure Bot → *Configuration* → *Messaging endpoint*.
5. **Kanał Teams.** Zasób Azure Bot → *Channels* → **Microsoft Teams** → zaakceptuj
   warunki.

**Gotowe, gdy:** masz App ID, Tenant ID, client secret oraz zasób Azure Bot z
włączonym kanałem Teams.

---

## Etap C — wystaw localhost tunelem (Microsoft Dev Tunnels)

Bot Connector musi dosięgnąć Twojego localhost po HTTPS.

```powershell
# jednorazowo: logowanie do dev tunnels
devtunnel user login
# host tunelu na porcie bota
devtunnel host -p 3978 --allow-anonymous
```

Skopiuj publiczny URL (postać `https://<id>.devtunnels.ms:3978`) i wpisz w Azure
Bot → *Configuration* → **Messaging endpoint** jako:
`https://<id>.devtunnels.ms:3978/api/messages`  (ścieżka `/api/messages` jest wymagana).

> Przy restarcie tunelu URL może się zmienić — wtedy zaktualizuj endpoint w Azure.

**Gotowe, gdy:** endpoint w Azure wskazuje na URL tunelu z `/api/messages`.

---

## Etap D — uruchom bota w trybie uwierzytelnionym

Zatrzymaj instancję anonimową z Etapu A. Ustaw tożsamość single-tenant i wystartuj:

```powershell
$env:WORKMATE_TEAMS_ANONYMOUS  = "false"
$env:WORKMATE_TEAMS_APP_ID     = "<Application (client) ID>"
$env:WORKMATE_TEAMS_APP_PASSWORD = "<client secret Value>"
$env:WORKMATE_TEAMS_TENANT_ID  = "<Directory (tenant) ID>"
uv run workmate-teams
```

Brak któregokolwiek z trzech pól → proces świadomie NIE wystartuje (twardy błąd),
bo pominięty `TENANT_ID` przy single-tenant to klasyczna przyczyna 401 w Teams.

**Gotowe, gdy:** proces nasłuchuje z `anonymous=False`, a tunel z Etapu C żyje.

---

## Etap E — manifest aplikacji Teams + sideload

1. **Manifest.** Utwórz `manifest.json` (kluczowe: `bots[].botId` = **App ID** z
   Azure — najczęstsza przyczyna „instaluje się, ale milczy"):
   ```json
   {
     "$schema": "https://developer.microsoft.com/json-schemas/teams/v1.19/MicrosoftTeams.schema.json",
     "manifestVersion": "1.19",
     "version": "1.0.0",
     "id": "<Application (client) ID>",
     "developer": {
       "name": "BIAP", "websiteUrl": "https://example.com",
       "privacyUrl": "https://example.com/privacy", "termsOfUseUrl": "https://example.com/tos"
     },
     "name": { "short": "WorkMate", "full": "WorkMate (spike)" },
     "description": { "short": "Bot echo WorkMate", "full": "Spike M2: potwierdzenie odbioru notatki." },
     "icons": { "color": "color.png", "outline": "outline.png" },
     "accentColor": "#2A6FF3",
     "bots": [ { "botId": "<Application (client) ID>", "scopes": ["personal", "team"] } ],
     "permissions": ["identity", "messageTeamMembers"],
     "validDomains": []
   }
   ```
2. **Ikony.** Dodaj `color.png` (192×192) i `outline.png` (32×32, przezroczyste).
3. **Pakiet.** Spakuj **do ZIP** trzy pliki: `manifest.json`, `color.png`,
   `outline.png` (pliki w korzeniu ZIP, nie w podfolderze). Alternatywa: zbuduj i
   zwaliduj pakiet w **Teams Developer Portal** (dev.teams.microsoft.com).
4. **Sideload.** W Teams: *Apps* → *Manage your apps* → *Upload an app* → **Upload
   a custom app** → wybierz ZIP → Add.

> **Jeśli „Upload a custom app" jest wyszarzone** — Twój tenant ma wyłączony custom
> app upload (uprawnienie admina, Twój znak zapytania z ustaleń). Opcje:
> (a) admin włącza w *Teams admin center → Teams apps → Setup policies → Global →
> „Upload custom apps" = On* (propagacja do 24 h); (b) użyj darmowego tenanta
> **Microsoft 365 Developer Program** (custom upload domyślnie włączony).

**Gotowe, gdy:** aplikacja WorkMate instaluje się w Teams bez błędu manifestu.

---

## Etap F — test w Teams

Otwórz czat z botem (lub dodaj do zespołu i użyj `@WorkMate`), napisz dowolną
wiadomość.

**Gotowe, gdy:** bot odpisuje `Odebrałem notatkę: <Twój tekst>` w wątku. To finish
line spike'u M2.

---

## Rozwiązywanie problemów

| Objaw | Najczęstsza przyczyna | Co zrobić |
|-------|----------------------|-----------|
| **401** przy odpowiedzi (endpoint dostaje request, bot milczy) | Niedopasowanie tożsamości | Sprawdź `WORKMATE_TEAMS_TENANT_ID` (single-tenant bez niego → 401); zregeneruj client secret, jeśli ma znaki specjalne; upewnij się, że secret jest aktualny. |
| **Instaluje się, ale milczy** | `botId` ≠ App ID; brak `/api/messages`; tunel padł | Zrównaj `botId` w manifeście z App ID; sprawdź messaging endpoint; sprawdź, czy `devtunnel` żyje i URL się nie zmienił. |
| **Endpoint nie dostaje requestów** | Kanał Teams niewłączony; `http` zamiast `https` | Włącz kanał Teams (Etap B.5); użyj URL tunelu (HTTPS). |
| **„Upload a custom app" wyszarzone** | Sideload wyłączony w tenancie | Admin włącza custom app upload albo użyj tenanta M365 Developer Program (Etap E). |
| **Proces nie startuje, „brakuje WORKMATE_TEAMS_…"** | Tryb uwierzytelniony bez pełnej tożsamości | Uzupełnij App ID + secret + Tenant ID, albo do testu lokalnego ustaw `WORKMATE_TEAMS_ANONYMOUS=true`. |

---

## Co dalej (po M2)

Wpięcie rdzenia jest **zrobione**:
[`app.py`](../../src/workmate/adapters/inbound/teams/app.py) buduje runtime agenta
na katalogu READ-ONLY (`build_agent_runtime(..., enable_write=False)`) i podaje go
jako `RuntimeResponder` — handler i `bot.py` bez zmian. Profil uprawnień per drzwi
(Teams = mniej zaufane, ADR 0006) wchodzi przez `enable_write=False`. Powrót do echa
to nadal jedna linia (`EchoResponder()`).

Dalej (Faza 2, M3–M4): przepływ „nowa notatka" przez Microsoft Graph (transkrypt →
streszczenie wg zamrożonego schematu → zapis przez bramkowany `save_note`) oraz
twarde tematy async (zadania w tle, tożsamość Entra/AD). Włączenie zapisu z Teams
(`enable_write=True`) to decyzja Bramki 2 (ADR 0006) — nie domyślnie.
