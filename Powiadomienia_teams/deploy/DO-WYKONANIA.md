# Do wykonania, zanim obraz zadziała na serwerze

Stan na 2026-07-21. Obraz `powiadomienia-teams:0.2.0` jest **zbudowany i przetestowany** (236 testów
w środku obrazu, przebieg próbny przeciwko prawdziwemu Graphowi przeszedł). Poniższe punkty to
wyłącznie to, czego brakuje **po stronie tenanta i konfiguracji** — nie kodu.

Kolejność ma znaczenie: krok 2 nie da się wykonać bez 1, a 5 bez 3.

---

## Tożsamość — co bot faktycznie potrzebuje (i czego NIE)

| Potrzebne | Skąd |
|---|---|
| `CLIENT_ID` = `91643a50-…` | rejestracja aplikacji **tego** bota |
| `TENANT_ID` = `99b17207-…` | tenant BIAP |
| jednorazowe `--login` | device-code wykonany przez człowieka |

**Hasło konta bota NIE jest częścią konfiguracji i nigdy nie powinno tu trafić.** Bot uwierzytelnia
się refresh-tokenem z cache MSAL, zakładanym raz przez device-code. Hasło jest potrzebne wyłącznie
człowiekowi w przeglądarce, w momencie logowania.

**Nie mylić z TeamsDriverem.** Konfiguracja TeamsDrivera używa `TEAMS_CLIENT_ID=414e4548-…` — to
INNA rejestracja aplikacji, z własnymi zgodami i uprawnieniami. Wspólny jest tylko tenant.
Podstawienie tamtego `CLIENT_ID` pod bota zmieniłoby tożsamość aplikacji (a więc zakres zgód),
a nie tożsamość nadawcy wiadomości — tę ustala wyłącznie to, kto wykonał `--login`.

## Zespół docelowy — USTALONY I ZWERYFIKOWANY (2026-07-21)

```
BIAP - Pion Inteligentnych Technologii
  team id:  c0ffee00-0000-4000-8000-000000000007
  grafik:   enabled=True   Europe/Warsaw   tydzień od poniedziałku   1990 zmian
  grupa:    TAG_bb093836-…  „Pion Inteligentnych Technologii"  (AKTYWNA)
  druga:    TAG_bc1bef85-…  „… - Staż"                          (isActive=false — NIE używać)
  skład:    9 osób, konto bota jest WŁAŚCICIELEM
```

To jedyny zespół w tenancie z działającym grafikiem. **BIAP-PI i pozostałe 78 zespołów konta bota
mają `provisionStatus: NotStarted`** — odczyt zmian zwraca tam 404. Zespół Stażyści ma grafik, ale
konta bota w nim nie ma.

Zweryfikowane end-to-end na pustym tygodniu 2026-09-07: zapis trzech zmian ścieżką bota
(`create_shift`), odczyt zwrotny, poprawne wykrycie luk przez `members_without_shifts`, usunięcie
(3 × HTTP 204) i kontrola, że grafik wrócił do stanu sprzed testu.

> **Uwaga o roli.** Zapis wykonało konto bota będące **właścicielem** tego zespołu. Czy sam
> `member` wystarczy — pozostaje niesprawdzone; ten test tego nie rozstrzyga.

## Blokery — bez nich usługa nie ruszy

### 1. ~~Utworzyć grafik Shifts~~ — ZROBIONE

Grafik istnieje, jest włączony, ma poprawną strefę i tydzień od poniedziałku.

### 2. ~~Uzupełnić `POWIADOMIENIA_SCHEDULING_GROUP_ID`~~ — ZROBIONE

`TAG_c0ffee00-0000-4000-8000-000000000021`. Wpisane w `deploy/env.example` i w lokalnym `.env`.
Wybrana grupa **aktywna** — druga w tym grafiku ma `isActive=false` i zapisy trafiałyby donikąd.

### 3. Zalogować się jako Virtual WorkMate  ▸ człowiek z dostępem do tego konta

```bash
cd /opt/teams-shifts-reminder
docker compose stop
docker run --rm -v powiadomienia-teams-stan:/s alpine rm -f /s/teams_token_cache.bin
docker compose run --rm -it powiadomienia --login
```

**Kasowanie starego cache jest warunkiem poprawności, nie porządkiem.** `graph/auth.py:140` wybiera
konto przez `accounts[0]`, bez sprawdzania kto to jest — dwa konta w jednym pliku i bot może
odezwać się niewłaściwą tożsamością, po cichu.

Weryfikacja, kto faktycznie jest głosem bota:

```bash
docker compose run --rm --entrypoint python powiadomienia /app/scripts/lista_czlonkow.py
```

### 4. ~~Potwierdzić prawo zapisu grafiku~~ — ZROBIONE

Konto bota utworzyło i usunęło trzy zmiany w tym grafiku (2026-07-21). Zapis, odczyt i usunięcie
działają. Zastrzeżenie z sekcji wyżej: bot jest tam właścicielem, więc scenariusz „tylko member"
pozostaje niesprawdzony.

### 5. Ustawić `ANTHROPIC_API_KEY` na serwerze  ▸ człowiek

Wymagany przy `DRY_RUN=false` — bez niego bot wysyłałby prośby, na które nigdy by nie odpowiedział.
Walidacja zatrzymuje start (bramka B1). Plik `env` musi mieć `chmod 640`.

---

## Ważne, choć nieblokujące

### 6. `POWIADOMIENIA_ADMIN_USER_ID` i `POWIADOMIENIA_ALERT_WEBHOOK_URL`

Bez nich usługa **działa, ale milczy**: nie ma cotygodniowego podsumowania (dead man's switch) ani
alertu o utracie sesji. Zatrzymany kontener pozostaje wtedy niewidoczny do następnego piątku.

Webhook musi być **niezależny od AAD** — alert „utracono sesję" powstaje dokładnie wtedy, gdy Teams
przestaje być dostępnym kanałem. Sprawdzić dostępność Workflows (Power Automate) w tenancie;
Microsoft wycofuje klasyczne Office 365 connectors.

### 7. Zawęzić uprawnienia rejestracji aplikacji

Token niesie **25 uprawnień**, a kod prosi o 8. Nadmiarowe m.in. `Channel.Delete.All`,
`Files.ReadWrite.All`, `Sites.Read.All`. Bot ich nie używa, ale wyciek cache tokenu dałby
atakującemu kasowanie kanałów i dostęp do plików. Zadanie dla administratora aplikacji.

---

## Weryfikacja na serwerze — kolejność

8. `docker compose run --rm powiadomienia --once` przy `DRY_RUN=true` → w logu 9 osób z BIAP-PI
   i pełne treści wiadomości. Ostatni moment na sprawdzenie listy odbiorców.
9. Przełączyć `DRY_RUN=false`, powtórzyć `--once` **pod nadzorem** → realna wysyłka. Tu ujawni się
   ewentualny brak uprawnień menedżerskich z kroku 4.
10. Odpisać z konta testowego, `--poll-once` → prośba o potwierdzenie, potem „tak" → wpis w Shifts.
    **To jedyna ścieżka weryfikująca klucz Anthropic.**
11. `docker compose up -d`, w logu `Następny przebieg powiadomień: <piątek 16:00>`.
12. `docker compose ps` → `healthy` po pierwszej pobudce (do 3 min).
13. **`sudo reboot`** → kontener wstaje sam. Jedyny sprawdzian `restart: unless-stopped`;
    nie da się go zasymulować lokalnie.

---

## Pułapki środowiskowe

| Rzecz | Uwaga |
|---|---|
| `POWIADOMIENIA_TOKEN_CACHE` | Lokalnie przestawiony na plik Virtual WorkMate. **Na serwerze nieistotne** — ścieżkę narzuca `Dockerfile` (`/var/lib/powiadomienia-teams/…`), a `env` ją tylko powtarza dla czytelności |
| Wolumen `powiadomienia-teams-stan` | Trzyma cache tokenu i stan. Bez niego każdy restart wysyła prośby **drugi raz** — idempotencja opiera się wyłącznie na tym pliku |
| Konto bota w składzie zespołu | Jest pełnoprawnym członkiem, więc bez filtra trafiało na listę „bez grafiku". `run_once` odfiltrowuje je po `get_me()`, niezależnie od `ONLY_USER_IDS` |
| Rozmiar grafiku | 1990 zmian, 2 strony po ~995. Limit `_MAX_PAGES=50` ≈ **49 750 zmian** — przy ~2000/rok zapas na ok. 25 lat. Odczyt całości: ~3,5 s |
| `$filter` po stronie Graph | **Nie działa** dla zakresu dat: `startDateTime ge … and startDateTime lt …` → HTTP 400 („property allowed at most once"). Filtrowanie zostaje po stronie klienta |
| Zmiana wersji obrazu | `WERSJA=0.2.0 bash scripts/build-image.sh`, potem podmiana tagu w `docker-compose.yml`. Etykieta obrazu jest sprawdzana wobec tagu |

## Stan lokalnej konfiguracji (`Powiadomienia_teams/.env`)

Aktualne na 2026-07-21: `TEAM_ID` → `3ffd0e1a-…`, `SCHEDULING_GROUP_ID` → `TAG_bb093836-…`,
`ONLY_USER_IDS` → 8 osób, `TOKEN_CACHE` → osobny plik Virtual WorkMate (sesja Piotra Cząstkiewicza
zaparkowana, plik nietknięty, przywracana odkomentowaniem jednej linii).

**Przebieg próbny działa**: 5 osób do powiadomienia, w tym trzy z odtworzonym grafikiem
z poprzedniego tygodnia. Pozostałe trzy z ośmiu mają już zmiany na przyszły tydzień.
