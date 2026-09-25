# Do wykonania, zanim obraz zadziała na serwerze

> **DOKUMENT HISTORYCZNY (2026-07-22).** Wersje i liczby testów poniżej dotyczą 0.2.1.
> Na produkcji działa dziś **0.2.19**, a podane niżej „259 testów w trakcie budowania" nie opisuje
> obecnego stanu — zestawu testowego 0.2.19 nie zachowano. Aktualna konfiguracja: `deploy/env.example`.
> Zostawione jako zapis zadań po stronie tenanta, nie jako instrukcja wdrożenia.

Stan na 2026-07-22. Aktualna wersja to **0.2.1** — obraz `0.2.0` z 21.07 jest NIEAKTUALNY i nie
należy go uruchamiać (ADR 0003: wygaszanie bez dowodu; szczegóły w README-docker.md, „Znane
ograniczenia"). Obraz 0.2.1 buduje się na serwerze z nowej paczki źródłowej; 259 testów biegnie
w trakcie budowania. Poniższe punkty to poza tym wyłącznie to, czego brakuje **po stronie tenanta
i konfiguracji** — nie kodu.

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
  team id:  wartość w .env (POWIADOMIENIA_TEAM_ID)
  grafik:   enabled=True   Europe/Warsaw   tydzień od poniedziałku   1990 zmian
  grupa:    „Pion Inteligentnych Technologii"  (AKTYWNA — id w .env, POWIADOMIENIA_SCHEDULING_GROUP_ID)
  druga:    „… - Staż"                          (isActive=false — NIE używać)
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

Id grupy harmonogramowania — wartość w `deploy/env.example` i w lokalnym `.env`.
Wybrana grupa **aktywna** — druga w tym grafiku ma `isActive=false` i zapisy trafiałyby donikąd.

### 3. Zalogować się jako Virtual Sufler  ▸ człowiek z dostępem do tego konta

```bash
cd /opt/teams-shifts-reminder
docker compose stop
docker run --rm -v powiadomienia-teams-stan:/s alpine rm -f /s/teams_token_cache.bin
docker compose run --rm -it powiadomienia --login
```

> **Ten `rm -f` wyłącza też Sufler'owi dostęp do grafiku.** Wolumen `powiadomienia-teams-stan`
> jest zamontowany **read-only** do kontenera `sufler-teams-graph`, który czyta stąd cache MSAL
> (decyzja 0004 paczki `infra-docker-workmate`). Od skasowania pliku do zakończenia device-code
> drugi produkt nie ma jak sięgnąć do Shifts — i szuka wtedy przyczyny u siebie.

**Kasowanie starego cache jest warunkiem poprawności, nie porządkiem.** Przy dwóch kontach w jednym
pliku wybór nadawcy byłby losowy (kolejność `get_accounts()` nie jest kontraktem MSAL), a bot
odezwałby się do zespołu niewłaściwą tożsamością — nieodwracalnie.

Od wersji 0.2.0 pilnuje tego **kod, a nie procedura**: `graph/auth.py` rzuca `AmbiguousAccountError`
z instrukcją usunięcia pliku, a `--login` odmawia startu, zamiast dołożyć kolejne konto do cache.
Jeśli zobaczysz ten komunikat, wykonaj powyższe `rm -f` i powtórz logowanie.

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

### 7. ~~Zawęzić uprawnienia rejestracji aplikacji~~ — ZAMKNIĘTE 2026-09-09 jako PRZYJĘTE RYZYKO

**Decyzja klienta: nie zawężamy.** Pozycja przestaje być zadaniem do wykonania — nie dlatego, że
praca została zrobiona, tylko dlatego, że ryzyko zostało świadomie przyjęte. Zapis zostaje, bo
skasowanie prawdziwego ustalenia zamieniłoby ten dokument w źródło nieprawdy.

**Stan zmierzony 2026-09-09** (odczyt cache tokenu na produkcji, nie z pamięci): token niesie
**27 uprawnień**, kod prosi o **8**. Poprzedni zapis mówił „25" — liczba urosła od lipca, co samo
w sobie jest informacją: nikt jej nie pilnuje.

Czternaście uprawnień jest nadmiarowych i bot nie używa żadnego z nich:

```
Channel.Create           ChannelMessage.Edit        Files.Read.All
Channel.Delete.All       ChannelMessage.Read.All    Files.ReadWrite.All
Channel.ReadBasic.All    ChannelMessage.ReadWrite   OnlineMeetingTranscript.Read.All
ChannelMember.Read.All   ChannelMessage.Send        OnlineMeetings.Read
Team.ReadBasic.All                                  Sites.Read.All
```

**Co dokładnie zostało przyjęte.** Cache tokenu (`teams_token_cache.bin`) leży w wolumenie stanu
i jest montowany read-only także do kontenera `sufler-teams-graph`. Jego wyciek daje dziś nie
tylko dostęp do grafiku i czatów 1:1, ale też **zapis do plików SharePoint/OneDrive**
(`Files.ReadWrite.All`), **kasowanie kanałów Teams** (`Channel.Delete.All`) i **odczyt transkrypcji
spotkań** — czyli powierzchnię wielokrotnie szerszą niż to, do czego usługa jest zbudowana.

**Jak to odwrócić, gdyby decyzja się zmieniła.** To zmiana w rejestracji aplikacji w Azure AD
(`POWIADOMIENIA_CLIENT_ID`), nie w kodzie: administrator tenanta usuwa nadmiarowe zgody
delegowane, po czym usługa wymaga jednorazowego `--login`. Lista ośmiu potrzebnych uprawnień jest
w `config._DEFAULT_SCOPES` i w README — kod nie prosi o nic ponad nią, więc zawężenie niczego
w nim nie zepsuje.

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
| `POWIADOMIENIA_TOKEN_CACHE` | Lokalnie przestawiony na plik Virtual Sufler. **Na serwerze nieistotne** — ścieżkę narzuca `Dockerfile` (`/var/lib/powiadomienia-teams/…`), a `env` ją tylko powtarza dla czytelności |
| Wolumen `powiadomienia-teams-stan` | Trzyma cache tokenu i stan. Bez niego każdy restart wysyła prośby **drugi raz** — idempotencja opiera się wyłącznie na tym pliku |
| Konto bota w składzie zespołu | Jest pełnoprawnym członkiem, więc bez filtra trafiało na listę „bez grafiku". `run_once` odfiltrowuje je po `get_me()`, niezależnie od `ONLY_USER_IDS` |
| Rozmiar grafiku | 1990 zmian, 2 strony po ~995. Limit `_MAX_PAGES=50` ≈ **49 750 zmian** — przy ~2000/rok zapas na ok. 25 lat. Odczyt całości: ~3,5 s |
| `$filter` po stronie Graph | **Nie działa** dla zakresu dat: `startDateTime ge … and startDateTime lt …` → HTTP 400 („property allowed at most once"). Filtrowanie zostaje po stronie klienta |
| Zmiana wersji obrazu | `WERSJA=0.2.0 bash scripts/build-image.sh`, potem podmiana tagu w `docker-compose.yml`. Etykieta obrazu jest sprawdzana wobec tagu |

## Stan lokalnej konfiguracji (`Powiadomienia_teams/.env`)

Aktualne na 2026-07-21: `TEAM_ID` → `3ffd0e1a-…`, `SCHEDULING_GROUP_ID` → `TAG_bb093836-…`,
`ONLY_USER_IDS` → 8 osób, `TOKEN_CACHE` → osobny plik Virtual Sufler (sesja Piotra Cząstkiewicza
zaparkowana, plik nietknięty, przywracana odkomentowaniem jednej linii).

**Przebieg próbny działa**: 5 osób do powiadomienia, w tym trzy z odtworzonym grafikiem
z poprzedniego tygodnia. Pozostałe trzy z ośmiu mają już zmiany na przyszły tydzień.
