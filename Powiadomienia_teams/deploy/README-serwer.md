# Wdrożenie na Ubuntu 24.04 LTS — wariant ZAPASOWY (systemd, bez Dockera)

> **Obowiązująca ścieżka wdrożenia to [README-docker.md](README-docker.md).** Ten dokument opisuje
> wariant bez Dockera — na wypadek, gdyby Docker był na serwerze niedostępny lub zablokowany
> politykami. Oba warianty korzystają z tej samej paczki źródłowej (`scripts/pack.sh`) i tego samego
> pliku konfiguracji (`env.example`); różnią się wyłącznie sposobem uruchamiania procesu.

Instrukcja operatora: od tarballa do działającej usługi systemd.

Bot pisze do pracowników **jako konkretny człowiek** (uwierzytelnianie delegowane, nie app-only).
Konto, którym wykonasz `--login` w kroku 5, będzie widoczne jako nadawca wiadomości i jako autor
zapisów w grafiku. Musi być **właścicielem/kierownikiem zespołu** — zapis do Shifts jest menedżerski.

## Wymagania

- Ubuntu 24.04 LTS, dostęp SSH z `sudo`
- Wyjście na `login.microsoftonline.com`, `graph.microsoft.com`, `api.anthropic.com`
- Klucz API Anthropic
- Konto AAD z rolą właściciela w zespole Teams

---

## 1. Rozpakowanie i użytkownik systemowy

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin powiadomienia

sudo mkdir -p /opt/teams-shifts-reminder
sudo tar -xzf /tmp/powiadomienia-teams-*.tar.gz -C /tmp
sudo cp -r /tmp/powiadomienia-teams/. /opt/teams-shifts-reminder/
sudo chown -R root:root /opt/teams-shifts-reminder
sudo chmod -R 755 /opt/teams-shifts-reminder
```

Kod należy do `root`, usługa biegnie jako `powiadomienia` i ma do niego dostęp tylko do odczytu —
skompromitowany proces nie podmieni własnego kodu.

## 2. Środowisko Pythona

```bash
sudo curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
cd /opt/teams-shifts-reminder
sudo uv sync --locked --extra agent
```

`--locked` odtwarza wersje z `uv.lock` co do commita — bez tego wdrożenie mogłoby dostać inne
zależności niż przetestowane.

**`--extra agent` jest obowiązkowe.** Pakiet `anthropic` jest formalnie opcjonalny, ale kod zawsze
buduje klienta Claude, a import jest leniwy: bez tego extra usługa wystartuje bez błędu i padnie
dopiero przy pierwszej odpowiedzi pracownika.

## 3. Konfiguracja

```bash
sudo mkdir -p /etc/powiadomienia-teams
sudo cp /opt/teams-shifts-reminder/deploy/env.example /etc/powiadomienia-teams/env
sudo chown root:powiadomienia /etc/powiadomienia-teams/env
sudo chmod 640 /etc/powiadomienia-teams/env
sudo nano /etc/powiadomienia-teams/env      # uzupełnij ANTHROPIC_API_KEY
```

Zostaw na razie `POWIADOMIENIA_DRY_RUN=true` — na żywo przełączysz w kroku 6.

`640` + grupa usługi: plik zawiera klucz API, więc czyta go tylko root i proces usługi.

> **Nie kładź `.env` w `/opt/teams-shifts-reminder/`.** Konfiguracja przychodzi z `EnvironmentFile`;
> dodatkowy `.env` tylko zaciemniałby, skąd bierze się dana wartość.

## 4. Instalacja usługi

```bash
sudo cp /opt/teams-shifts-reminder/deploy/powiadomienia-teams.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start powiadomienia-teams    # utworzy /var/lib/powiadomienia-teams
sudo systemctl stop powiadomienia-teams     # zatrzymaj — najpierw logowanie
```

Pierwszy start utworzy katalog stanu (`StateDirectory`) z właściwymi prawami. Usługa zatrzyma się
z komunikatem o braku tokenu — to oczekiwane.

## 5. Jednorazowe logowanie (device-code)

**To jedyny krok wymagający obecności człowieka.** Loguje się właściciel konta kierowniczego,
a dostęp SSH ma administrator — dlatego procedura jest tak zbudowana, żeby hasło i MFA nigdy nie
przechodziły przez administratora.

Usługa musi być **zatrzymana** (`--login` omija blokadę jednej instancji i pisałby do cache
równolegle z działającym procesem).

```bash
sudo systemctl stop powiadomienia-teams

sudo -u powiadomienia env $(grep -v '^#' /etc/powiadomienia-teams/env | grep -v '^$' | xargs) \
    LANG=C.UTF-8 PYTHONUTF8=1 \
    /opt/teams-shifts-reminder/.venv/bin/powiadomienia-teams --login
```

Wypisze kod i adres `microsoft.com/devicelogin`. Kod jest krótkotrwały i jednorazowy — administrator
odczytuje go z terminala i przekazuje właścicielowi konta, który wpisuje go **na własnym urządzeniu**.

Weryfikacja:

```bash
sudo ls -l /var/lib/powiadomienia-teams/teams_token_cache.bin
# oczekiwane: -rw------- 1 powiadomienia powiadomienia
```

Sprawdź, czy zalogowało się właściwe konto i czy ma rolę `owner`:

```bash
sudo -u powiadomienia env $(grep -v '^#' /etc/powiadomienia-teams/env | grep -v '^$' | xargs) \
    /opt/teams-shifts-reminder/.venv/bin/python /opt/teams-shifts-reminder/scripts/lista_czlonkow.py
```

Skrypt wypisze wszystkich członków z ich AAD user-id, oznaczy zalogowane konto i ostrzeże, jeśli
nie jest właścicielem. **Stąd bierzesz identyfikatory do `POWIADOMIENIA_ONLY_USER_IDS`.**

## 6. Wejście na żywo

Kolejność jest istotna — każdy krok odblokowuje następny.

### 6a. Bramka na maszynie docelowej

```bash
cd /opt/teams-shifts-reminder && sudo uv run --no-sync pytest -q
```

Potwierdza m.in. gałąź POSIX blokady jednej instancji, która na Windows nigdy się nie wykonuje.

### 6b. Przebieg w dry-run

```bash
sudo -u powiadomienia env $(grep -v '^#' /etc/powiadomienia-teams/env | grep -v '^$' | xargs) \
    LANG=C.UTF-8 /opt/teams-shifts-reminder/.venv/bin/powiadomienia-teams --once
```

Log pokaże listę osób i **pełne treści** wiadomości, których nikt nie dostanie. To ostatni moment
na weryfikację listy odbiorców.

### 6c. Przełączenie na żywo

```bash
sudo nano /etc/powiadomienia-teams/env
#   POWIADOMIENIA_DRY_RUN=false
#   POWIADOMIENIA_ONLY_USER_IDS=<id z kroku 5>
```

Od tego momentu start bez `ANTHROPIC_API_KEY` albo bez `SCHEDULING_GROUP_ID` zakończy się
czytelnym błędem konfiguracji — celowo, zanim ktokolwiek dostanie wiadomość.

### 6d. Realna wysyłka pod nadzorem

```bash
sudo -u powiadomienia env $(grep -v '^#' /etc/powiadomienia-teams/env | grep -v '^$' | xargs) \
    LANG=C.UTF-8 /opt/teams-shifts-reminder/.venv/bin/powiadomienia-teams --once
```

Sprawdź: wiadomości dotarły w Teams, a stan zawiera wpisy `awaiting_reply` z niepustym watermarkiem:

```bash
sudo cat /var/lib/powiadomienia-teams/powiadomienia_state.json | python3 -m json.tool
```

### 6e. Test pełnego obiegu

Odpisz z konta testowego, potem:

```bash
sudo -u powiadomienia env $(grep -v '^#' /etc/powiadomienia-teams/env | grep -v '^$' | xargs) \
    LANG=C.UTF-8 /opt/teams-shifts-reminder/.venv/bin/powiadomienia-teams --poll-once
```

Powinna przyjść prośba o potwierdzenie z konkretnym grafikiem. Odpisz „tak" i powtórz — zmiana
ma pojawić się w Shifts. **To jedyny krok weryfikujący, że klucz Claude faktycznie działa.**

### 6f. Start usługi

```bash
sudo systemctl enable --now powiadomienia-teams
sudo journalctl -fu powiadomienia-teams
```

Szukaj w logu: `Następny przebieg powiadomień: <data najbliższego piątku 16:00>`.

---

## Runbook

### Usługa stoi, w logu „Utracono uwierzytelnienie"

Refresh-token wygasł (rolling ~90 dni) albo zadziałała polityka Conditional Access. To zachowanie
**zamierzone** — usługa zatrzymuje się czysto zamiast udawać, że działa.

Powtórz krok 5, potem `sudo systemctl start powiadomienia-teams`.

> Zaplanuj ponowne logowanie **co ~80 dni**, zanim token wygaśnie sam.

### Zmiana listy odbiorców

```bash
sudo nano /etc/powiadomienia-teams/env       # POWIADOMIENIA_ONLY_USER_IDS
sudo systemctl restart powiadomienia-teams
```

Bez ponownego wdrożenia. Identyfikatory: `scripts/lista_czlonkow.py`.

### Zmiana konta bota

Zatrzymaj usługę, usuń cache tokenu, zaloguj nowe konto:

```bash
sudo systemctl stop powiadomienia-teams
sudo rm /var/lib/powiadomienia-teams/teams_token_cache.bin
# ...krok 5 z nowym kontem...
sudo systemctl start powiadomienia-teams
```

Stan pendingów zostaje — otwarte rozmowy będą kontynuowane z nowego konta.

### Nowa wersja

```bash
sudo tar -xzf /tmp/powiadomienia-teams-<nowa>.tar.gz -C /tmp
sudo cp -r /tmp/powiadomienia-teams /opt/teams-shifts-reminder-<data>
cd /opt/teams-shifts-reminder-<data> && sudo uv sync --locked --extra agent
sudo uv run --no-sync pytest -q                       # bramka przed przełączeniem
sudo systemctl stop powiadomienia-teams
sudo mv /opt/teams-shifts-reminder /opt/teams-shifts-reminder-poprzednia
sudo mv /opt/teams-shifts-reminder-<data> /opt/teams-shifts-reminder
sudo systemctl start powiadomienia-teams
```

Stan i cache leżą w `/var/lib`, więc przeżywają podmianę. **Rollback** = zamiana katalogów z powrotem.

### Monitoring

Bez tego zatrzymana usługa jest niewidoczna do następnego piątku:

```bash
systemctl is-active powiadomienia-teams
journalctl -u powiadomienia-teams --since "7 days ago" -p warning
```

Docelowo warto podpiąć `OnFailure=` z jednostką powiadamiającą.

### Diagnostyka błędów Graph

Treść odpowiedzi Graph trafia do logu na poziomie ERROR — tam jest prawdziwa przyczyna:

```bash
sudo journalctl -u powiadomienia-teams -p err --since today
```

- **401** → usługa zatrzyma się z instrukcją `--login` (patrz wyżej)
- **403** → konto straciło rolę właściciela albo cofnięto zgodę admina; komunikat wskaże, którego
  uprawnienia brakuje. Nie jest ponawiany — ponawianie nic by nie dało.

---

## Znane ograniczenia

Świadomie pozostawione, udokumentowane zamiast naprawiane:

| Objaw | Przyczyna | Obejście |
|---|---|---|
| Pętla stoi kilkanaście minut | Brak timeoutu klienta Anthropic (domyślnie 10 min × 2 próby) | Zwykle mija samo; przy nawrotach `systemctl restart` |
| Bardzo długa cisza przy throttlingu | `Retry-After` z Graph bez górnego limitu (może wynieść 3600 s) | Jak wyżej |
| Stan zniknął po awarii zasilania | Uszkodzony plik jest nadpisywany bez kopii i bez `fsync` | Kopiuj `powiadomienia_state.json` przed każdą aktualizacją |
| Pracownik nie dostał prośby o potwierdzenie | Nieudana wysyłka nie jest ponawiana; status już zmieniony | Napisz do niego ręcznie |
| Odpowiedź zignorowana bez komunikatu | Model zwrócił JSON poprawny składniowo, ale złego kształtu | Poproś pracownika o prostszą odpowiedź |
| Fałszywe prośby po ~roku pracy | Paginacja odczytu zmian ucięta na 50 stronach, po cichu | Wymaga poprawki kodu, gdy zespół urośnie |
