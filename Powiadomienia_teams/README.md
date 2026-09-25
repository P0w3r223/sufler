# Powiadomienia_teams

Cotygodniowy asystent uzupełniania zmian w **Microsoft Shifts** (Teams). W **piątek o 16:00**
wykrywa, kto z wybranych osób nie ma zmian na **następny tydzień pracujący**, i pisze do niego
prywatną wiadomość 1:1 z gotowcem „jak w zeszłym tygodniu"; po odpowiedzi naturalnym językiem
bot — po jawnym „tak" — wpisuje zmiany do Shifts za pracownika. Pracownik może też **odmówić**
(„nie chcę zmian w tym tygodniu", „pomiń mnie") — bot nic nie zapisuje i kończy przypominanie.
Potem **nasłuchuje na odpowiedź** z adaptacyjnym odstępem (gęsto tuż po nudge'u, wolniej w ciszy),
a po upływie **terminu kalendarzowego** bez reakcji uprzejmie zamyka temat.
Pełny zamysł: **[PLAN.md](PLAN.md)**.

> Samodzielny pod-projekt (własny `pyproject.toml`, środowisko `uv`). Reużywa wzorców
> uwierzytelniania z drzwi `teams_graph` głównego repo Sufler.

> **Wersja i pochodzenie źródeł.** Ten katalog odpowiada obrazowi **0.2.19** — temu, który
> działa u klienta. Źródła zostały **odzyskane z obrazu** 2026-09-04, bo build 0.2.19 powstał
> z drzewa roboczego, które nigdy nie trafiło do gita. Dwie konsekwencje, o których trzeba
> wiedzieć przed pracą tutaj:
>
> - **Oryginalnych testów 0.2.19 nie ma.** Bramka jakości obrazu zaliczyła 792 testy, ale zestawu
>   nie zachowano. Obecny `tests/` został przepisany pod odzyskane źródła i przechodzi w całości —
>   nie jest to jednak ten sam zestaw co przy buildzie, więc nie dowodzi zgodności z 0.2.19
>   w takim stopniu, w jakim dowodziłby oryginalny.
> - **Numeracja wersji śledzi tagi obrazu Docker**, nie `pyproject.toml`. Gdy jedno rozjedzie się
>   z drugim, obowiązuje tag obrazu.

## Obieg

- **Przebieg tygodniowy** (`RUN_WEEKDAY`/`RUN_HOUR`): wykrycie braków, prośba 1:1, zapis stanu.
- **Nasłuch** z adaptacyjnym backoffem (`scheduler/backoff.py`); obsłużona odpowiedź natychmiast
  wraca do odstępu bazowego.
- **Wygaszanie po dowodzie (ADR 0003):** temat zamyka się dopiero po UDANYM odczycie czatu, który
  nic nie przyniósł — przestój usługi ani awaria Graph nie wypalają cudzego okna odpowiedzi.
  Potwierdzenie w trakcie tygodnia docelowego zapisuje tę część tygodnia, która jeszcze przed
  nami; dni zakończone są odsiewane, żeby nie wpisywać do grafiku przeszłości.
- **Self-fill, pamięć rozmowy, dni urlopowe (ADR 0004):** wykrywanie, że pracownik sam uzupełnił
  Shifts (bot dziękuje zamiast dalej nagabywać); interpreter pamięta ostatnie wiadomości
  (odpowiedzi wieloturowe); częściowy urlop pomija tylko dni już objęte urlopem.
- **Podsumowanie dla administratorów** po KAŻDYM przebiegu — dead man's switch: w instalacji bez
  monitoringu brak tej wiadomości w piątek wieczorem jest jedynym sygnałem awarii. Dlatego
  `ADMIN_USER_IDS` to lista i powinny być w niej **co najmniej dwie** osoby.
- **Heartbeat sesji** (`HEARTBEAT_INTERVAL_H`) poza przebiegiem tygodniowym: bez niego utrata
  sesji w poniedziałek wyszłaby dopiero w piątek o 16:00.

## Uruchomienie na żywo

```bash
uv run --directory Powiadomienia_teams powiadomienia-teams --login    # jednorazowe logowanie (device-code)
uv run --directory Powiadomienia_teams powiadomienia-teams            # usługa: pętla tygodniowa + nasłuch
uv run --directory Powiadomienia_teams powiadomienia-teams --once     # jeden przebieg powiadomień i wyjście
uv run --directory Powiadomienia_teams powiadomienia-teams --poll-once # jedno sprawdzenie odpowiedzi i wyjście
uv run --directory Powiadomienia_teams powiadomienia-teams --stan     # raport stanu (bez sieci, bez blokady)
```

Dwa polecenia diagnostyczne warte osobnej uwagi:

- `--proba-nasluchu` — jeden obieg nasłuchu na ŻYWYM tenancie i modelu, z odciętymi metodami
  zapisu i wysyłki (nie flagą, tylko brakiem tych metod w kliencie). Pisze do osobnego pliku
  stanu `<state>-proba.json`.
  **Uwaga:** bierze blokadę na PRODUKCYJNYM pliku stanu, więc przy działającej usłudze kończy się
  „Inna instancja już działa". Żeby użyć jej bez przestoju, uruchom ją na kopii wolumenu stanu
  w osobnym kontenerze. Próba przy braku otwartych rozmów kończy się bez ani jednego zapytania do
  Graph — mówi to wtedy wprost i **nie jest dowodem sprawności ścieżki zapisu**.
- `--ignoruj-cisze` — pozwala `--once` pisać w godzinach ciszy (nadrabianie po awarii).

Realne działanie wymaga `POWIADOMIENIA_DRY_RUN=false`. Bez ważnego tokenu usługa nie wystartuje
(w terminalu poprosi o logowanie; bez terminala wychodzi z instrukcją `--login` — nie zawiesza się).

> **Uruchamiaj tylko JEDNĄ instancję.** Blokada pliku (`<state>.lock`) uniemożliwia równoczesny
> start drugiego procesu — chroni przed podwójnym zapisem do Shifts. Nieudany przebieg jest
> ponawiany (backoff), a start w oknie łaski po minionym terminie nadrabia zaległe powiadomienia
> (`POWIADOMIENIA_CATCHUP_GRACE_HOURS`). Godziny ciszy okna łaski NIE zjadają — przesuwają
> nadrabianie, a nie unieważniają je.

## Wdrożenie na serwer

Obowiązująca ścieżka: **[deploy/README-docker.md](deploy/README-docker.md)** — obraz Docker
budowany na serwerze z paczki źródłowej (`scripts/pack.sh` → scp → `scripts/build-image.sh`).

Konto »głosu« bota i lista odbiorców to **konfiguracja, nie kod**: konto = to, którym wykonasz
`--login`; odbiorcy = `POWIADOMIENIA_ONLY_USER_IDS` (zmiana + restart, bez przebudowy).
Identyfikatory AAD wypisze `scripts/lista_czlonkow.py`.

## Uruchamianie zadań deweloperskich

```bash
uv run --directory Powiadomienia_teams ruff check .    # lint
uv run --directory Powiadomienia_teams mypy            # typy
uv run --directory Powiadomienia_teams pytest -q       # testy
```

Wszystkie cztery bramki są zielone. **Liczb tu nie ma świadomie** — zmieniają się przy każdej
naprawie, więc byłyby rozjazdem z założenia; przelicz je `uv run pytest` i `uv run mypy`. Ta sama
zasada obowiązuje w korzeniowym `CLAUDE.md`, a pilnuje jej `tests/test_dokumentacja_bramek.py`.

**`xfail` w tym pod-projekcie jest opisem UDOKUMENTOWANEJ usterki, nie pominiętym testem.** Ma mieć
`strict=True` i w `reason` powód, po którym da się usterkę odtworzyć — wtedy naprawa zapala XPASS
i wymusza zdjęcie znacznika, więc nie da się jej przeoczyć. Dziś nie ma ani jednego: lista dziewięciu
usterek importu 0.2.19 została zamknięta (patrz CHANGELOG).

Od 2026-09-07 `src/` podlega WSZYSTKIM regułom, łącznie z `ruff format` i sufitem funkcji.
Wyłączenia były uzasadnione wiernością wobec obrazu 0.2.19; ta wierność żyje dziś w commicie
importu `f273dc2` (przed przepisaniem historii 2026-09-10: `7c7fe8c`), a u klienta stoi obraz
zbudowany z tego drzewa. Sześć funkcji przekraczających
sufit ma punktowe `noqa` z powodem — dług policzalny, nie hurtowe wyciszenie.

Testy biegają na atrapach, więc jedynym sprawdzeniem na ŻYWYM
tenancie jest `--proba-nasluchu` (z zastrzeżeniami wyżej) i `scripts/lista_czlonkow.py`
(czysty odczyt, weryfikuje sesję Graph).

## Konfiguracja

Pełna lista kluczy z wartościami domyślnymi i uzasadnieniem: **[deploy/env.example](deploy/env.example)**
— jedyne miejsce, w którym ta lista jest utrzymywana i weryfikowana wobec `config.py`.
Do uruchomienia lokalnego: `.env.example` → `.env` (tylko różnice wobec serwera).

Zmienne mają prefiks `POWIADOMIENIA_`. Rzeczy, o które najczęściej się potyka:

- **`DRY_RUN` domyślnie `true`.** Nic nie jest wysyłane ani zapisywane, dopóki nie ustawisz `false`.
  Wartość musi być rozpoznana — literówka (`fasle`, `prawda`, `"true"` w cudzysłowach) **zatrzymuje
  start** błędem konfiguracji, zamiast po cichu włączyć tryb na żywo.
- **`ONLY_USER_IDS` puste = WSZYSCY.** Dlatego zamiar zawężenia deklaruje się osobno przez
  `PILOTAZ=true`; włączony `PILOTAZ` przy pustej liście zatrzymuje start. Dwie zmienne muszą się
  zgadzać, bo literówka w nazwie listy zamienia pilotaż na wysyłkę do całego zespołu — bezgłośnie.
- **Termin odpowiedzi jest KALENDARZOWY**, nie „liczbą godzin ciszy": północ poniedziałku tygodnia
  docelowego + `REPLY_DEADLINE_OFFSET_H` (domyślnie 5 → poniedziałek 05:00), nie wcześniej niż
  `REPLY_MIN_HOURS` (24) od ostatniej prośby bota.
- **Godziny ciszy** `CISZA_OD_H` (20) / `CISZA_DO_H` (7) — kiedy bot nie pisze do pracowników.
  `RUN_HOUR` wpadający w ciszę jest konfiguracją legalną, ale usługa ostrzega o tym przy starcie.
- **`ALERT_WEBHOOK_URL` bywa sekretem** (token w URL-u) i **trafia do logów kontenera** — klient
  HTTP loguje URL żądania na poziomie INFO. Traktuj logi jak miejsce przechowywania tego sekretu.
  Przy `DRY_RUN=false` brak webhooka i brak jawnego `ALERTY_WYLACZONE=true` zatrzymuje start.
- **`LOGUJ_NAZWISKA` domyślnie `false`** — logi i alerty nazywają pracownika identyfikatorem, bo
  alert zostaje w kanale Teams bezterminowo i jest przeszukiwalny. Włączenie to świadome
  poszerzenie tego, co opuszcza instalację.

## Uprawnienia (Microsoft Graph, delegowane, admin consent)

`Schedule.Read.All`, `Schedule.ReadWrite.All`, `Chat.Create`, `Chat.ReadWrite`,
`ChatMessage.Send`, `TeamMember.Read.All`, `User.ReadBasic.All`. Logowanie jako
właściciel/kierownik zespołu (zapis zmian w Shifts jest menedżerski). Szczegóły i status
weryfikacji na żywo: [PLAN.md](PLAN.md).
