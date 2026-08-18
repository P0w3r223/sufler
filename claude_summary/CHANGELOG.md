# Changelog

Wszystkie istotne zmiany w projekcie `claude-summary`. Format oparty na
[Keep a Changelog](https://keepachangelog.com/pl/1.1.0/); wersjonowanie
[SemVer](https://semver.org/lang/pl/). Decyzje projektowe: [`docs/adr/`](docs/adr/).

Plik powstał 2026-08-17, gdy pod-projekt dostał pierwszą zmianę zmieniającą jego kontrakt
prywatności — do tej pory żył wyłącznie ADR-ami. Wpisy sprzed tej daty odtwarzają stan `0.1.0`
z decyzji, a nie z zapisu bieżącego.

## [Unreleased]

### Dodane

- **Zgoda jako TYP na granicy odczytu, nie konwencja w orkiestratorze** ([ADR 0004](docs/adr/0004-consent-as-a-type-at-the-read-boundary.md)).
  `core/consent.py` wprowadza `ConsentProof` — obiekt, który powstaje **wyłącznie** w
  `grant_consent()` (konstruktor odmawia bez wewnętrznego znacznika) — i `require_consent()` jako
  strażnika dla wołających bez typów. Każda funkcja czytająca transkrypty w
  `adapters/transcript_files.py` przyjmuje `consent: ConsentProof` jako wymagany argument i sprawdza
  go **zachłannie** (`iter_prompts` zwraca generator, więc kontrola odłożona do pierwszej iteracji
  jest kontrolą, której wołający nieiterujący nigdy nie wykona). Do tej pory bramka stała w
  `app.run`, a adapter czytający `~/.claude/projects` przyjmował wywołanie od każdego — gwarancja
  była własnością MIEJSCA WYWOŁANIA, nie zdolności. **Zakres:** to zabezpieczenie przed POMYŁKĄ,
  nie przed przeciwnikiem — w Pythonie da się obejść konstruktor, ale zwykłe wywołanie odczytu
  z pominięciem bramki nie przechodzi `mypy` i nie rusza.
- **Pusty raport mówi, DLACZEGO jest pusty.** Zero promptów to zwykle błąd konfiguracji, nie brak
  pracy: `scan_prompts` zwraca ostrzeżenia (brak/nieczytelny katalog historii, brak folderów
  projektów, `--project` bez dopasowania, linie `type:"user"` odrzucone przez dyskryminator),
  a CLI wypisuje je na `stderr`.

### Zmienione

- **Redakcja obejmuje też METADANE raportu** (poprawka [ADR 0003](docs/adr/0003-redaction-of-sensitive-content.md)
  z 2026-08-17). Deklaracja „nic wrażliwego nie trafia do wyjścia, kontraktu JSON ani zapytania LLM"
  była wdrożona wyłącznie dla treści promptów i commitów; audyt znalazł trzy pola, które ją omijały:
  `repo` (ścieżka bezwzględna z nazwą użytkownika systemu), `session_id` (pełny UUID wskazujący
  wprost plik transkryptu) oraz `person`/`author` (adres e-mail — w JSON, w Markdown **i** w
  żądaniu do Claude API). Redakcja stoi teraz na **granicy emisji** (`core/render.py`,
  `adapters/anthropic_summarizer`), więc obowiązuje każdego wołającego renderery: `repo` przez
  `redact_text`, `session_id` przycięty do 8 znaków, osoba przez `person_label`
  (`jan.kowalski@firma.pl` → `Jan Kowalski`). `--author` i `git config user.email` pozostają pełnym
  adresem **wewnątrz** procesu — tym filtruje `git log`.
- **IPv6 dołącza do kategorii `ip`** (dotąd wykrywany był wyłącznie IPv4), a wzorzec nazwy
  użytkownika dopasowuje **cały segment ścieżki** do następnego separatora — `C:\Users\Jan
  Kowalski\app` i `/home/jan-kowalski/app` nie wypuszczają już połowy nazwiska.
- **Skanowanie redakcji jest okienkowane** (najwyżej `8 × _MAX_KEEP` znaków wejścia, bo
  `sanitize_prompt` i tak przycina PO redakcji). Wklejka 60 kB w jednym tokenie potrafiła zamrozić
  CLI na dziesiątki sekund na kwadratowym skanowaniu. Okno tnie na **granicy tokenu**, nigdy w jego
  środku.
- **`--out` zdejmuje wyłącznie WŁASNY sufiks** (`.md`/`.json`). `raport.2026-07-17` zostaje nazwą
  pliku — dotąd `with_suffix("")` zjadało datę.
- **Repozytorium z `i18n.commitEncoding` innym niż UTF-8 nie wywraca już biegu** — `git log` czytamy
  z `errors="replace"`. Podmieniony bajt jest gorszy od poprawnego ogonka, ale nie kosztuje raportu.
  Puste `stdout`/`stderr` procesu (zdarza się na Windows) traktujemy jak pustkę zamiast `None`.

## [0.1.0] — 2026-07-23

Pierwsza wersja pod-projektu: dzienne zestawienie pracy z historii promptów Claude Code
i commitów, jako materiał dla agenta zapisującego worklog.

### Dodane

- **Samodzielny pod-projekt uv**, układ heksagonalny: czyste `core/` (modele, dyskryminator,
  grupowanie po dniach, rendering) i `adapters/` po stronie I/O ([ADR 0001](docs/adr/0001-structure-and-prompt-discriminator.md)).
- **Dyskryminator promptów** — linia `type:"user"` to jeszcze nie prompt człowieka; zostaje tylko
  gdy zachodzą WSZYSTKIE warunki `promptSource`/`origin.kind`/braku `toolUseResult`/`isSidechain`
  (ADR 0001).
- **Korelacja repo ↔ transkrypt** oraz jedno miejsce konwersji stref czasowych przed obcięciem do
  daty (ADR 0001).
- **Warstwa opisu prozą per dzień** przez Claude API, opcjonalna (`--llm`,
  [ADR 0002](docs/adr/0002-llm-summary-layer.md)).
- **Redakcja treści wrażliwych na granicy parsowania, bez flagi wyłączającej** — trzy poziomy:
  redakcja w miejscu, przycięcie wklejek, pełne pominięcie ([ADR 0003](docs/adr/0003-redaction-of-sensitive-content.md)).
- **Bramka zgody fail-closed** (`--consent` / `CLAUDE_SUMMARY_CONSENT=1`) — wtedy jeszcze jako
  wczesny return w orkiestratorze; mechanizm zastąpiony w Unreleased (ADR 0004).
