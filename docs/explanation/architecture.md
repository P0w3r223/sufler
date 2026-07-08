# Architektura: jeden rdzeń, wiele drzwi

## Idea

WorkMate ma jeden „mózg" i wiele „drzwi". **Rdzeń** (`core/`) zawiera całą
wartość i całą trudność: modele danych, logikę wyszukiwania, syntezę statusu.
**Drzwi** (`adapters/`) to tanie adaptery — kanały, którymi wchodzi zapytanie
i wychodzi odpowiedź. Jeśli kontrakt rdzenia jest dobry, dołożenie kolejnych
drzwi (Teams, GitHub) przestaje być decyzją architektoniczną, a staje się
dorobieniem adaptera.

```
Claude Code        Teams @WorkMate      GitHub issues
  (Faza 1)           (Faza 2)             (Faza 3)
      │                  │                    │
      ▼                  ▼                    ▼
        adapters/inbound/*  ── DRZWI (wejście) ──
                       │
                       ▼
        core/  ── RDZEŃ: domena · porty · przypadki użycia
                       │
                       ▼
        adapters/outbound/*  ── implementacje portów (dane)
```

## Warstwy (heksagonalnie)

- `core/domain/` — modele (`Note`, `Project`, `ProjectStatus`) i schemat notatki.
  Bez I/O, bez zależności od frameworków.
- `core/ports/` — interfejsy (`Protocol`), których wymaga rdzeń (`NotesRepository`,
  `ProjectsRepository`). To „gniazda", w które wpina się adaptery.
- `core/application/` — przypadki użycia (cztery narzędzia Fazy 1) zależne
  wyłącznie od portów.
- `adapters/inbound/` — drzwi wejściowe (MCP teraz).
- `adapters/outbound/` — implementacje portów (Markdown, YAML).
- `server.py` — **punkt składania**: tworzy adaptery, wstrzykuje je do serwisów,
  podpina serwisy do drzwi.

## Reguła zależności (najważniejsza konwencja)

**`core/` nigdy nie importuje z `workmate.adapters`.** Zależność jest
jednokierunkowa: adaptery znają rdzeń, rdzeń nie zna adapterów. Dlatego:

- logikę testujemy na atrapach w pamięci, bez dysku i bez MCP;
- podmiana magazynu danych (np. plik → baza) nie dotyka logiki;
- dołożenie drzwi Fazy 2 to nowy pakiet w `adapters/`, a nie przebudowa rdzenia.

## Dlaczego Faza 1 jest tańsza niż Faza 2

Claude Code **sam jest agentem** — potrzebuje tylko narzędzi, a do tego służy
MCP. Teams i GitHub własnego agenta nie mają, więc dla nich to rdzeń musi
dostarczyć *runtime agenta* (model, który czyta zapytanie, woła te same
narzędzia i składa odpowiedź). Te same narzędzia pod spodem, dwie różne
głębokości wejścia — stąd `adapters/teams/` czeka na Fazę 2 jako stub.

## Granice zaufania

Źródła prawdy zostają źródłami prawdy: rejestr projektów i notatki to dane,
których rdzeń nie zastępuje, tylko **syntetyzuje** (np. status projektu = część
zadeklarowana z rejestru + fakty policzone z notatek). Treść notatek jest zawsze
traktowana jak dane, nigdy jak polecenia.
