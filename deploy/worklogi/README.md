# Preflight kart czasu (C3 — worklog go-live, ADR 0035)

Narzędzie operatora do bezpiecznego dojścia do trybu bojowego drzwi `workmate-worklogi`.
Pokrywa to, co da się zweryfikować BEZ Graph/Teams i bez wysyłki; reszta (Krok 0 w UI Jiry,
przebieg próbny, pilotaż) pozostaje krokami człowieka — patrz
[`docs/how-to/worklogi-weekly.md`](../../docs/how-to/worklogi-weekly.md) i poz. 17
[`docs/how-to/live-smoke-checklist.md`](../../docs/how-to/live-smoke-checklist.md).

```bash
uv run --no-sync python deploy/worklogi/preflight.py
# z zapisem przykładowego arkusza do porównania z szablonem WorklogPRO:
uv run --no-sync python deploy/worklogi/preflight.py --sample D:/worklogi/przyklad.xlsx
```

Co robi (READ-ONLY, exit 1 przy braku spójności):

1. **Raport konfiguracji** — stan obu bramek (`dry_run`, `headers_confirmed`), źródło godzin,
   ścieżki, `only_source_ids`.
2. **Walidacja startu** — uruchamia realne `WorklogiSettings.validate` wobec `.env`
   (przy `enabled=true`: ścieżki poza repo/danymi, mapa tożsamości, źródło godzin).
3. **Dowód bramki nagłówków** — na żywo potwierdza, że `DRY_RUN=false` bez
   `HEADERS_CONFIRMED=true` jest blokowane (strażnik z `config.py`).
4. **Przykładowy arkusz WorklogPRO** — generuje go z czystego rdzenia (bez sieci): nagłówki
   i wiersze na stderr, opcjonalnie `.xlsx` przez `--sample`. To artefakt do **Kroku 0**.

## Czego preflight NIE zastępuje (kroki operatora)

- **Krok 0 (bramka schematu)** — Apps → WorklogPRO → Import worklogs: pobierz szablon
  instancji, porównaj nagłówki z przykładowym arkuszem, zaimportuj 2–3 wiersze (i te same
  drugi raz — duplikaty?), sprawdź import z konta bez admina. Dopiero potem
  `WORKMATE_WORKLOGI_HEADERS_CONFIRMED=true`.
- **Przebieg próbny** — `WORKMATE_WORKLOGI_ENABLED=true`, `DRY_RUN=true`,
  `ONLY_SOURCE_IDS`=Ty, `uv run workmate-worklogi --login` (raz) → `--once`. Dotyka Graph
  (`fetch_team_members`), więc nie da się go zrobić z tego narzędzia.
- **Pilotaż bojowy** — `DRY_RUN=false`, `HEADERS_CONFIRMED=true`, `ONLY_SOURCE_IDS`=Ty (+ 1 osoba).
  Wysyła prywatne wiadomości — świadoma decyzja operatora.
