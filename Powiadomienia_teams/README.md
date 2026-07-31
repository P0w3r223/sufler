# Powiadomienia_teams

Cotygodniowy asystent uzupełniania zmian w **Microsoft Shifts** (Teams). W **piątek o 16:00**
wykrywa, kto z wybranych osób nie ma zmian na **następny tydzień pracujący**, i pisze do niego
prywatną wiadomość 1:1 z gotowcem „jak w zeszłym tygodniu"; po odpowiedzi naturalnym językiem
bot — po jawnym „tak" — wpisuje zmiany do Shifts za pracownika. Pracownik może też **odmówić**
(„nie chcę zmian w tym tygodniu", „pomiń mnie") — bot nic nie zapisuje i kończy przypominanie.
Potem **nasłuchuje na odpowiedź** z adaptacyjnym odstępem (gęsto tuż po nudge'u, wolniej w ciszy),
a po oknie `reply_window_hours` bez reakcji uprzejmie zamyka temat. Pełny zamysł: **[PLAN.md](PLAN.md)**.

> Samodzielny pod-projekt (własny `pyproject.toml`, środowisko `uv`). Reużywa wzorców
> uwierzytelniania z drzwi `teams_graph` głównego repo WorkMate.
>
> **Status kopii.** Ten katalog jest kopią referencyjną — pod-projekt jest wdrażany i utrzymywany
> osobno (Docker + systemd na serwerze docelowym). Źródłem prawdy o wersji produkcyjnej jest to
> środowisko, nie ten katalog; treść tutaj może od niego odbiegać.

## Stan prac

- **Rdzeń gotowy:** konfiguracja (`config.py`), model domenowy (`domain/models.py`), czysta
  logika (`reminders/`, `scheduler/`), warstwa Graph (`graph/`), interpretacja odpowiedzi przez
  Claude (`agent/interpreter.py`), stan i idempotencja (`state.py`), pełny obieg w `app.py`.
- **Nasłuch (ADR 0002):** adaptacyjny backoff (`scheduler/backoff.py`), wygasanie okna odpowiedzi
  i sprzątanie stanu (`reminders/lifecycle.py`), odporny na długie działanie provider tokenu
  (silent-only, `--login` tylko ze startu). Wielozespołowość zaprojektowana (ADR 0001), odłożona.
- **Wygaszanie po dowodzie (ADR 0003):** temat zamyka się dopiero po UDANYM odczycie czatu, który
  nic nie przyniósł — przestój usługi ani awaria Graph nie wypalają już cudzego okna odpowiedzi
  i nie kończą się nieprawdziwym „nie dostałem odpowiedzi". Potwierdzenie w trakcie tygodnia
  docelowego zapisuje tę część tygodnia, która jeszcze przed nami; dni zakończone są odsiewane,
  żeby nie wpisywać do grafiku przeszłości.

## Uruchomienie na żywo

```bash
uv run --directory Powiadomienia_teams powiadomienia-teams --login   # jednorazowe logowanie (device-code)
uv run --directory Powiadomienia_teams powiadomienia-teams           # usługa: pętla tygodniowa + nasłuch
uv run --directory Powiadomienia_teams powiadomienia-teams --once    # jeden przebieg powiadomień i wyjście
```

Realne działanie wymaga `POWIADOMIENIA_DRY_RUN=false`. Bez ważnego tokenu usługa nie wystartuje
(w terminalu poprosi o logowanie; bez terminala wychodzi z instrukcją `--login` — nie zawiesza się).

> **Uruchamiaj tylko JEDNĄ instancję.** Blokada pliku (`<state>.lock`) uniemożliwia równoczesny
> start drugiego procesu (np. `--poll-once` obok działającej usługi) — chroni przed podwójnym
> zapisem do Shifts. Nieudany przebieg jest ponawiany (backoff), a start w oknie łaski po minionym
> terminie nadrabia zaległe powiadomienia (`POWIADOMIENIA_CATCHUP_GRACE_HOURS`).

## Wdrożenie na serwer

Obowiązująca ścieżka: **[deploy/README-docker.md](deploy/README-docker.md)** — obraz Docker
budowany na serwerze z paczki źródłowej (`scripts/pack.sh` → scp → `scripts/build-image.sh`).
Testy biegną w trakcie budowania, więc obraz nie powstanie z niesprawnego kodu.

Wariant zapasowy bez Dockera (systemd): [deploy/README-serwer.md](deploy/README-serwer.md).

Konto »głosu« bota i lista odbiorców to **konfiguracja, nie kod**: konto = to, którym wykonasz
`--login`; odbiorcy = `POWIADOMIENIA_ONLY_USER_IDS` (zmiana + restart, bez przebudowy).
Identyfikatory AAD wypisze `scripts/lista_czlonkow.py`.

## Uruchamianie zadań deweloperskich

Z katalogu głównego repo (uv utworzy środowisko podprojektu przy pierwszym uruchomieniu):

```bash
uv run --directory Powiadomienia_teams pytest -q       # testy
uv run --directory Powiadomienia_teams ruff check .    # lint
uv run --directory Powiadomienia_teams mypy            # typy
```

## Konfiguracja

Skopiuj `.env.example` → `.env` i uzupełnij. Zmienne mają prefiks `POWIADOMIENIA_`. Kluczowe:

- `CLIENT_ID`, `TENANT_ID`, `TEAM_ID` — wymagane (tożsamość aplikacji + zespół).
- **`ONLY_USER_IDS`** — wybór osób: lista AAD user-id po przecinku (puste = wszyscy bez zmian).
- `RUN_WEEKDAY` (domyślnie `4` = piątek), `RUN_HOUR` (16), `TIMEZONE` (`Europe/Warsaw`).
- `REPLY_WINDOW_HOURS` (48) — po tylu h ciszy zamknij okno (liczone od ostatniej aktywności,
  a zamknięcie wymaga UDANEGO odczytu czatu — ADR 0003); `SEND_EXPIRY_MESSAGE` (true) — czy
  wysłać wtedy uprzejme domknięcie.
- `POLL_INTERVAL_S` (10, bazowy odstęp nasłuchu) i `POLL_MAX_INTERVAL_S` (3600, górny limit
  backoffu — nieobecny pracownik = sprawdzanie czatu raz na godzinę; obsłużona odpowiedź
  natychmiast wraca do odstępu bazowego).
- `CATCHUP_GRACE_HOURS` (6) — ile h po minionym terminie wolno nadrobić zaległy przebieg (0 = off).
- `DRY_RUN` (domyślnie `true`) — nic nie jest wysyłane ani zapisywane, dopóki nie ustawisz `false`.

## Uprawnienia (Microsoft Graph, delegowane, admin consent)

`Schedule.Read.All`, `Schedule.ReadWrite.All`, `Chat.Create`, `Chat.ReadWrite`,
`ChatMessage.Send`, `TeamMember.Read.All`, `User.ReadBasic.All`. Logowanie jako
właściciel/kierownik zespołu (zapis zmian w Shifts jest menedżerski). Szczegóły i status
weryfikacji na żywo: [PLAN.md](PLAN.md).
