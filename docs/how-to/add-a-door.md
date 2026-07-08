# How-to: dodaj nowe „drzwi" (adapter)

„Drzwi" to nowy kanał dostępu (np. Teams, GitHub) nad **tymi samymi**
narzędziami rdzenia. Ten przewodnik pokazuje zasadę; pełne drzwi Teams to
zakres Fazy 2.

## Zasada

Nowe drzwi = nowy pakiet w `adapters/inbound/<nazwa>/`, który:

1. dostaje gotowe serwisy rdzenia (`NotesService`, `ProjectsService`),
2. tłumaczy zapytania swojego protokołu na wywołania serwisów,
3. odsyła odpowiedź w formacie swojego kanału.

Rdzeń **nie zmienia się** przy dokładaniu drzwi. To cały sens wcześniejszego
dopieszczenia kontraktu narzędzi.

## Szkic (Faza 2, Teams)

```
src/workmate/adapters/
  inbound/
    mcp/          # Faza 1 (jest)
    teams/        # Faza 2 — spike M2 echo (jest); wpięcie rdzenia to szew (patrz niżej)
      responder.py  # SZEW: Responder(Protocol) + EchoResponder (dziś); RuntimeResponder (M1)
      bot.py        # handler wiadomości (SDK-free) + build_agent_app (Agents SDK, leniwie)
      app.py        # osobny proces bota (workmate-teams): aiohttp na /api/messages
  agent/          # NOWE w Fazie 2 (M1): runtime agenta w rdzeniu
    runtime.py    # model w pętli: czyta zapytanie → woła te same narzędzia → składa odpowiedź
```

Szew M1 bez przepisania: `app.py` ma jedną linię `responder = EchoResponder()`.
M1 podmienia ją na `RuntimeResponder(runtime_rdzenia)` implementujący ten sam
`Responder` (strukturalnie) — handler i `bot.py` bez zmian. Profil uprawnień per
drzwi (Teams = mniej zaufane, ADR 0006) wchodzi przez to, z jakim serwisem rdzenia
zbudowany jest `RuntimeResponder`.

Różnica względem MCP: Claude Code sam jest agentem, więc `mcp` tylko wystawia
narzędzia. Teams własnego agenta nie ma — dlatego Faza 2 dokłada **runtime
agenta** (model w pętli) w rdzeniu, a drzwi Teams z niego korzystają.

## Uprawnienia per drzwi

Zgodnie z zasadą przekrojową: mniej zaufane drzwi wymagają mocniejszego
bramkowania. Ten sam rdzeń, ale profil uprawnień zależy od kanału (np. issue
publiczne < uwierzytelniona sesja developera). Decyzje — na Bramce 2.

## Rejestracja w `.mcp.json` vs inne kanały

Drzwi MCP rejestrujemy w [`.mcp.json`](../../.mcp.json). Drzwi Teams/GitHub
działają inaczej (webhook / Bot Framework / Graph) i nie przechodzą przez
`.mcp.json` — to osobny proces uruchamiany obok serwera MCP.
