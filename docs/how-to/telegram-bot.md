# How-to: bot Telegram (Faza 2)

Bot, który w Telegramie odbiera pytanie i odpowiada przez **runtime agenta**
WorkMate nad tymi samymi narzędziami co serwer MCP — **lokalnie, bez Azure, bez
tunelu i bez publicznego endpointu** (tryb LONG POLLING: bot sam odpytuje Telegram).
Telegram to drzwi **mniej zaufane** (ADR 0006), więc agent działa na katalogu
**tylko do odczytu** (czyta notatki i status projektów, **nie zapisuje**).

Bot stoi na wspólnym szwie `Responder`
([`adapters/inbound/responder.py`](../../src/workmate/adapters/inbound/responder.py))
— ten sam interfejs co drzwi Teams. Powrót do samego echa (test transportu bez
klucza) to jedna linia: `RuntimeResponder(runtime)` → `EchoResponder()` w `app.py`.

Stos: **python-telegram-bot** (long polling) + **runtime agenta** (Claude API) +
**python-dotenv**.

## Wymagania

- Python 3.10+ i `uv`; instalacja: **`uv sync --extra telegram --extra agent`**
  (serwer MCP zostaje lekki bez tych extra).
- Konto Telegram; klucz **Claude API** (console.anthropic.com → API keys).

---

## Krok 1 — utwórz bota u @BotFather i weź token

1. W Telegramie otwórz czat z **[@BotFather](https://t.me/BotFather)**.
2. Wyślij `/newbot`, podaj nazwę i unikalny username (kończący się na `bot`).
3. BotFather odpowie **tokenem** w postaci `123456789:AA...` — to sekret.

**Gotowe, gdy:** masz token bota.

---

## Krok 2 — sekrety do `.env` (nigdy do repo)

W korzeniu projektu utwórz/uzupełnij `.env` (jest w `.gitignore`):

```
WORKMATE_TELEGRAM_BOT_TOKEN=123456789:AA...   # token z @BotFather
ANTHROPIC_API_KEY=sk-ant-api03-...            # klucz Claude API (runtime agenta)
```

> Oba to **sekrety** — trzymaj je wyłącznie w lokalnym `.env` albo w zmiennych
> środowiskowych, nigdy w repozytorium. Brak któregokolwiek = **twardy błąd startu**.

**Gotowe, gdy:** `.env` zawiera token bota i klucz Claude API.

---

## Krok 3 — uruchom bota (long polling)

```powershell
uv sync --extra telegram --extra agent
uv run workmate-telegram
```

W logu zobaczysz `Drzwi Telegram wystartowały (long polling, runtime agenta read-only).`
Bot łączy się z Telegramem sam (odpytuje) — nie potrzebuje publicznego adresu.

**Gotowe, gdy:** proces działa i nie zgłasza błędu tokenu ani klucza.

---

## Krok 4 — test

W Telegramie otwórz czat ze swoim botem (po username z Kroku 1) i zadaj pytanie
o bazę wiedzy, np. `jaki jest status projektu scada-integration?`.

**Gotowe, gdy:** bot odpowiada **podsumowaniem opartym o notatki/rejestr** (a nie
prostym echem) — agent zawołał narzędzia odczytu i złożył odpowiedź.

> Sam transport (bez klucza/agenta) potwierdza smoke-test handlera w repo
> (`tests/adapters/telegram/test_handler.py`) oraz wariant echa: w
> [`app.py`](../../src/workmate/adapters/inbound/telegram/app.py) zmień
> `RuntimeResponder(runtime)` na `EchoResponder()`.

---

## Rozwiązywanie problemów

| Objaw | Najczęstsza przyczyna | Co zrobić |
|-------|----------------------|-----------|
| **„wymaga WORKMATE_TELEGRAM_BOT_TOKEN"** | Brak tokenu w env/.env | Uzupełnij `.env` albo ustaw zmienną środowiskową. |
| **„wymaga klucza Claude API"** | Brak `ANTHROPIC_API_KEY` | Dodaj klucz do `.env` (sekcja runtime agenta). |
| **„Drzwi Telegram wymagają extra 'telegram'/'agent'"** | Brak zależności | `uv sync --extra telegram --extra agent`. |
| **Bot milczy / błąd w logu** | Zły token, brak internetu, błąd Claude API | Sprawdź token u @BotFather; napisz do bota jako pierwszy; sprawdź klucz/sieć (błąd API leci do logu). |

---

## Co dalej (poza odczytem)

Agent przez Telegram **czyta** (katalog read-only). **Zapis** z Telegrama
(`save_note`) to osobna decyzja bramkowania — Telegram to drzwi mniej zaufane
(ADR 0006), więc to kolejny krok (M3/M4), nie spike. Wpięcie zapisu to zmiana
profilu (`enable_write`) w okablowaniu runtime + własna bramka, nie w handlerze.
