# Architektura: jeden rdzeń, wiele drzwi

## Idea

WorkMate ma jeden „mózg" i wiele „drzwi". **Rdzeń** (`core/`) zawiera całą wartość i całą
trudność: modele danych, logikę wyszukiwania i rankingu, syntezę statusu, runtime agenta,
warstwę zdarzeń i notyfikacji. **Drzwi** (`adapters/`) to cienkie adaptery — kanały, którymi
wchodzi zapytanie i wychodzi odpowiedź. Ponieważ kontrakt rdzenia jest stabilny, dołożenie
kolejnych drzwi (Teams, Telegram, GitHub, Jira) przestało być decyzją architektoniczną — jest
dorobieniem adaptera nad **tym samym** katalogiem narzędzi.

```mermaid
flowchart TB
    subgraph IN["adapters/inbound — DRZWI"]
        MCP["mcp<br/>(Claude Code)"]
        TG["teams · teams_graph"]
        TEL["telegram"]
        CLI["cli · workmate-agent"]
        GHD["github · workmate-github"]
        JD["jira · workmate-jira"]
    end
    subgraph CORE["core — RDZEŃ (bez I/O, bez SDK)"]
        SEAM["Responder (wspólny szew)"]
        AGENT["agent/ — runtime + prompt"]
        APP["application/ — przypadki użycia<br/>services · tools · events · github · jira<br/>notifier · ci_autocomment · conversations · workspace"]
        DOM["domain/ — modele + reguły<br/>notes · projects · events · ci · threads<br/>ranking · pricing · sanitize"]
        PORTS["ports/ — interfejsy (Protocol)"]
    end
    subgraph OUT["adapters/outbound — implementacje portów"]
        NOTES[("Markdown notes<br/>+ YAML registry")]
        LLM["anthropic_llm<br/>(Claude API)"]
        ES[("sqlite_events<br/>EventStore")]
        CONV[("sqlite_conversations")]
        GHAPI["github_api"]
        JIRAAPI["jira_api"]
        GRAPH["graph_teams_notifier<br/>(Microsoft Graph)"]
        LEM["simplemma_lemmatizer"]
    end
    IN --> SEAM --> AGENT --> APP
    MCP --> APP
    APP --> PORTS
    DOM -.-> APP
    PORTS --> NOTES & LLM & ES & CONV & GHAPI & JIRAAPI & GRAPH & LEM
```

## Warstwy (heksagonalnie)

- **`core/domain/`** — modele i reguły bez I/O: `Note`, `Project`, `ProjectStatus` i schemat
  notatki (`models.py`), a także `events`, `conversation`, `ci`, `threads`, `workspace`,
  `pricing`, `ranking` (BM25/RRF) i `sanitize`. Zero zależności od frameworków.
- **`core/ports/`** — interfejsy (`Protocol`), których wymaga rdzeń: `repositories`
  (`NotesRepository`, `ProjectsRepository`), `llm`, `conversations`, `events`, `github`, `jira`
  (read + write/tranzycja), `notifications`, `thread_links`, `workspace`, `text`, `meeting`. To
  „gniazda" dla adapterów.
- **`core/application/`** — przypadki użycia zależne wyłącznie od portów: `services`
  (notatki, status, wyszukiwanie), jednoźródłowy katalog narzędzi `tools`, `events`, `github`
  i `jira` (bramkowany zapis + tranzycja), `notifier`, `ci_autocomment`, `conversations`,
  `compaction`, `workspace`, `meeting_notes`.
- **`core/agent/`** — runtime agenta (`runtime.py`) i budowa promptu (`prompt.py`): model Claude
  w pętli, który czyta zapytanie, woła narzędzia i składa odpowiedź.
- **`adapters/inbound/`** — drzwi: `mcp` (serwer MCP), `teams` i `teams_graph` (delegowany
  polling Graph), `telegram`, `cli`, `github` (polling PAT + notifier), `jira` (polling REST v2
  PAT + notifier). Wspólny szew `responder.py` oddziela transport od treści odpowiedzi.
- **`adapters/outbound/`** — implementacje portów: repozytoria Markdown/YAML, `anthropic_llm`
  i `anthropic_summarizer` (Claude API), `github_api`, `jira_api`, `sqlite_events` /
  `sqlite_conversations` / `sqlite_thread_links`, `graph_teams_notifier`, `simplemma_lemmatizer`,
  `filesystem_workspace`.
- **`server.py`** — **punkt składania**: tworzy adaptery, wstrzykuje je do serwisów, podpina
  serwisy do drzwi. `config.py` — typowana konfiguracja ze zmiennych środowiskowych.

## Reguła zależności (najważniejsza konwencja)

**`core/` nigdy nie importuje z `workmate.adapters`.** Zależność jest jednokierunkowa: adaptery
znają rdzeń, rdzeń nie zna adapterów. Dlatego:

- logikę testujemy na atrapach w pamięci, bez dysku, sieci i SDK;
- podmiana magazynu danych (plik → baza, jeden model LLM → inny) nie dotyka logiki;
- nowe drzwi to nowy pakiet w `adapters/`, a nie przebudowa rdzenia.

## Runtime agenta: te same narzędzia, dwie głębokości wejścia

Claude Code **sam jest agentem** — potrzebuje tylko narzędzi, i do tego służy MCP (drzwi `mcp`
wystawiają katalog wprost). Teams, Telegram i CLI własnego agenta nie mają, więc dla nich rdzeń
dostarcza **runtime agenta**: model, który prowadzi rozmowę (z pamięcią i kompaktowaniem historii),
woła te same narzędzia i składa odpowiedź. Kluczowe: **katalog narzędzi jest jednoźródłowy**
(`application/tools.py`) — drzwi MCP i runtime agenta dostają je z tego samego miejsca
([ADR 0008](../adr/0008-agent-runtime-and-tool-catalog.md)). Powierzchnia MCP (4+1) jest zamrożona
i pilnowana golden-testem; narzędzia warstwy roboczej i mostu wchodzą per drzwi przez
`extra_catalog`, więc nie ruszają tej powierzchni.

## Most: GitHub i Jira ↔ EventStore ↔ Teams

Niezależne procesy spotykają się na jednym pliku SQLite (`EventStore`, append-only z
deduplikacją). Nikt nie woła nikogo bezpośrednio:

- **GitHub → EventStore** — drzwi `github` (`workmate-github`) odpytują repo tokenem PAT i mapują
  białą listą pól zdarzenia issue/PR/komentarzy/recenzji/CI ([ADR 0020](../adr/0020-github-delegated-polling-door.md), [ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)).
- **EventStore → Teams** — notifier wypycha zdarzenia na kanał i czat 1:1; wątkowanie dokłada
  zdarzenia jednego issue/PR do wspólnego wątku ([ADR 0022](../adr/0022-proactive-dual-target-teams-push.md)).
- **Teams → GitHub** — agent na kanale, przez bramkowany zapis (create-only), zakłada issue lub
  odpowiada w wątku wprost na powiązanym issue/PR ([ADR 0021](../adr/0021-github-write-capability-gate-4.md), [ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)).

**Jira wchodzi tym samym wzorcem (seria B):** drzwi `jira` (`workmate-jira`) mapują białą listą pól
zdarzenia utworzenia/tranzycji/komentarza ([ADR 0030](../adr/0030-jira-server-read-door.md)) → ten sam
`EventStore` → notifier (`source="jira"`, etykieta `[Jira]`) → Teams; z Teams bramkowany zapis
create-only (Gate 5, [ADR 0031](../adr/0031-jira-write-capability-gate-5.md)) oraz best-effort tranzycja
statusu ([ADR 0032](../adr/0032-jira-status-transition-capability.md)). Wątkowanie kanału jest
współdzielone — resolver kojarzy zdarzenia jednego zgłoszenia po `/browse/{KEY}` (B2, [ADR 0024](../adr/0024-github-pr-ci-review-ingest-and-bidirectional-teams-threads.md)).
Te same filary niezawodności i strażnik pętli (self-skip PAT + echo `source="teams"`) obowiązują.

Niezawodność stoi na czterech filarach: append-only + dedup `(source, external_id, kind)`,
watermark przesuwany dopiero po ingest (at-least-once), kursor konsumenta przesuwany dopiero po
udanej wysyłce, oraz **dwustronny strażnik pętli** (self-skip zdarzeń autorstwa konta PAT;
echo zapisu oznaczone `source="teams"`, którego notifier nie odsyła). Jedyną autonomiczną
ścieżką zapisu jest deterministyczny (nie-LLM) auto-komentarz przy porażce CI.

## Retrieval leksykalny

Wyszukiwanie notatek (`search_notes`) korzysta z rankingu BM25 nad lematami (polska lematyzacja
`simplemma` przez port `Lemmatizer`), z **fallbackiem podłańcuchowym**, gdy brak extra `retrieval`
— import jest leniwy, więc serwer i testy bez tego extra działają bez zmian. Jakość rankingu
pilnuje mikro-eval nad zestawem złotych zapytań ([ADR 0023](../adr/0023-hybrid-local-retrieval.md)).

## Granice zaufania

Źródła prawdy zostają źródłami prawdy: rejestr projektów i notatki to dane, których rdzeń nie
zastępuje, tylko **syntetyzuje** (np. status projektu = część zadeklarowana z rejestru + fakty
policzone z notatek). Treść notatek, zdarzeń GitHub i wiadomości z Teams jest zawsze traktowana
jak **dane, nigdy jak polecenia** (dotyczy też treści zdarzeń Jiry). Zapis idzie wyłącznie przez wąskie, bramkowane narzędzia
(profil uprawnień per drzwi — mniej zaufane drzwi mają mocniejsze bramkowanie), a sekrety żyją
poza rdzeniem, czytane z env/plików spoza `data/`.
