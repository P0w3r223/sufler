# Plan: Powiadomienia_teams — cotygodniowy asystent uzupełniania zmian (Microsoft Shifts)

> Status: zatwierdzony (2026-07-14). Ten dokument jest roadmapą projektu.
>
> **Aktualizacja 2026-07-16:** rdzeń zaimplementowany (Etapy 0–4 pierwotnego planu, patrz „Postęp"
> niżej). Termin przełączony na **piątek 16:00**; wybór osób przez `ONLY_USER_IDS`; **nasłuch
> przeprojektowany** na adaptacyjny backoff + wygasanie okna odpowiedzi + odporny provider tokenu
> (silent-only, `--login` ze startu) — decyzje w **[ADR 0002](docs/adr/0002-adaptive-listener.md)**.
> Wielozespołowość zaprojektowana w **[ADR 0001](docs/adr/0001-multi-team-shifts-support.md)**
> (odłożona). Zostaje smoke na żywym tenancie (poniżej).

## Kontekst — po co to robimy

Pracownicy pionu uzupełniają swoje godziny pracy w **zakładce „zmiany" = Microsoft Shifts**
(aplikacja Shifts w Teams). Część osób zapomina uzupełnić grafik na nadchodzący tydzień.
Cel: **co niedzielę o 16:00** automatycznie wykryć, kto nie ma jeszcze zmian na przyszły
tydzień, i wysłać każdej takiej osobie **prywatną wiadomość Teams (czat 1:1)**, która:
1. przypomina o uzupełnieniu grafiku,
2. podsuwa gotowca — grafik z zeszłego tygodnia tej osoby („powtórzyć jak ostatnio?"),
3. pozwala odpisać naturalnym językiem, co zmienić (np. „w piątek mnie nie będzie, reszta
   tak samo" albo „w piątek tylko 10–20"), po czym **bot sam wpisuje zmiany do Shifts**
   za pracownika i potwierdza.

Projekt mieszka w folderze `Powiadomienia_teams/` jako samodzielny pod-projekt. Reużywa
wzorców z istniejących drzwi `teams_graph`, ale nie wpina się w zamrożony katalog narzędzi
WorkMate.

### Decyzje użytkownika (przyjęte założenia)
| Wymiar | Wybór |
|--------|-------|
| Źródło grafiku | **Microsoft Shifts** (Graph `/teams/{id}/schedule/…`) |
| Zakres interakcji | **Dwukierunkowy** — bot rozumie odpowiedź NL i **zapisuje** zmiany |
| Kanał dostawy | **Prywatny czat 1:1 Teams** |
| Uruchamianie | **Stały proces Python** liczący następny termin i śpiący do niego |

---

## Kluczowe ustalenia z kodu (co już jest, co trzeba dołożyć)

- **Auth do Graph już istnieje jako wzorzec**: `src/workmate/adapters/inbound/teams_graph/auth.py`
  — delegowany **device-code (MSAL `PublicClientApplication`)**, cache refresh-tokenu w pliku
  `~/.workmate/teams_token_cache.bin` (chmod 600). To skopiujemy/zaadaptujemy.
- **Klient Graph po httpx** (`teams_graph/graph.py`, `HttpxGraphChannelClient`) potrafi dziś
  **tylko `post_reply`** (odpowiedź w istniejącym wątku kanału). **Brakuje**: wysyłki 1:1,
  odczytu członków zespołu, całej powierzchni Shifts. To trzeba napisać.
- **Brak schedulera** w całym repo (żadnego cron/apscheduler). Najbliższy wzorzec pętli w tle
  to `teams_graph/poller.py` (`while True` + `asyncio.sleep`). „Co niedzielę 16:00" trzeba
  policzyć samemu (kolejny termin + sleep-until, strefa `Europe/Warsaw` + DST).
- **Brak modelu zmian/grafiku** w domenie (`core/domain/models.py` to tylko notatki/projekty).
  Model `Shift`/`WeekSchedule` powstanie w nowym projekcie.
- **Brak rejestru osób** — nie ma mapowania osoba → Teams/AAD/email. Listę pracowników
  pobierzemy z Graph (`GET /teams/{id}/members`).
- **Config**: wzorzec to `@dataclass(frozen=True)` + `from_env()` + `validate()` (patrz
  `TeamsGraphSettings` w `config.py:433`). Powtórzymy go w nowym projekcie.
- **Runtime agenta (Claude)** istnieje (`outbound/anthropic_llm.py`, extra `agent`), klucz z
  `ANTHROPIC_API_KEY`/`WORKMATE_AGENT_API_KEY`. Wykorzystamy Claude do interpretacji odpowiedzi
  NL → strukturalne zmiany grafiku.
- **Stack zależności już w repo**: `msal 1.37`, `httpx 0.28`, `anthropic 0.116` (bez
  `msgraph-sdk`/`azure-identity` — Graph wołany wprost po httpx). Nowy projekt użyje tego samego.

---

## Twarda prawda o Microsoft Graph (ograniczenia, które kształtują projekt)

Te punkty są nieoczywiste i **muszą** być zaakceptowane/zweryfikowane, zanim ruszy budowa:

1. **Delegowany device-code = działanie JAKO konkretny zalogowany człowiek.** Każde wywołanie
   Graph (wysłanie 1:1, zapis zmiany) wykona się **z konta osoby, która się zalogowała** —
   nie z anonimowego „bota WorkMate". Wniosek: procesem powinno logować się **konto kierownika
   / właściciela zespołu z prawem do grafiku** (scheduling manager). Wiadomości do pracowników
   będą wyglądać, jakby pisał je ten kierownik. To akceptowalne dla wewnętrznego pilotażu; jeśli
   docelowo ma to być tożsamość „bota", trzeba ścieżki Bot Framework / uprawnień aplikacyjnych
   (odłożone, patrz „Warianty odrzucone").

2. **Zapis do Shifts jest modelem menedżerskim.** W Shifts to menedżer/właściciel publikuje
   zmiany; zwykły pracownik przez API zwykle nie tworzy własnej „shared shift" (ma tylko
   requesty: swap/offer/timeOff). Dlatego „bot wpisuje zmiany za pracownika" realnie znaczy:
   **proces działa jako kierownik z `Schedule.ReadWrite.All` i tworzy zmiany dla członków zespołu.**
   Po zapisie grafik trzeba **udostępnić** (`POST /teams/{id}/schedule/share`), żeby pracownik
   go zobaczył.

3. **Proaktywny czat 1:1 przez delegowany Graph** jest możliwy (`POST /chats` z `Chat.Create`,
   potem `POST /chats/{id}/messages` z `ChatMessage.Send`), ale: (a) wiadomość idzie od
   zalogowanego kierownika, (b) trzeba to potwierdzić na żywym tenantcie (bywa ograniczane
   politykami). **Ryzyko do smoke-testu.**

4. **Nieprzerwane działanie a token.** Stały proces trzyma się na **refresh-tokenie** z cache
   MSAL. Refresh-token wygasa (rolling ~90 dni, szybciej przy politykach Conditional Access) →
   proces będzie wymagał **ponownego logowania człowieka** co jakiś czas. Trzeba: alert przy
   utracie tokenu + jednorazowy device-code login przy pierwszym starcie. (Prawdziwie bezobsługowy
   byłby dopiero wariant aplikacyjny z certyfikatem — patrz warianty odrzucone.)

5. **Wykrywanie „nie uzupełnił".** „Brak zmian na przyszły tydzień" to sygnał domyślny, ale
   trzeba doprecyzować na żywo: czy liczą się `draftShift` czy `sharedShift`, czy `timeOff`
   (urlop) to „uzupełnione", i jak rozpoznać `schedulingGroupId` (wymagany przy zapisie zmiany).

---

## Wymagane uprawnienia (to o co pytałeś wprost)

### Uprawnienia delegowane Microsoft Graph (admin consent wymagany)
Konto logujące = **kierownik/właściciel zespołu**.

| Uprawnienie | Po co |
|-------------|-------|
| `User.Read` | tożsamość zalogowanego (id, mail) |
| `Team.ReadBasic.All` / `Group.Read.All` | rozpoznanie zespołu |
| `TeamMember.Read.All` (lub `GroupMember.Read.All`) | **lista pracowników do powiadomienia** + ich AAD id/mail |
| `User.ReadBasic.All` | displayName/mail członków |
| `Schedule.Read.All` | odczyt zmian: wykrycie luki na przyszły tydzień + odczyt zeszłego tygodnia (gotowiec) |
| `Schedule.ReadWrite.All` | **zapis zmian** (bot wpisuje godziny) + `share` grafiku |
| `Chat.Create` | utworzenie czatu 1:1 z pracownikiem |
| `ChatMessage.Send` | wysłanie powiadomienia w czacie 1:1 |
| `Chat.Read` (lub `Chat.ReadWrite`) | **odczyt odpowiedzi** pracownika (polling wiadomości czatu) |
| `offline_access` | refresh-token dla stałego procesu (MSAL dokłada sam — nie wpisywać jawnie) |

> MSAL sam dokłada `openid`/`profile`/`offline_access` — nie umieszczać ich w liście scope
> (jak ostrzega `config.py:393-394`).

### Rejestracja aplikacji w Azure AD
- App registration: **public client** (jak istniejąca WorkMate) z włączonym
  „Allow public client flows = Yes" (device-code).
- API permissions → dodać powyższe **delegated** → **Grant admin consent** (jednorazowo, przez
  administratora tenanta).
- Można reużyć istniejącej aplikacji WorkMate (dołożyć brakujące scope: `Schedule.*`, `Chat.*`,
  `TeamMember.Read.All`) — to jednorazowy re-consent.

### Sekrety / klucze (poza repo i `data/`)
- Cache tokenu MSAL: plik na dysku, chmod 600 (jak `teams_token_cache.bin`).
- `ANTHROPIC_API_KEY` (interpretacja odpowiedzi przez Claude) — z env, `repr=False`.
- Brak client-secret (public client). Zmienne `POWIADOMIENIA_*` w `.env` (nie w repo).

---

## Status weryfikacji na żywo — Smoke #1 (2026-07-14)

Sprawdzono na żywym tenancie (`99b17207-…`), konto `piotr.czastkiewicz@contoso.onmicrosoft.com`,
`client_id=c0ffee00-0000-4000-8000-000000000015` — reużyto cache tokenu MSAL z drzwi `teams_graph`
(silent refresh dobrał nowe uprawnienia bez ponownego logowania).

**Uprawnienia — stan faktyczny:**
| Uprawnienie | Stan |
|-------------|------|
| `Schedule.Read.All` | ✅ nadane, działa (odczyt grafiku 200) |
| `Schedule.ReadWrite.All` | ✅ nadane (zapis jeszcze nietestowany — osobny smoke) |
| `Chat.Create` | ✅ nadane |
| `Chat.ReadWrite`, `ChannelMessage.Send`, `Team.ReadBasic.All`, `User.Read` | ✅ (już w cache) |
| `TeamMember.Read.All` | ✅ **nadane** (2026-07-14, po admin consent) → `/teams/{id}/members` = 200 |
| `User.ReadBasic.All` | ✅ **nadane** (2026-07-14) → rozwiązywanie `userId → imię/e-mail` |

**Blokada USUNIĘTA (2026-07-14):** po admin consent MSAL dobrał oba uprawnienia po cichu
(bez re-logowania). Roster zespołu „Stażyści" (6 osób) i mapowanie id→osoba działają. Ręczny
roster YAML (`roster.py`) był fallbackiem na wypadek braku `TeamMember.Read.All` — **usunięty
2026-07-21**, bo uprawnienie działa stabilnie, a niepodłączony fallback tylko rósł jako martwy kod.

**Roster „Stażyści" (potwierdzony na żywo):** Mikołaj Anonimowicz (owner), Jerzy Zastepski (owner),
Oskar Zastepski, Igor Zakryty, Kamil Ukryty, Piotr Częstkiewicz (owner — konto bota). Konto bota
jest **właścicielem** zespołu → spełniony warunek zapisu zmian innym (do potwierdzenia Smoke'iem
zapisu).

**Edge case (ważny):** w zmianach pojawia się `userId=9d8beddf…` z 58 zmianami (gł. 2025), którego
**nie ma** już na liście członków — to **były członek**. Wniosek: zmiany „sierot" (ex-członków)
trzeba ignorować przy wykrywaniu luk (obecny `members_without_shifts` robi to poprawnie — iteruje
tylko po aktualnym rosterze).

**Dane grafiku (odczyt działa):**
- Shifts **prowizjonowane tylko w jednym zespole**: „BIAP - Pion Inteligentnych Technologii —
  Stażyści" (`c0ffee00-0000-4000-8000-000000000019`, `provisionStatus=Completed`).
  Pozostałe: `BIAP-PI` (8ea8af09…) i `Workmate-Teams` (27fefc2f…) = `NotStarted`.
- 63 zmiany (1 strona, bez paginacji), tylko **2 identyfikatory osób**: `9d8beddf…` (58, głównie
  2025) i `1b3fa85a…` (5, 13–17.07.2026). **Na przyszły tydzień 20–26.07: 0 osób** ma zmiany.
- Grupa grafiku do zapisu: `TAG_c0ffee00-0000-4000-8000-000000000009` (aktywna); część zmian ma
  `schedulingGroupId=None` (dozwolone). Godziny w **UTC** → przeliczać na `Europe/Warsaw`.

**Do potwierdzenia z użytkownikiem:** (1) czy docelowy zespół to „Stażyści"; (2) czy
`piotr.czastkiewicz@…` to zamierzony „głos" bota i czy jest właścicielem/kierownikiem grafiku
tego zespołu (warunek zapisu zmian innym — do sprawdzenia Smoke'iem zapisu).

**Zweryfikowane wartości do konfiguracji:** `POWIADOMIENIA_CLIENT_ID=c0ffee00-0000-4000-8000-000000000015`,
`POWIADOMIENIA_TENANT_ID=c0ffee00-0000-4000-8000-000000000017`,
`POWIADOMIENIA_TEAM_ID=c0ffee00-0000-4000-8000-000000000019`.

---

## Architektura nowego projektu

Samodzielny pod-projekt uv w `Powiadomienia_teams/`, styl heksagonalny jak WorkMate:

```
Powiadomienia_teams/
  pyproject.toml            # własny projekt uv: msal, httpx, anthropic, pyyaml, python-dotenv, tzdata
  README.md                 # po polsku (proza), instrukcja uruchomienia + consent
  .env.example              # POWIADOMIENIA_CLIENT_ID / _TENANT_ID / _TEAM_ID / _MANAGER_LOGIN / _RUN_AT / ...
  src/powiadomienia_teams/
    config.py               # Settings(frozen dataclass)+from_env()+validate()  <- wzor: workmate/config.py:433
    domain/models.py        # Shift, WeekSchedule, Member, PendingReminder  (model, ktorego WorkMate nie ma)
    graph/
      auth.py               # device-code MSAL  <- adaptacja teams_graph/auth.py
      client.py             # httpx: list_members, read_shifts(week), write_shift, share_schedule,
                            #        create_or_get_chat, send_chat_message, list_chat_messages
    scheduler/weekly.py     # next_run(now)->datetime (niedziela 16:00 Europe/Warsaw, DST) + petla sleep-until
    reminders/
      detect.py             # PURE: members_without_shifts(members, shifts_next_week) -> [Member]
      propose.py            # PURE: proposal_from_last_week(member, shifts_last_week) -> WeekSchedule
    agent/interpreter.py    # Claude: (proposal + odpowiedz NL) -> WeekSchedule (strukturalne zmiany)
    app.py                  # main(): settings->auth->client-> uruchom petle tygodniowa + listener odpowiedzi
    state.py                # JSON: kto powiadomiony, pending rozmowy, watermark odpowiedzi, applied (idempotencja)
  tests/                    # lustrzane: test_detect, test_propose, test_weekly (next_run), test_interpreter, test_client
  docs/adr/0001-*.md        # ADR projektu (po angielsku, wg konwencji repo)
```

**Zasada rozdziału I/O ↔ logika**: `detect.py`, `propose.py`, `scheduler/weekly.next_run`
są **czyste** (wstrzykiwany `now`, dane wejściowe = struktury) → w pełni testowalne bez sieci.
Cała sieć (Graph, Claude) siedzi w `graph/` i `agent/`.

---

## Przepływ działania (dwie fazy w cyklu tygodniowym)

**Faza A — niedziela 16:00, „nudge":**
1. `list_members(team_id)` → lista pracowników (+ AAD id, mail).
2. `read_shifts(next_week)` → kto ma zero zmian na przyszły tydzień = luka (`detect.py`).
3. Dla każdej osoby z luką: `read_shifts(last_week, member)` → `proposal_from_last_week` (`propose.py`).
4. `create_or_get_chat(me, member)` → `send_chat_message(...)`: przypomnienie + gotowiec
   (np. „Cześć! Nie masz jeszcze zmian na 21–27.07. W zeszłym tygodniu: Pon–Pt 8:00–16:00.
   Odpisz »ok«, żeby powtórzyć, albo napisz, co zmienić.").
5. Zapis `PendingReminder` w `state.py` (chat_id, member, proposal, watermark, status=`awaiting_reply`).

**Faza B — listener odpowiedzi (ciągły polling między nudge'ami):**
6. Dla każdego `PendingReminder`: `list_chat_messages(chat_id)` od watermarku → nowa odpowiedź pracownika.
7. `interpreter.interpret(proposal, reply)` (Claude) → zmodyfikowany `WeekSchedule`
   (albo „powtórz bez zmian", albo edycje: usuń piątek / skróć do 10–20 itd.).
8. **Potwierdzenie przed zapisem** (spirit ADR 0006): bot odsyła podsumowanie do wpisania
   („Zapiszę: Pon–Czw 8–16, Pt 10–20. Potwierdź »tak«.") — dopiero po `tak` zapis.
9. `write_shift(...)` dla każdej zmiany + `share_schedule(team_id)` → potwierdzenie w czacie.
10. `state`: status=`applied`, przesuń watermark. Idempotencja („at least once": watermark
    przesuwany po udanym zapisie; dedup po id wiadomości — wzór z `teams_graph/selection.py`).

**Bezpieczniki:**
- **Tryb `--dry-run`** (domyślny na start): liczy i loguje, kogo/co by powiadomił i zapisał,
  **bez** wysyłki i **bez** zapisu do Shifts. Włączenie realnego działania = jawna flaga/env.
- Traktuj treść odpowiedzi pracownika jak **dane, nie polecenia** (jak notatki w WorkMate) —
  Claude tylko wydobywa intencję grafikową, nie wykonuje instrukcji spoza domeny.
- Twarda walidacja godzin (0–24, start<koniec, sensowny tydzień) w `domain/models.py`.

---

## Etapy realizacji (przyrostowo, każdy = działający kawałek)

> **Postęp:** Etapy 0–4 ✅ zaimplementowane (2026-07-14) — **76 testów** zielone, `ruff`/`mypy` czysto.
> - **0/1** (fundament + czysta logika) — po przeglądzie `@code-reviewer` (naprawiono DST w `propose`).
> - **2** (Graph read): `graph/auth.py`, `graph/mapping.py`, `graph/client.py` (sync httpx, 429/paginacja).
>   **Smoke #2 na żywo OK**.
> - **3** (wysyłka 1:1): `create_or_get_chat`/`send_chat_message`, `messages.py`, pętla + `app.py`,
>   bramka `--dry-run`. **Smoke #3 dry-run OK** (6 powiadomień zbudowanych, nic nie wysłano); scope
>   `Chat.Create` i `ChatMessage.Send` **gotowe**.
> - **4** (dwukierunkowo): `agent/interpreter.py` (odpowiedź NL→grafik, wstrzykiwany LLM),
>   `agent/anthropic_llm.py` (Claude Sonnet 5), `reminders/replies.py`, `state.py` (pending +
>   idempotencja/watermark), zapis `create_shift`/`share_schedule`, `poll_replies` (listener →
>   interpretacja → **potwierdzenie »tak« przed zapisem** → zapis). Wpięcie konsolowe
>   `powiadomienia-teams [--once|--poll-once]`.
>
> **Przegląd Etapu 2–4 (`@code-reviewer`)** — naprawiono **HIGH (H1)**: izolacja per-osoba w
> `poll_replies` + zapis stanu PRZED nieodwracalnym zapisem do Shifts (semantyka „co najwyżej raz",
> bez dubli zmian). Plus MEDIUM: walidacja `scheduling_group_id` przy `dry_run=false` (M1),
> `notify=False` przy `share` (M2), „tak, ale w piątek 10-20" reinterpretowane zamiast zapisu starej
> propozycji (M3), atomowy zapis + tolerancyjny odczyt stanu (M4), `thinking={"type":"disabled"}` +
> model `claude-opus-4-8` konfigurowalny (M5), watermark porównywany po sparsowanym czasie (M6).
> Werdykt po fixach: brak CRITICAL/HIGH.
>
> **Smoke ZAPISU (2026-07-14, na żywo, Mikołaj Anonimowicz, tydzień 20–26.07):** utwórz→zweryfikuj→
> skasuj. **Kontrakt potwierdzony:** `create_shift` z samym `sharedShift` publikuje zmianę od razu
> (widoczna w `read_shifts` jako `[shared]`, poprawna konwersja UTC↔Warszawa) — **osobny `share`
> zbędny, usunięty ze ścieżki zapisu**. `DELETE` = 204, po sprzątaniu 0 zmian = stan wyjściowy
> (żadnego śladu). Domknięto walidację modelu: **max długość zmiany 24h + `week_start`=poniedziałek**
> (zakres tygodnia gwarantowany konstrukcyjnie przez `build_schedule`). **86 testów** zielone.
>
> ⚠️ **KOD gotowy i zweryfikowany. Pozostaje tylko decyzja operacyjna użytkownika:** ustawić
> `POWIADOMIENIA_DRY_RUN=false` (+ `SCHEDULING_GROUP_ID`, wymuszone walidacją) i uruchomić proces
> `powiadomienia-teams` — dopiero wtedy w niedzielę 16:00 pójdą realne wiadomości do zespołu i zapis
> zmian. Prerekwizyty (smoke zapisu, walidacja) — **odhaczone**.
>
> **Do domknięcia przed Etapem 4 (z przeglądu, świadomie odłożone):** twarda walidacja modelu
> pod ścieżkę zapisu — max długość zmiany, `week_start` = poniedziałek, kontrola że propozycja
> mieści się w docelowym tygodniu; opcjonalnie `scopes` z env (jak `TeamsGraphSettings`).

**Etap 0 — szkielet + prerekwizyty (bez sieci):**
- Utwórz folder `Powiadomienia_teams/` + `pyproject.toml` (uv), `README.md`, `.env.example`.
- `config.py` (Settings+from_env+validate), `domain/models.py` (Shift/WeekSchedule/Member).
- Testy jednostkowe modelu (walidacja godzin). `ruff`/`mypy` zielone.

**Etap 1 — czysta logika (bez sieci, w pełni testowalna):**
- `reminders/detect.py`, `reminders/propose.py`, `scheduler/weekly.next_run`.
- Testy: wykrywanie luki, budowa gotowca z zeszłego tygodnia, poprawny „następny niedzielny
  termin 16:00" wokół zmiany czasu (DST).

**Etap 2 — klient Graph (odczyt) + auth:**
- `graph/auth.py` (device-code), `graph/client.py`: `list_members`, `read_shifts`.
- **Smoke #1 (żywy tenant):** zaloguj kierownika, wypisz członków i zmiany na 2 tygodnie
  (potwierdź semantykę „luki", `schedulingGroupId`, draft vs shared).

**Etap 3 — powiadomienia 1:1 (wysyłka, jeszcze bez zapisu):**
- `client.create_or_get_chat`, `send_chat_message`; pętla tygodniowa `scheduler/weekly` + `app.py`.
- Tryb `--dry-run` → realna wysyłka.
- **Smoke #2:** wyślij testowe 1:1 do siebie; potwierdź, jak wygląda nadawca.

**Etap 4 — interpretacja odpowiedzi (Claude) + zapis do Shifts:**
- `agent/interpreter.py`, `client.write_shift`, `client.share_schedule`, listener odpowiedzi,
  potwierdzenie przed zapisem, `state.py` (idempotencja).
- **Smoke #3:** pełny obieg na koncie testowym — nudge → odpowiedź „w piątek 10–20" → potwierdź →
  zmiana widoczna w Shifts.

**Etap 5 — hardening:** obsługa 429/Retry-After (wzór `graph.py`), alert przy utracie
refresh-tokenu, logowanie, ADR projektu, README z instrukcją consentu i uruchomienia.

---

## Prerekwizyty (do zorganizowania równolegle z Etapem 0–1)
- [ ] **Konto kierownika/właściciela** zespołu, które będzie „głosem" bota (delegowany login).
- [ ] **Administrator tenanta** do udzielenia admin consent na uprawnienia z sekcji wyżej.
- [ ] **`team_id`** docelowego zespołu (tryb odkrywania Graph albo z URL Teams).
- [ ] Potwierdzenie, że zespół **ma prowizjonowany Shifts** (istnieje `schedule`).
- [ ] `ANTHROPIC_API_KEY` dla interpretacji odpowiedzi.

---

## Ryzyka do weryfikacji na żywym tenancie (zanim uznamy funkcję za gotową)
1. Czy konto kierownika (delegated `Schedule.ReadWrite.All`) **zapisze zmianę dla innego członka**
   i czy `share` ją uwidoczni.
2. Czy proaktywny **czat 1:1** (`Chat.Create`+`ChatMessage.Send`) działa i jak wygląda nadawca.
3. Czy **odczyt odpowiedzi** z czatu 1:1 przez polling (`GET /chats/{id}/messages`) jest dozwolony
   i jak throttlowany.
4. **Żywotność refresh-tokenu** stałego procesu (polityki CA mogą wymuszać re-login).
5. Dokładna semantyka „nie uzupełnił" (draft/shared/timeOff, `schedulingGroupId`).

*(Wpisuje się w istniejący wzorzec `docs/how-to/live-smoke-checklist.md`.)*

---

## Weryfikacja (jak sprawdzimy, że działa)
- **Testy jednostkowe** (`uv run pytest` w podprojekcie): `detect`, `propose`, `next_run` (DST),
  `interpreter` (na atrapie LLM), parsowanie odpowiedzi Graph na utrwalonych fixture'ach.
- **Tryb `--dry-run`** na żywym tenancie: log „kogo/co bym powiadomił i zapisał" bez efektów
  ubocznych — porównanie z rzeczywistym stanem Shifts.
- **3 smoke-testy** (Etapy 2–4) na koncie/zespole testowym, wg checklisty live-smoke.
- **Ręczny e2e**: uruchom proces, wymuś „następny termin = za 1 min", przejdź pełny obieg
  nudge → odpowiedź NL → potwierdzenie → zapis widoczny w Shifts.
- `ruff check .` + `mypy` zielone.

---

## Warianty odrzucone (świadomie, do rozważenia później)
- **Tożsamość „bota" (Bot Framework / uprawnienia aplikacyjne)** zamiast konta kierownika —
  daje prawdziwie bezobsługowe działanie i neutralnego nadawcę, ale `ChatMessage.Send`
  aplikacyjny jest chronionym API (wymaga zgody Microsoftu/RSC) i to ścieżka odłożona w ADR 0015.
- **Drzwi wewnątrz `src/workmate/`** (konwencja repo) zamiast osobnego folderu — spójniejsze z
  pakietem/mypy/pytest, ale użytkownik świadomie chce osobny projekt w `Powiadomienia_teams/`.
  Koszt osobnego folderu: własny `pyproject.toml` i ~50 linii zduplikowanego auth.

---

## Otwarte decyzje (do potwierdzenia przy realizacji, nie blokują planu)
- Jeden zespół (pilotaż) czy wiele zespołów od razu?
- Godzina/strefa sztywno 16:00 `Europe/Warsaw` czy z env (`POWIADOMIENIA_RUN_AT`)?
- Czy wymagać jawnego „tak" przed zapisem (rekomendowane), czy zapisywać od razu po interpretacji?
- Jak długo po niedzieli trzymać okno na odpowiedzi (np. do wtorku), zanim zamkniemy pending?

---

## Czyszczenie i wydanie 0.2.0 (2026-07-21)

Przegląd referencji przed pierwszym wdrożeniem serwerowym wykazał kod, który nigdy nie wszedł na
ścieżkę wykonania. Usunięte (zatwierdzone jawnie, nie automatem):

- `roster.py` + `load_roster` + `config.roster_path` + `POWIADOMIENIA_ROSTER_PATH` +
  `roster.example.yaml` — fallback YAML na wypadek braku `TeamMember.Read.All`; uprawnienie
  działa od 2026-07-14, fallback nigdy nie został podłączony do `app.py`. Wraz z nim wypadła
  zależność `pyyaml` (jedyny konsument) i `types-PyYAML`.
- `GraphClient.share_schedule` — `create_shift`/`create_time_off` publikują od razu jako
  `sharedShift`/`sharedTimeOff`, więc osobne udostępnianie było zbędne (zgodnie z notatką przy
  ścieżce zapisu).
- `GraphClient.schedule_provision_status`, `CANONICAL_REASONS` — zero odwołań w kodzie i testach.
- `reminders.replies.is_affirmative`, `looks_like_schedule` — zastąpione przez
  `is_pure_affirmation` (heurystyka „są cyfry" była krucha), żyły już tylko w testach.
- Pola `Shift.shared` / `TimeOff.shared` — zapisywane w mapperach, nigdy nieczytane do decyzji.
  Wybór ciała `sharedX` → `draftX` w `graph/mapping.py` **nie zmienił semantyki** (dalej
  `if body is None`, a nie `or` — puste `sharedShift` przy ważnym drafcie nadal daje `None`).
- 5 testów bez pokrycia gałęzi: domyślne wartości dataclass (`ReplyDecision`, `Member`), duplikat
  `week_windows` z `test_weekly.py`, słaby test własnościowy backoffu, duplikat pustego czasu wolnego.

Suita: **250 → 236 testów**, wszystkie zielone; `ruff` i `mypy` czyste.

**Dług techniczny (świadomie odłożony):** `app.py` ma ~995 linii przy przyjętym limicie 800.
Podział (wydzielenie pętli serwisowej, pulsu i alertów z `run_once`/`poll_replies`) odłożony,
żeby nie wprowadzać ryzyka regresji tuż przed pierwszym uruchomieniem na serwerze.
