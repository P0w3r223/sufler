# Powiadomienia_teams

Cotygodniowy asystent uzupełniania zmian w **Microsoft Shifts** (Teams). Co niedzielę
o 16:00 wykrywa, kto nie ma zmian na przyszły tydzień, i pisze do niego prywatną wiadomość
1:1 z gotowcem „jak w zeszłym tygodniu"; po odpowiedzi naturalnym językiem bot wpisuje
zmiany do Shifts za pracownika. Pełny zamysł i decyzje: **[PLAN.md](PLAN.md)**.

> Samodzielny pod-projekt (własny `pyproject.toml`, środowisko `uv`). Reużywa wzorców
> uwierzytelniania z drzwi `teams_graph` głównego repo WorkMate.

## Stan prac

- **Etap 0–1 — gotowe:** szkielet projektu, konfiguracja (`config.py`), model domenowy
  (`domain/models.py`), czysta logika: wykrywanie luk (`reminders/detect.py`), propozycja
  „jak ostatnio" (`reminders/propose.py`), harmonogram (`scheduler/weekly.py`), wczytanie
  rosteru z YAML (`roster.py`). Wszystko pokryte testami, bez sieci.
- **Następne (Etap 2+):** warstwa Graph (odczyt/zapis Shifts, czat 1:1), pętla tygodniowa,
  interpretacja odpowiedzi przez Claude, stan i idempotencja.

## Uruchamianie zadań deweloperskich

Z katalogu głównego repo (uv utworzy środowisko podprojektu przy pierwszym uruchomieniu):

```bash
uv run --directory Powiadomienia_teams pytest -q       # testy
uv run --directory Powiadomienia_teams ruff check .    # lint
uv run --directory Powiadomienia_teams mypy            # typy
```

## Konfiguracja

Skopiuj `.env.example` → `.env` i uzupełnij. Zmienne mają prefiks `POWIADOMIENIA_`
(`CLIENT_ID`, `TENANT_ID`, `TEAM_ID`, opcjonalnie `RUN_HOUR`, `TIMEZONE`, `DRY_RUN` …).
Domyślnie `DRY_RUN=true` — nic nie jest wysyłane ani zapisywane, dopóki nie ustawisz `false`.

## Uprawnienia (Microsoft Graph, delegowane, admin consent)

`Schedule.Read.All`, `Schedule.ReadWrite.All`, `Chat.Create`, `Chat.ReadWrite`,
`ChatMessage.Send`, `TeamMember.Read.All`, `User.ReadBasic.All`. Logowanie jako
właściciel/kierownik zespołu (zapis zmian w Shifts jest menedżerski). Szczegóły i status
weryfikacji na żywo: [PLAN.md](PLAN.md).
