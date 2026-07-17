## Co i po co

Zwięźle: co zmienia ten PR i dlaczego.

## Powiązania
- Issue / ADR:

## Lista kontrolna
- [ ] `uv run pytest` — zielone
- [ ] `uv run ruff check src tests eval` i `uv run ruff format --check src tests eval` — czyste
- [ ] `uv run mypy` — czyste
- [ ] Reguła zależności zachowana (`core/` nie importuje z `adapters/`)
- [ ] Zmiana mutująca stan ma własny ADR i bramkę per drzwi (jeśli dotyczy — ADR 0006/0021)
- [ ] Sekrety poza repo; brak wartości sekretów w kodzie/testach
- [ ] Dokumentacja zaktualizowana (`README` / `docs/` / `CHANGELOG`), jeśli dotyczy
