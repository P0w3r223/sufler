# How-to: bot Telegram (Faza 2, spike echo)

Ten przewodnik prowadzi od zera do bota, który w Telegramie odbiera wiadomość
i odpisuje „Odebrałem notatkę: …". To spike — dowód, że round-trip Telegrama
działa **lokalnie, bez Azure, bez tunelu i bez publicznego endpointu** (tryb
LONG POLLING: bot sam odpytuje Telegram). Bot NIE dotyka jeszcze rdzenia WorkMate
— stoi na tym samym szwie `Responder` co drzwi Teams
([`adapters/inbound/responder.py`](../../src/workmate/adapters/inbound/responder.py)),
więc wpięcie runtime agenta / zapisu to później jedna linia w `app.py`.

Stos: **python-telegram-bot** (async, `app.run_polling()`) + **python-dotenv**.

## Wymagania
- Python 3.10+ i `uv`; instalacja drzwi Telegram: **`uv sync --extra telegram`**
  (serwer MCP zostaje lekki bez tego extra).
- Konto Telegram (do rozmowy z @BotFather i do testu bota).

---

## Krok 1 — utwórz bota u @BotFather i weź token

1. W Telegramie otwórz czat z **[@BotFather](https://t.me/BotFather)**.
2. Wyślij `/newbot`, podaj nazwę i unikalny username (kończący się na `bot`).
3. BotFather odpowie **tokenem** w postaci `123456789:AA...` — to sekret.

**Gotowe, gdy:** masz token bota.

---

## Krok 2 — token do `.env` (nigdy do repo)

W korzeniu projektu utwórz/uzupełnij `.env` (jest w `.gitignore`):

```
WORKMATE_TELEGRAM_BOT_TOKEN=123456789:AA...   # token z @BotFather
```

> Token to **sekret**. Trzyma się go wyłącznie w lokalnym `.env` albo w zmiennej
> środowiskowej — nigdy w repozytorium. Proces świadomie **nie wystartuje** bez
> tokenu (twardy błąd startu).

**Gotowe, gdy:** `.env` zawiera `WORKMATE_TELEGRAM_BOT_TOKEN`.

---

## Krok 3 — uruchom bota (long polling)

```powershell
uv sync --extra telegram
uv run workmate-telegram
```

W logu zobaczysz `Drzwi Telegram wystartowały (long polling). Ctrl+C, aby zatrzymać.`
Bot łączy się z Telegramem sam (odpytuje) — nie potrzebuje publicznego adresu.

**Gotowe, gdy:** proces działa i nie zgłasza błędu tokenu.

---

## Krok 4 — test echa

W Telegramie otwórz czat ze swoim botem (po username z Kroku 1) i napisz np.
`notatka ze spotkania z mpwik`.

**Gotowe, gdy:** bot odpisuje `Odebrałem notatkę: notatka ze spotkania z mpwik`.

> To samo potwierdza smoke-test handlera w repo
> (`tests/adapters/telegram/test_handler.py`: message → echo), więc jeśli tu coś
> nie gra, to konfiguracja tokenu/bota, nie kod.

---

## Rozwiązywanie problemów

| Objaw | Najczęstsza przyczyna | Co zrobić |
|-------|----------------------|-----------|
| **Proces nie startuje, „wymaga WORKMATE_TELEGRAM_BOT_TOKEN"** | Brak tokenu w env/.env | Uzupełnij `.env` albo ustaw zmienną środowiskową. |
| **„Drzwi Telegram wymagają extra 'telegram'"** | Brak zależności | `uv sync --extra telegram`. |
| **Bot milczy** | Zły token / bot zablokowany / brak internetu | Sprawdź token u @BotFather; napisz do bota jako pierwszy; sprawdź sieć. |
| **401/Unauthorized w logu** | Token nieprawidłowy lub zregenerowany | Weź aktualny token z @BotFather i zaktualizuj `.env`. |

---

## Co dalej (poza spike'em)

Echo to dowód transportu. Wpięcie rdzenia to **szew bez przepisania**: w
[`app.py`](../../src/workmate/adapters/inbound/telegram/app.py) linia
`responder = EchoResponder()` zmienia się na `RuntimeResponder(runtime_rdzenia)`
(runtime agenta) lub responder zapisujący przez `save_note`. Handler i `bot.py`
zostają bez zmian. Telegram to **drzwi mniej zaufane** (jak Teams, ADR 0006):
przy wpięciu runtime katalog narzędzi budujemy read-only, a treść z Telegrama
traktujemy jak dane, nie polecenia.
