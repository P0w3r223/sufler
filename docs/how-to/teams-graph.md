# How-to: uruchomić delegowane drzwi Teams (Microsoft Graph)

`workmate-teams-graph` to produkcyjny wariant drzwi Teams: bot działa jako **zalogowany użytkownik**
(delegowany device-code MSAL), odpytując kanał przez Microsoft Graph — bez publicznego endpointu i
bez rejestracji bota. Agent odpowiada read-only nad notatkami, czyta wrzucone załączniki
(multimodalnie) i — po włączeniu bramki — pisze zwrotnie na GitHub. Decyzje:
[ADR 0015](../adr/0015-teams-delegated-graph-polling.md)/[0016](../adr/0016-user-multimodal-attachments.md).
Pełny wykaz zmiennych: [`reference/config.md`](../reference/config.md).

> Wariant przez Bot Framework (Emulator/Azure Bot) opisuje osobny [`teams-bot.md`](teams-bot.md) —
> to inne drzwi (`workmate-teams`).

## Wymagania

- **Aplikacja Entra (public client)** z włączonym device-code flow i **zgodą admina** na zakresy
  Graph: `ChannelMessage.Read.All`, `ChannelMessage.Send`, `Team.ReadBasic.All`,
  `Channel.ReadBasic.All`, `User.Read`, a do plików-załączników `Files.Read.All`, `Sites.Read.All`.
- **Extras**: `uv sync --extra teams-graph --extra agent`.
- **Klucz Claude** (`ANTHROPIC_API_KEY` w `.env`) — runtime agenta bez niego się nie uruchomi.

## Krok 1 — odkryj `team_id` / `channel_id`

Bez `WATCH` proces wypisze dostępne zespoły i kanały z ich ID i zakończy działanie:

```powershell
$env:WORKMATE_TEAMS_GRAPH_CLIENT_ID = "<client_id>"
$env:WORKMATE_TEAMS_GRAPH_TENANT_ID = "<tenant_id>"
Remove-Item Env:WORKMATE_TEAMS_GRAPH_WATCH -ErrorAction SilentlyContinue
uv run workmate-teams-graph
```

Pierwsze uruchomienie: w terminalu pojawi się kod device-code — zaloguj się w przeglądarce jako
konto „głosu bota". Cache tokenu zapisze się w `~/.workmate/teams_token_cache.bin` (**sekret**,
chmod 600); kolejne uruchomienia odświeżają token cicho. Zapisz `team_id` i `channel_id` kanału.

## Krok 2 — nasłuch kanału

```powershell
$env:WORKMATE_TEAMS_GRAPH_WATCH = "<team_id>:<channel_id>"   # pary po przecinku dla wielu kanałów
uv run workmate-teams-graph
```

Agent odpowiada na **każdą** wiadomość na obserwowanym kanale, której autor jest inny niż konto bota
(pomija własne wiadomości — brak pętli). Pamięć rozmowy jest per wątek (`team/channel/root`).

## Krok 3 — załączniki multimodalne

Bez dodatkowej konfiguracji: wrzucony na kanał plik agent materializuje i czyta —

- **Obrazy** PNG/JPEG/GIF/WEBP natywnie, HEIC/HEIF → JPEG, BMP/TIFF → PNG (downscaling do 2048 px).
- **PDF** → blok dokumentu; **DOCX/XLSX/PPTX** → ekstrakcja tekstu; pliki tekstowe → UTF-8.
- Nieobsługiwany typ / przekroczony limit → rzeczowa notka (poller nie pada).

Pobieranie plików z SharePoint wymaga zgody `Files.Read.All`/`Sites.Read.All`; obrazy wklejane inline
(hostedContents) działają bez tych zakresów. Limity: zmienne `WORKMATE_TEAMS_GRAPH_MAX_*`.

## Krok 4 (opcjonalnie) — zapis zwrotny na GitHub

Włącz bramkę zapisu, aby agent mógł zakładać issue/komentarze i odpowiadać w wątkach (Bramka 4):

```powershell
$env:WORKMATE_GITHUB_ENABLE_WRITE = "true"
# wymaga skonfigurowanego repo w .env: WORKMATE_GITHUB_TOKEN/OWNER/REPO
uv run workmate-teams-graph
```

Agent dostanie akcje `GitHub(action='create_issue')` i `GitHub(action='comment')` (create-only;
owner/repo z konfiguracji). W wątku powiązanym z issue/PR `comment` trafia w ten numer BEZ podawania
go przez model (mapa `ThreadLinkStore`) i **wymaga**, by drzwi
`workmate-github` biegły z `ENABLE_CHANNEL_THREADING=true` na wspólnym `events.db` i tej samej parze
team/channel — patrz [`github-bridge.md`](github-bridge.md).

## Powrót do echa (bez klucza / bez API)

Runtime agenta degraduje się do prostego echa jedną linią (`RuntimeResponder` → `EchoResponder`,
patrz `adapters/inbound/responder.py`) — przydatne do testu samego transportu bez kosztów API.
