# Wdrożenie jako obraz Docker

Docelowo usługa działa jako **kontener**. Obraz budowany jest **na serwerze** z paczki źródłowej,
więc serwer potrzebuje Dockera i dostępu do sieci; nie potrzebuje Pythona ani `uv`.

Bot pisze do pracowników **jako konkretny człowiek** (uwierzytelnianie delegowane, nie app-only).
Konto, którym wykonasz `--login` w kroku 4, będzie widoczne jako nadawca wiadomości i jako autor
zapisów w grafiku. Musi być **właścicielem/kierownikiem zespołu** — zapis do Shifts jest menedżerski.

## Wymagania serwera

- Ubuntu 24.04 LTS, dostęp SSH z `sudo`
- Docker Engine z wtyczką `compose`
- Dostęp do `docker.io`, `ghcr.io` i PyPI **na czas budowania**
- Dostęp do `login.microsoftonline.com`, `graph.microsoft.com`, `api.anthropic.com` **na stałe**
- Klucz API Anthropic, konto AAD z rolą właściciela w zespole

---

## Wysłanie źródeł

Na maszynie deweloperskiej:

```bash
cd Powiadomienia_teams
bash scripts/pack.sh
scp ../powiadomienia-teams-*.tar.gz uzytkownik@serwer:/tmp/
```

Paczka zawiera `Dockerfile`, kod, `uv.lock`, testy i pliki wdrożeniowe. **Nie zawiera `.env`** —
skrypt to sprawdza i przerywa, gdyby sekret się tam znalazł.

## 1. Budowanie obrazu na serwerze

```bash
tar -xzf /tmp/powiadomienia-teams-*.tar.gz -C /tmp
cd /tmp/powiadomienia-teams
bash scripts/build-image.sh
```

**Testy biegną w trakcie budowania** — obraz nie powstanie, jeśli któryś padnie. To celowe:
weryfikacja odbywa się na tym Pythonie i tej architekturze, na których obraz faktycznie pojedzie.
W szczególności potwierdza gałąź POSIX `fcntl` w blokadzie jednej instancji, której na Windows
nie da się uruchomić.

Skrypt po zbudowaniu sprawdza jeszcze: wersję Pythona, użytkownika (`10001`, nie root), obecność
SDK, kodowanie UTF-8, dostępność strefy `Europe/Warsaw` oraz to, że przy pustej konfiguracji
program kończy się **czytelnym błędem walidacji**, a nie stacktrace'em.

Po zbudowaniu źródła w `/tmp` można usunąć — obraz jest samowystarczalny.

> **Przenoszenie obrazu gdzie indziej.** Gdyby serwer nie miał dostępu do internetu, zbuduj obraz
> na innej maszynie linux/amd64 z `EKSPORT=1 bash scripts/build-image.sh`, przenieś powstały plik
> i wgraj go przez `gunzip -c ...-image.tar.gz | docker load`.

## 2. Układ katalogów

```bash
sudo mkdir -p /opt/teams-shifts-reminder
sudo cp /tmp/powiadomienia-teams/deploy/docker-compose.yml /opt/teams-shifts-reminder/
sudo cp /tmp/powiadomienia-teams/deploy/env.example /opt/teams-shifts-reminder/env
sudo chmod 640 /opt/teams-shifts-reminder/env
sudo nano /opt/teams-shifts-reminder/env       # uzupełnij ANTHROPIC_API_KEY
```

Zostaw na razie `POWIADOMIENIA_DRY_RUN=true` — na żywo przełączysz w kroku 5.

`640` na pliku `env`: zawiera klucz API. **Nigdy nie wpisuj go do `Dockerfile` ani do obrazu** —
warstwy obrazu są nieusuwalne i wędrują razem z nim.

> Ścieżki stanu (`POWIADOMIENIA_TOKEN_CACHE`, `POWIADOMIENIA_STATE_PATH`) są już ustawione
> w obrazie i wskazują na wolumen. W pliku `env` zostawione jawnie dla czytelności — wartości
> muszą pozostać zgodne z punktem montowania wolumenu.

## 3. Pierwszy start (utworzenie wolumenu)

```bash
cd /opt/teams-shifts-reminder
docker compose up -d
docker compose logs
docker compose stop
```

Usługa zatrzyma się z komunikatem o braku tokenu — to oczekiwane. Chodziło o utworzenie wolumenu
`powiadomienia-teams-stan` z właściwymi prawami.

> **Wolumen jest krytyczny.** Trzyma cache tokenu i stan pendingów. Bez niego każdy restart
> zaczynałby od pustego stanu: otwarte rozmowy przepadają, a najbliższy przebieg wysyła prośby
> **drugi raz** do tych samych osób — idempotencja opiera się wyłącznie na tym pliku.

## 4. Jednorazowe logowanie (device-code)

**To jedyny krok wymagający obecności człowieka.** Loguje się właściciel konta kierowniczego,
a dostęp SSH ma administrator — procedura jest tak zbudowana, żeby hasło i MFA nigdy nie
przechodziły przez administratora.

Usługa musi być **zatrzymana**: `--login` omija blokadę jednej instancji i pisałby do cache
równolegle z działającym procesem.

```bash
cd /opt/teams-shifts-reminder
docker compose stop
docker compose run --rm -it powiadomienia --login
```

`run --rm -it` używa tego samego wolumenu i tej samej konfiguracji co usługa, więc token wyląduje
dokładnie tam, gdzie usługa będzie go szukać. `-it` jest konieczne — bez terminala kod świadomie
odmawia device-code zamiast zawiesić się w oczekiwaniu.

Wypisze kod i adres `microsoft.com/devicelogin`. Kod jest krótkotrwały i jednorazowy — administrator
odczytuje go z terminala i przekazuje właścicielowi konta, który wpisuje go **na własnym urządzeniu**.

Weryfikacja — kto się zalogował i czy ma rolę właściciela:

```bash
docker compose run --rm --entrypoint python powiadomienia /app/scripts/lista_czlonkow.py
```

Skrypt wypisze wszystkich członków z ich AAD user-id, oznaczy zalogowane konto i ostrzeże, jeśli
nie jest właścicielem. **Stąd bierzesz identyfikatory do `POWIADOMIENIA_ONLY_USER_IDS`.**

## 5. Wejście na żywo

### 5a. Przebieg w dry-run

```bash
docker compose run --rm powiadomienia --once
```

Log pokaże listę osób i **pełne treści** wiadomości, których nikt nie dostanie. To ostatni moment
na weryfikację listy odbiorców.

### 5b. Przełączenie

```bash
sudo nano /opt/teams-shifts-reminder/env
#   POWIADOMIENIA_DRY_RUN=false
#   POWIADOMIENIA_ONLY_USER_IDS=<id z kroku 4>
```

Od tego momentu start bez `ANTHROPIC_API_KEY` albo bez `SCHEDULING_GROUP_ID` kończy się czytelnym
błędem konfiguracji — celowo, zanim ktokolwiek dostanie wiadomość.

### 5c. Realna wysyłka pod nadzorem

```bash
docker compose run --rm powiadomienia --once
docker run --rm -v powiadomienia-teams-stan:/s alpine \
    cat /s/powiadomienia_state.json
```

Sprawdź: wiadomości dotarły w Teams, a stan zawiera wpisy `awaiting_reply` z niepustym watermarkiem.

### 5c-bis. Próba nasłuchu na żywym tenancie (bez skutków)

`--proba-nasluchu` przechodzi jeden obieg nasłuchu na PRAWDZIWYM Graphie i modelu, mając odcięte
metody zapisu i wysyłki — obietnica „nic nie wyjdzie" stoi na braku tych metod w kliencie, nie na
fladze. Pisze do osobnego pliku stanu `<state>-proba.json`.

Dwa zastrzeżenia, bez których ten krok wprowadza w błąd:

1. **Bierze blokadę na PRODUKCYJNYM pliku stanu**, mimo że do niego nie pisze. Przy działającej
   usłudze kończy się `Inna instancja już działa`. Żeby uruchomić ją bez przestoju, zrób to na
   kopii wolumenu:

   ```bash
   docker volume create proba-stan-tmp
   docker run --rm -v powiadomienia-teams-stan:/src:ro -v proba-stan-tmp:/dst alpine \
     sh -c 'cp -a /src/. /dst/ && rm -f /dst/powiadomienia_state.json.lock'
   docker run --rm --env-file /opt/teams-shifts-reminder/env \
     -v proba-stan-tmp:/var/lib/powiadomienia-teams \
     powiadomienia-teams:0.2.19 --proba-nasluchu
   docker volume rm proba-stan-tmp
   ```

2. **Brak otwartych rozmów = próba niczego nie dowodzi.** Kończy się wtedy bez ani jednego
   zapytania do Graph i mówi to wprost. Sesję Graph sprawdza wtedy osobno czysty odczyt:
   `docker run --rm --env-file /opt/teams-shifts-reminder/env -v powiadomienia-teams-stan:/var/lib/powiadomienia-teams \
   --entrypoint python powiadomienia-teams:0.2.19 /app/scripts/lista_czlonkow.py`

### 5d. Test pełnego obiegu

Odpisz z konta testowego, potem:

```bash
docker compose run --rm powiadomienia --poll-once
```

Powinna przyjść prośba o potwierdzenie z konkretnym grafikiem. Odpisz „tak" i powtórz — zmiana
ma pojawić się w Shifts. **To jedyny krok weryfikujący, że klucz Claude faktycznie działa.**

### 5e. Start usługi

```bash
docker compose up -d
docker compose logs -f
```

Szukaj w logu: `Następny przebieg powiadomień: <data najbliższego piątku 16:00>`.

---

## Runbook

### Alert „Utracono uwierzytelnienie"

Powtórz krok 4. **Nie musisz nic więcej robić** — `restart: unless-stopped` sam podniesie usługę,
gdy tylko token wróci. W logu i w alercie jest kod AADSTS, który mówi, co dokładnie się stało:

| Kod | Przyczyna | Co zrobić |
|---|---|---|
| `AADSTS50173` | Zmieniono lub zresetowano hasło konta bota | `--login`; rozważ konto passwordless |
| `AADSTS50078`, `70043` | Polityka sign-in frequency | `--login`; poproś admina o wyłączenie SIF dla tej aplikacji |
| `AADSTS530036` | Conditional Access blokuje device code flow | **Token nie do odzyskania** — potrzebna zmiana polityki |
| `AADSTS65001` | Cofnięto zgodę administratora | Ponowna zgoda na uprawnienia aplikacji |
| `AADSTS700082` | Token wygasł z bezczynności | Usługa nie działała >90 dni; `--login` |

> **Nie ma potrzeby cyklicznego przelogowywania.** Wbrew powszechnemu przekonaniu 90 dni to okno
> **bezczynności**, a nie maksymalny wiek tokenu (`MaxAgeSingleFactor` = `until-revoked`).
> Refresh-token rotuje się przy każdym użyciu, więc przy cotygodniowym cyklu żyje bezterminowo.
> Ponowne logowanie jest reakcją na zdarzenie w tenancie, nie zadaniem w kalendarzu.

### Nie przyszło cotygodniowe podsumowanie

To **główny sygnał awarii** w instalacji bez monitoringu. Usługa wysyła podsumowanie po każdym
przebiegu, także gdy nikogo nie trzeba było zagadnąć — cisza oznacza więc, że nie żyje.

```bash
docker compose ps          # zdrowy? zatrzymany?
docker compose logs --tail 100
```

### Zmiana listy odbiorców

```bash
sudo nano /opt/teams-shifts-reminder/env      # POWIADOMIENIA_ONLY_USER_IDS
docker compose up -d --force-recreate
```

Bez przebudowy obrazu. Identyfikatory: `lista_czlonkow.py` (krok 4).

### Zmiana konta bota

```bash
docker compose stop
docker run --rm -v powiadomienia-teams-stan:/s alpine \
    rm -f /s/teams_token_cache.bin
docker compose run --rm -it powiadomienia --login    # nowe konto
docker compose up -d
```

Stan pendingów zostaje — otwarte rozmowy będą kontynuowane z nowego konta.

### Nowa wersja

```bash
tar -xzf /tmp/powiadomienia-teams-<nowa>.tar.gz -C /tmp
cd /tmp/powiadomienia-teams
WERSJA=0.3.0 bash scripts/build-image.sh          # testy muszą przejść, żeby obraz powstał

sudo sed -i 's/powiadomienia-teams:0.2.0/powiadomienia-teams:0.3.0/' \
    /opt/teams-shifts-reminder/docker-compose.yml
cd /opt/teams-shifts-reminder && docker compose up -d
```

Wolumen ze stanem i tokenem przeżywa podmianę. **Rollback** = wpisanie poprzedniej wersji
i ponowne `up -d`; stary obraz zostaje na serwerze, dopóki go nie usuniesz — dlatego nie usuwaj
poprzedniego tagu, dopóki nowa wersja nie przepracuje jednego pełnego cyklu tygodniowego.

### Kopia zapasowa stanu

```bash
docker run --rm -v powiadomienia-teams-stan:/s -v "$PWD":/kopia alpine \
    tar czf /kopia/stan-$(date +%F).tar.gz -C /s .
```

Warto zrobić przed każdą aktualizacją — uszkodzony plik stanu jest nadpisywany bez kopii
(znane ograniczenie poniżej).

### Monitoring

Usługa sama się zgłasza — ręczne zaglądanie jest już tylko uzupełnieniem:

| Kanał | Kiedy | Po co |
|---|---|---|
| Podsumowanie na Teams | po każdym przebiegu | **dead man's switch** — brak wiadomości = awaria |
| Webhook | start, utrata sesji, nieudany przebieg, nieudany puls | alert niezależny od Graph |
| `HEALTHCHECK` | co 5 min | `docker compose ps` pokazuje `healthy`/`unhealthy`, nie samo „Up" |
| Puls sesji | co 24 h | utrata sesji wychodzi w dobę, nie dopiero w piątek o 16:00 |

Wymaga ustawienia `POWIADOMIENIA_ADMIN_USER_ID` i `POWIADOMIENIA_ALERT_WEBHOOK_URL` — bez nich
usługa działa, ale milczy.

```bash
docker compose ps
docker compose logs --since 168h | grep -Ei 'error|critical'
```

> Docker **nie restartuje** kontenera oznaczonego `unhealthy` (robi to dopiero Swarm). Healthcheck
> daje widoczność, nie samoleczenie — przed zawieszeniem chronią timeouty w klientach Graph
> i Anthropica.

### Diagnostyka błędów Graph

Treść odpowiedzi Graph trafia do logu na poziomie ERROR — tam jest prawdziwa przyczyna:

- **401** → kontener zatrzyma się z instrukcją `--login`
- **403** → konto straciło rolę właściciela albo cofnięto zgodę admina; komunikat wskaże, którego
  uprawnienia brakuje. Nie jest ponawiany — ponawianie nic by nie dało.

---

## Decyzje w obrazie (dlaczego tak)

| Decyzja | Powód |
|---|---|
| Testy w trakcie budowania | Weryfikacja na docelowym Pythonie i architekturze; gałąź POSIX `fcntl` nie działa na Windows |
| `--extra agent` obowiązkowe | `anthropic` jest formalnie opcjonalne, ale import jest leniwy — brak ujawniłby się dopiero przy pierwszej odpowiedzi |
| `USER 10001`, kod jako root | Skompromitowany proces nie podmieni własnego kodu |
| `read_only: true` + tmpfs | Zapisywalny jest wyłącznie wolumen stanu |
| `tini` jako PID 1 | Przekazuje SIGTERM, więc `docker stop` kończy pętlę czysto zamiast SIGKILL po 10 s |
| `LANG=C.UTF-8`, `PYTHONUTF8=1` | Emoji w wiadomościach i polskie znaki w cache MSAL; bez tego `UnicodeEncodeError` |
| `restart: unless-stopped` | Usługa ma wracać po reboocie hosta bez człowieka; utrata sesji jest zgłaszana alertem, a nie ukrywana martwym kontenerem |
| Opóźnienie przed wyjściem | Przy `unless-stopped` martwy token dawałby restart co sekundę — 10 min zamienia to w spokojne czekanie na `--login` |
| `HEALTHCHECK` po pliku pulsu | „Up" nie znaczy „działa"; puls pokazuje, kiedy pętla naprawdę ostatnio się obudziła |
| Webhook alertów poza AAD | Alert „utracono sesję" powstaje wtedy, gdy Teams przestaje być dostępnym kanałem |
| Brak `VOLUME` w Dockerfile | Nazwany wolumen w compose zamiast anonimowych, które narastają przy każdym `docker run` |

## Znane ograniczenia

Świadomie pozostawione, udokumentowane zamiast naprawiane:

| Objaw | Przyczyna | Obejście |
|---|---|---|
| Zmiana przez północ znika | `22:00–06:00` traci informację o przejściu doby i jest po cichu pomijana | Nie dotyczy tego zespołu (brak nocek); wymaga poprawki kodu, gdy się pojawią |
| Pracownik nie dostał prośby o potwierdzenie | Nieudana wysyłka nie jest ponawiana; status już zmieniony | Napisz do niego ręcznie |
| `--login` przy działającej usłudze | Omija blokadę jednej instancji i pisze do cache równolegle | Zawsze `docker compose stop` przed logowaniem (krok 4) |
| Wpis zniknął, a pracownik nie dostał ŻADNEJ wiadomości | Odczyt czatu padł w serii kolejnych obiegów, a od ostatniej aktywności minęło ponad `SUFIT_WPISU_BEZ_ODCZYTU_H` (144 h) — wpis zamknięto po cichu (ADR 0007). Same 144 h nie wystarczą: pojedyncza awaria odczytu wpisu NIE zamyka | To zamierzone: nie wiemy, czy odpisał, więc nie wolno mu zarzucić milczenia. Alert „Przypomnienia zablokowane na odczycie czatu" mówi kogo dotyczy; grafik ustal z tymi osobami ręcznie |

**Nie szukaj wysłanego domknięcia w ostatnim przypadku — nie ma go i nie powinno być.** Wcześniej
(do 0.2.19 włącznie) taki wpis nie znikał wcale: wisiał otwarty bez końca, blokując przypomnienie
tej osoby w każdym kolejnym tygodniu, a jedynym śladem był `logger.exception` w logu kontenera.
Operator dostaje teraz alert po trzech obiegach bez odczytu, czyli na długo przed sufitem;
kolumna `bez odcz.` w `--stan` pokazuje, ile obiegów stoi za każdym wpisem.

Naprawione w 0.2.0, wcześniej wymienione jako ograniczenia: brak timeoutu Anthropica,
`Retry-After` bez sufitu, stan nadpisywany bez kopii, ciche ucięcie paginacji, JSON złego kształtu
kończący się pętlą zamiast prośbą o doprecyzowanie.

Naprawione w **0.2.1** (uzasadnienie: ADR 0003 `docs/adr/0003-expiry-requires-evidence.md`
w repozytorium — paczka wdrożeniowa nie zawiera katalogu `docs/`): po przestoju
dłuższym niż okno odpowiedzi (np. utrata sesji przez weekend) pierwszy przebieg po powrocie wysyłał
pracownikowi prośbę o potwierdzenie i zaraz po niej „Nie dostałem odpowiedzi", a temat zamykał
terminalnie — jego „tak" nie było już nigdy czytane. Ta sama wada zamieniała awarię odczytu czatu
w ciche wygaszenie. Wygaszenie wymaga teraz DOWODU: udanego odczytu, który nic nie przyniósł.
Potwierdzenie w trakcie tygodnia docelowego zapisuje tę część tygodnia, która jeszcze przed nami —
dni już zakończone są odsiewane, a gdy nie zostaje nic, temat domyka własny, prawdziwy komunikat
zamiast „zapisałem" albo „nie dostałem odpowiedzi". Osoba, która odpisała, ale nie potwierdziła,
też ma odtąd własny komunikat. W podsumowaniu dla administratora rubryka „wygasłe bez odpowiedzi"
nazywa się teraz **„zamknięte bez zapisu"** — obejmuje trzy różne powody, więc stara etykieta
wprowadzała w błąd przy decyzji, do kogo napisać ręcznie.

> `POWIADOMIENIA_SEND_EXPIRY_MESSAGE=false` wycisza komunikaty tematów, które gasną **same**
> (brak odpowiedzi, brak potwierdzenia). Nie wycisza odpowiedzi na jawne „tak" pracownika —
> także wtedy, gdy odpowiedzią jest „nie ma już czego zapisać".
