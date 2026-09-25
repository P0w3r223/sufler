# Skrypty pomocnicze

## Weryfikacja transportu Teams bez Azure

Round-trip drzwi Teams (odbiór „message" → echo „Odebrałem notatkę: …") można
potwierdzić lokalnie, bez Azure i bez GUI Emulatora.

1. **Uruchom bota anonimowo** (tylko loopback):
   ```powershell
   uv sync --extra teams
   ./scripts/run-teams-anon.ps1
   ```
   (równoważnie ręcznie: `SUFLER_TEAMS_ANONYMOUS=true` + `uv run sufler-teams`)

2. **Zweryfikuj round-trip** — mini-emulator: POST aktywności „message" i
   przechwycenie odpowiedzi bota na `serviceUrl`:
   ```powershell
   uv run --no-sync python scripts/teams_smoke.py
   ```
   Sukces: `[WYNIK] ROUND-TRIP OK`.

   Alternatywnie podłącz **Bot Framework Emulator** do
   `http://localhost:3978/api/messages` (App ID / hasło puste).

## Wystawienie po HTTPS (Bot Connector → localhost)

Do testu w realnym Teams (Etap C w how-to) Bot Connector musi dosięgnąć localhost
po HTTPS — tunelem:
```powershell
devtunnel user login
devtunnel host -p 3978 --allow-anonymous
```
Publiczny URL + `/api/messages` wpisz jako *Messaging endpoint* w zasobie Azure Bot.

Pełna procedura Azure/Teams: [`docs/how-to/teams-bot.md`](../docs/how-to/teams-bot.md).
