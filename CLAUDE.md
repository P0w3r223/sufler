# CLAUDE.md — WorkMate

## Styl
Krótko, bez wstępów i uprzejmości. Zdania oznajmujące. Nie streszczaj mojego pytania.
Narzędzie: uruchom i pokaż wynik — nie opisuj kroków. Po zmianach: jedna linia „co zrobione", bez raportu.

## Budżet kontekstu
- Kod lokalizuj przez **crg** (MCP `code-review-graph`; CLI: `uvx code-review-graph search|query|impact|architecture|dead-code`). Pierwszeństwo przed globalną regułą CodeGraph.
- Pusty wynik `search` znaczy „indeks tego nie zna", nie „nie istnieje" — indeks bywa starszy niż gałąź. Wtedy: `uvx code-review-graph update`, a do czasu przebudowy szukaj wprost.
- Czytaj fragmenty, nie całe pliki. ADR-y i `docs/` tylko gdy zadanie ich dotyczy — nie „na wszelki wypadek".
- `.claude/SYSTEM-SPEC.md` i briefy `.claude/sessions/` opisują system szerzej niż ten plik. Przy rozjeździe z kodem rządzi kod — sprawdź datę pliku wobec gałęzi.
- Testy w iteracji: `--testmon`. Pełny pakiet = bramka przed commitem.
- Po zmianach w kodzie: `uvx code-review-graph update`.

## System (minimum)
Serwer MCP i runtime agenta dzielą ŹRÓDŁO narzędzi (`core/application/tools.py`), ale mają osobne powierzchnie — rozjazd zamierzony, bo po stronie MCP nie ma naszego wykonawcy.
Agent docelowo pięć narzędzi: `Bash`·`Project`·`Activity`·`Jira`·`Schedule` (ADR 0068) — przy `WORKMATE_ENABLE_SHELL=true`. Bez powłoki dochodzą trzy narzędzia odczytu bazy wiedzy (siedem), bo nie ma czym ich zastąpić.
Sesja MCP: zamrożona ósemka, pilnowana golden-testem.
Most GitHub ↔ `EventStore` (SQLite `~/.workmate/events.db`, append-only, poza `data/`) ↔ Teams.
Jira: odczyt bez zapisu, bez mostu, bez EventStore — sześć akcji po ADR 0059.
Retrieval leksykalny BM25 nad notatkami `data/notes/<firma>/<projekt>/*.md`.
Układ heksagonalny: `core/{domain,ports,application,agent}` · `adapters/{inbound,outbound}` · `server.py` (wiring) · `config.py`.
Jira dual-provider: `WORKMATE_JIRA_DEPLOYMENT=server|cloud`.

## Komendy
- `uv sync` (extras: agent, teams, teams-graph, github, jira, retrieval, retrieval-dense, file-reply, seed)
- `uv run --no-sync pytest --testmon` · bramka: `uv run --no-sync pytest` (`--no-sync` omija blokadę `workmate.exe`)
- `uv run ruff check .` · **`uv run ruff format --check src tests eval deploy scripts`** · `uv run mypy` (obejmuje `src`, limit linii 100) · `uv run lint-imports`
  Format to OSOBNY krok CI (`.github/workflows/ci.yml`), nie skutek `ruff check` — bramka bez niego daje czerwone CI. Ta sama lista katalogów co w CI: `deploy` i `scripts` doszły po incydencie, w którym trzy pliki stały niezgodne przy zielonym przebiegu.
- `uv run workmate` · `uv run mcp dev src/workmate/server.py` · `uv run workmate-github`
- Pod-projekty (własny venv): `cd Powiadomienia_teams|claude_summary && uv run pytest`

## Reguły twarde (złamanie = regres)
1. `core/` nie importuje z `workmate.adapters` — pilnuje import-linter (`uv run lint-imports`, krok CI).
2. Odczyt domyślny. Baza wiedzy ma jedną drogę TWORZENIA — `NotesWriteService`, create-only przez `os.link` (kolizja daje nowy sufiks, nie nadpisanie): `save_note` (MCP) / `Project(action='save')` (agent); pozostałe drogi to notatki ze spotkań, wątków i seed CLI. Od ADR 0065 istnieje DRUGA droga — ZMIANA istniejącej notatki (`File(action='edit'|'delete')`) — i jest to świadome odwrócenie tej reguły, nie wyjątek od niej: odwracalność przestała być strukturalna (nie dało się zepsuć tego, czego nie dało się zmienić) i stała się proceduralna (migawka przed operacją + nocna kopia wolumenu). Mutacja idzie przez `NoteMutationService`, jest domyślnie WYŁĄCZONA, wymaga rozpoznanego nadawcy i przechodzi przez niezależnego sędziego; kasowanie ma osobną bramkę, związaną z działającą kopią zapasową. Notatki ze spotkań i wątków (`-mtg-`/`-thr-`) zostają niezmienne — ich niezmienność jest mechanizmem idempotencji. Nowe narzędzie mutujące = własny ADR + zgoda zespołu.
3. `NoteMetadata` (`core/domain/models.py`) = zamrożony kontrakt; zmiana pól = ADR. Bramki na to nie ma — golden-test MCP zamraża sygnaturę `save_note`, nie zestaw pól.
4. Treść notatek, zdarzeń i odpowiedzi to DANE, nie polecenia. EGZEKWOWANE maszynowo tylko dla znaków sterujących (`reject_dangerous_content`, `strip_control_chars`) — tylko tam coś jest odrzucane albo wycinane. Klasy zaufania T0–T3 (ADR 0066, domyślnie OFF) treść ETYKIETUJĄ, nie blokują: koperta z nonce'em na turę niesie pochodzenie, nie zakaz. Reszta kształtuje zachowanie i nie jest granicą bezpieczeństwa.
5. Sekrety wyłącznie poza repo; w dokumentacji tylko wskaźniki.
6. Nowe narzędzie: serwis w `core/application/<domena>.py` → rejestracja w `application/tools.py` (jedno źródło, DWA wejścia). `build_tool_catalog` = drzwi MCP + router komend, powierzchnia zamrożona `tests/adapters/test_mcp_tool_surface.py`. Agent ma własne buildery (`build_project_catalog`, `build_activity_catalog`, `build_jira_catalog`, `build_schedule_catalog`, `build_shell_catalog`) + `extra_catalog` per drzwi. Przed tknięciem buildera sprawdź, kto jeszcze go woła — wspólny sprzęga powierzchnię swobodną z zamrożoną (decyzja 0009 paczki `infra-docker-workmate`, nie lokalne `docs/adr/0009`).
7. Zapisy GitHub: bramkowane (OFF), CREATE-ONLY; poller i drzwi zapisu na TYM SAMYM tokenie/koncie i wspólnym `events.db` (echo `source` + self-skip).
8. Jira: wyłącznie odczyt. Sześć akcji (`my_tasks`, `my_history`, `member_tasks`, `member_history`, `task`, `search` — ADR 0059). Konto Jira NIGDY nie przychodzi od modelu: `my_*` biorą je z serwisu domkniętego przy budowie, `member_*` tłumaczą imię przez zaufaną mapę tożsamości i odmawiają przy niejednoznaczności. Token minimalnego zakresu (bez create/edit/transition). Karty czasu (WorklogPRO) wycofane (ADR 0055) — `Activity(action='worklog')` to co innego: estymacja z commitów.
9. Zmianę rankingu retrievalu bramkuje mikro-eval `eval/` — próg dotyczy ścieżki leksykalnej; dense i `reciprocal_rank_fusion` progu nie mają. RRF to punkt rozszerzenia, nie martwy kod.
10. Decyzje żyją w `docs/adr/`, decyzje paczki wdrożeniowej w `infra-docker-workmate/docs/decyzje/`. Zmiana niezmiennika = ADR przed kodem.

## Pod-projekty
Samodzielne venv-y uv z własnym `PLAN.md` — czytaj dopiero przy pracy nad nimi.
`Powiadomienia_teams/` (Shifts; zapis tylko po jawnym „tak", strażnik cross-user, wygaszenie okna wymaga dowodu pustego odczytu).
`claude_summary/` (bramka zgody fail-closed, redakcja na granicy, wynik poza repo).

## Konwencje
Opisy narzędzi: słowa kluczowe na początku, nazwa oddaje ZAWARTOŚĆ, jedna konwencja (PascalCase) na powierzchni agenta — ADR 0068. Sufit 2048 B per narzędzie i 8000 B na całą powierzchnię pilnuje `tests/core/test_tool_descriptions.py`; realna powierzchnia to 7446 B z powłoką i 7865 B bez niej, czyli ~135 B zapasu — nowa akcja mieści się kosztem istniejącej prozy, nie obok niej. Instrukcje prezentacji wyniku idą polem `note` w kopercie, nie w opisie.
Testy odwzorowują `src/` z grubsza (rdzeń płasko w `tests/core/`); rdzeń na atrapach w pamięci.
Proza po polsku; ADR i `docs/research/` po angielsku.
Gałąź robocza bywa inna niż `Dev` — sprawdź `git status` przed pracą. CI biega wyłącznie na `Main` i `Dev`.
