# claude_summary

Narzędzie CLI, które zestawia **co dana osoba robiła każdego dnia**, łącząc dwa źródła:

1. **Historia promptów Claude Code** (za jawną zgodą) — realne, wpisane przez człowieka
   prompty z transkryptów sesji (`~/.claude/projects`).
2. **Historia commitów** wskazanego repozytorium (opcjonalnie — filtrowana po autorze).

Wynik to uporządkowane dane per dzień: **JSON** (stabilny kontrakt do dalszego przetwarzania)
oraz czytelny **Markdown**. Opcjonalnie dokłada krótki opis prozą generowany przez Claude API.

> **Prywatność.** Narzędzie czyta Twoją prywatną historię promptów. Odczyt jest twardo
> bramkowany zgodą (`--consent` / `CLAUDE_SUMMARY_CONSENT=1`). Treść jest **redagowana** przed
> zapisem (patrz niżej), ale wynik i tak trafia domyślnie **poza repo** (`~/.claude-summary`) i
> **nie należy go commitować**.
>
> **Status kopii.** Samodzielny pod-projekt (własny `pyproject.toml`, środowisko `uv`) — wdrażany
> i utrzymywany osobno; ten katalog jest kopią referencyjną kontraktu/kodu, nie źródłem prawdy
> o wersji uruchomionej u konkretnego użytkownika.

## Redakcja treści wrażliwej (zawsze włączona)

Surowa treść promptów bywa poufna (IP serwerów, loginy SSH, klucze/tokeny/hasła, GUID-y, wklejone
logi). Redakcja działa **na granicy parsowania** — surowa treść nie wchodzi do modeli, JSON-a ani
do zapytania LLM — i ma trzy poziomy:

1. **Redakcja w miejscu** — wartości są zastępowane etykietami: `[SEKRET]`, `[KONTO]` (e-mail /
   `user@host`), `[IP]`, `[ID]` (UUID/GUID), `[UŻYTKOWNIK]` (nazwa użytkownika w ścieżce).
2. **Przycięcie wklejek** — prompt wyglądający na wklejony log/zrzut redukujemy do wiodącej
   instrukcji + znacznik `[…wklejona treść pominięta]`.
3. **Pełne pominięcie** — prompt będący samym zrzutem terminala jest ignorowany w całości.

Kategorie, które zadziałały, są widoczne w JSON (pole `redactions`) i jako dyskretny znacznik
`(zredagowano: …)` w Markdown. Szczegóły: `docs/adr/0003`.

## Instalacja

Samodzielny pod-projekt uv (własny `.venv`):

```bash
cd claude_summary
uv sync                 # rdzeń
uv sync --extra agent   # + warstwa LLM (anthropic)
```

## Użycie

```bash
# Ostatnie 7 dni, tylko prompty (bez repo):
uv run claude-summary --consent

# Prompty + commity wskazanego repo, konkretny zakres, JSON i Markdown do pliku:
uv run claude-summary --consent --repo "C:\Users\Ja\projekt" \
    --since 2026-07-17 --until 2026-07-23 --format both

# Z opisem prozą (wymaga ANTHROPIC_API_KEY w .env i extra agent):
uv run claude-summary --consent --repo "C:\Users\Ja\projekt" --llm
```

### Flagi

| Flaga | Znaczenie |
|-------|-----------|
| `--consent` | Zgoda na czytanie historii promptów (wymagana, o ile nie ustawiono env). |
| `--repo ŚCIEŻKA` | Repozytorium git — dokłada commity i zawęża prompty do tego repo. |
| `--author EMAIL` | Filtr autora commitów (domyślnie `git config user.email` repo). |
| `--since` / `--until` | Zakres dat `RRRR-MM-DD` (domyślnie ostatnie 7 dni). |
| `--llm` | Dołóż opis prozą per dzień (Claude API). |
| `--format md\|json\|both` | Format wyjścia (domyślnie `md`). |
| `--out PLIK` | Zapisz wynik do pliku (dla `both` powstają `.md` i `.json`). |
| `--all-projects` | Wszystkie foldery `~/.claude/projects`, bez filtra po repo. |
| `--project NAZWA` | Tylko wskazany folder projektu. |

## Jak odróżniamy realny prompt człowieka

Linia `type:"user"` w transkrypcie bywa tool-resultem, powiadomieniem sub-agenta czy
rozwinięciem slash-komendy. Przepuszczamy wyłącznie wpisy z `promptSource == "typed"`
(lub `suggestion_accepted`/`queued`), `origin.kind == "human"`, tekstową treścią, bez
`toolUseResult` i spoza sub-agenta (`isSidechain`). Znaczniki czasu są w UTC — konwersja na
strefę lokalną następuje przed grupowaniem po dniu. Szczegóły: `docs/adr/0001`.

## Konfiguracja (zmienne `CLAUDE_SUMMARY_*`)

Patrz `.env.example`. Najważniejsze: `CLAUDE_SUMMARY_CONSENT`, `CLAUDE_SUMMARY_TZ`
(domyślnie `Europe/Warsaw`), `CLAUDE_SUMMARY_PROJECTS_DIR`, `ANTHROPIC_API_KEY` (warstwa LLM).

## Rozwój

```bash
uv run pytest
uv run ruff check .
uv run mypy
```
