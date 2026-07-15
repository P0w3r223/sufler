# How-to: checklist smoke-testów na żywo (klucz / Azure / 2. konto)

Ta lista zbiera w jednym miejscu weryfikacje, których **pakiet `pytest` świadomie nie
wykonuje** — cała automatyka działa na atrapach/mockach, bez sieci. Realne zachowanie
(faktyczne wywołania Claude API, Microsoft Graph, przebieg kosztów) potwierdza się
osobnym „smoke na kluczu / na żywym koncie". Poniższe pozycje pochodzą z ADR-ów i briefów
sesji, w których zapisano „live-smoke do zrobienia".

Legenda warunku: 🔑 wymaga `ANTHROPIC_API_KEY` · 👥 wymaga 2. konta w kanale Teams ·
☁️ wymaga infrastruktury (Azure/M365 lub serwer Windows/IIS) · 🐙 wymaga PAT GitHub + repo.

---

## 1. Realne `usage` / koszty — 🔑 (ADR 0013)

- **Krok:** zadaj agentowi realne pytanie przez harness CLI:
  `uv run workmate-agent "Jaki jest status projektu scada-integration w MPWiK?"`
- **Oczekiwane:** zwrócone przez API liczby `usage` (input/output/thinking tokens) są realne
  (atrapy ich nie generują), a koszt USD z `core/domain/pricing.py::cost_usd` zgadza się z
  cennikiem użytego modelu.

## 2. Kompaktowanie rozmowy — 🔑 (ADR 0014)

- **Krok:** prowadź wieloturową rozmowę (CLI `--history` lub drzwi Teams) aż przekroczy próg
  kompaktowania z `AgentSettings`/`ConversationSettings`; obserwuj wywołanie podsumowujące.
- **Oczekiwane:** podsumowanie generuje się na granicy, kontekst pozostaje ciągły w kolejnych
  turach (agent „pamięta" wcześniejsze ustalenia), a koszt wywołania podsumowującego jest
  rozsądny.

## 3. Wdrożenie HTTP / Bramka 3 — ☁️ (ADR 0007)

- **Krok:** wg [`deploy-http.md`](deploy-http.md) — wygeneruj `tokens.json` z ACL, skonfiguruj
  IIS (response buffering **off**) + usługę Windows, potwierdź ekspansję `${WORKMATE_TOKEN}`.
  Smoke transportu: żądanie bez tokenu, ze złym tokenem, z dobrym tokenem, z obcym `Host`.
- **Oczekiwane:** `401` bez/na zły token (z nagłówkiem `WWW-Authenticate: Bearer`), `200` na
  dobry token, `421` na obcy `Host`; drzwi HTTP tylko do odczytu (`enable_write=False`).

## 4. Teams — wielotura, realne odpowiedzi po naprawie — 🔑 👥 (ADR 0015)

- **Krok:** w istniejącym wątku kanału wpisz wiadomość z **drugiego konta** (tura 2+).
- **Oczekiwane:** tura 2+ daje REALNĄ odpowiedź agenta (a nie komunikat zastępczy) — potwierdza
  naprawę bloków wyjściowych z ADR 0015.

## 5. Teams — obrazy inline i Word `.docx` — 🔑 👥 (ADR 0016)

- **Krok:** wyślij w wątku obraz (wklejka), GIF oraz emoji, a osobno plik `.docx`.
- **Oczekiwane:** bloki obrazów przyjęte przez Claude API (wklejka przez listowanie-fallback,
  GIF/emoji przez publiczny URL), a treść `.docx` odczytana poprawnie. (PDF i `.pptx` już
  potwierdzone — patrz brief 2026-07-13.)

## 6. Rozumowanie — `summarized`, streaming, poufność — 🔑 (ADR 0011)

- **Krok:** ustaw `thinking` na tryb z `display=summarized` i `max_tokens=128000`; zadaj
  pytanie wymagające rozumowania.
- **Oczekiwane:** realny tekst `summarized` w odpowiedzi, poprawny streaming przy dużym
  `max_tokens`, brak wycieku surowego rozumowania tam, gdzie nie powinno go być (sondy
  poufności).

## 7. Summarizer M3 — jakość i poprawność JSON — 🔑 (ADR 0009)

- **Krok:** podaj realny transkrypt do `AnthropicMeetingSummarizer.summarize`
  (`InMemoryTranscriptSource` wystarcza — realny fetch z Graph jest ☁️ Azure-gated i pozostaje
  odłożony). Czysta obróbka otoku JSON (`_extract_json`) jest już pokryta testem jednostkowym.
- **Oczekiwane:** model zwraca JSON walidujący się do `MeetingSummary` (`model_validate_json`),
  a jakość streszczenia (tytuł, decyzje, action items) jest sensowna.

## 8. GitHub → EventStore (ingest) — 🐙 (ADR 0019/0020)

- **Krok:** ustaw `WORKMATE_GITHUB_TOKEN`/`_OWNER`/`_REPO`, uruchom `uv run workmate-github`;
  utwórz ręcznie issue w repo. Podejrzyj `~/.workmate/events.db` (np. przez agenta narzędziem
  `read_recent_events` na drzwiach Teams).
- **Oczekiwane:** issue pojawia się jako zdarzenie `source="github", kind="issue_opened"`;
  ponowny poll go NIE dubluje (dedup), a issue utworzone kontem PAT jest pomijane (self-skip).

## 9. EventStore → Teams (notifier dual-target) — 🔑 👥 🐙 (ADR 0022)

- **Krok:** skonfiguruj `WORKMATE_TEAMS_PUSH_*` (włącz `ENABLE_CHAT` i/lub `ENABLE_CHANNEL`),
  zaloguj się raz device-code; wywołaj zdarzenie GitHub (nowe issue).
- **Oczekiwane:** powiadomienie ląduje w **czacie 1:1 ORAZ na kanale** (wg włączonych celów),
  treść zescapowana (bez żywego HTML). Restart procesu nie gubi ani nie dubluje (kursor).

## 10. Teams → GitHub (bramkowany zapis) — 🔑 👥 🐙 (ADR 0021)

- **Krok:** ustaw `WORKMATE_GITHUB_ENABLE_WRITE=true`; przez agenta na kanale Teams poproś
  „utwórz issue: …". 
- **Oczekiwane:** issue powstaje w skonfigurowanym repo (nie w cudzym); agent zwraca numer i URL;
  **potwierdź, że NIE wraca jako powiadomienie** (self-skip po loginie PAT + echo `source="teams"`).

---

> Po wykonaniu pozycji odnotuj wynik w briefie sesji (`.claude/sessions/`) i — gdy dotyczy —
> zaktualizuj powiązany ADR. Pozycje ☁️ Azure-gated (M3 fetch transkryptu, M4 mapowanie
> tożsamości) czekają na dostęp do infrastruktury i nie są tu wykonywalne.
