# Uruchamia drzwi Teams w trybie anonimowym (tylko loopback) — test bez Azure.
# Wymaga wcześniejszego: uv sync --extra teams.
# Po starcie: podłącz Bot Framework Emulator do http://localhost:3978/api/messages
# (App ID / hasło puste) albo uruchom scripts/teams_smoke.py (round-trip bez GUI).
$ErrorActionPreference = "Stop"
$env:WORKMATE_TEAMS_ANONYMOUS = "true"
uv run workmate-teams
