# Reference: konfiguracja

Konfiguracja jest scentralizowana w `src/workmate/config.py` (`Settings`) i czytana
ze zmiennych środowiskowych. **Wszystkie zmienne są opcjonalne** — bez nich serwer
używa danych z katalogu `data/` w korzeniu repozytorium.

W trybie `stdio` WorkMate **nie wymaga sekretów ani kluczy API** — odczyt i zapis
notatek działają na lokalnych plikach. Tryb `streamable-http` (Bramka 3) dokłada
**magazyn tokenów** per osoba — trzymany poza `data/` (patrz niżej i
[ADR 0007](../adr/0007-gate-3-http-auth-deployment.md)).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_DATA_DIR` | `<repo>/data` | Katalog bazowy danych. |
| `WORKMATE_NOTES_DIR` | `<data_dir>/notes` | Katalog z notatkami `.md`. |
| `WORKMATE_PROJECTS_REGISTRY` | `<data_dir>/projects/registry.yaml` | Plik rejestru projektów. |
| `WORKMATE_TRANSPORT` | `stdio` | Transport MCP: `stdio` lub `streamable-http`. |
| `WORKMATE_LOG_LEVEL` | `INFO` | Poziom logowania. |
| `WORKMATE_ENABLE_WRITE` | `true` | Czy wystawić narzędzie zapisu `save_note` (profil uprawnień per drzwi, [ADR 0006](../adr/0006-write-capability-gate-2.md)). Drzwi HTTP ustawiają `false`. |

### Tryb HTTP (`streamable-http`, Bramka 3 / ADR 0007)

Poniższe zmienne mają znaczenie **tylko** przy `WORKMATE_TRANSPORT=streamable-http`;
w trybie `stdio` są ignorowane. Pełna procedura: [`how-to/deploy-http.md`](../how-to/deploy-http.md).

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `WORKMATE_TOKENS_FILE` | `C:\ProgramData\WorkMate\tokens.json` | Magazyn tokenów per osoba (`sha256` w spoczynku). **Musi leżeć poza `data/`** — inaczej twardy błąd startowy. |
| `WORKMATE_BIND_HOST` | `127.0.0.1` | Adres nasłuchu uvicorn (za IIS: loopback). |
| `WORKMATE_BIND_PORT` | `8000` | Port nasłuchu uvicorn. |
| `WORKMATE_ALLOWED_HOSTS` | loopback | Lista (po przecinku) dozwolonych nagłówków `Host`. **Dołóż publiczny host w OBU formach** — bez portu (`workmate.firma.pl`, dla HTTPS na 443) i z portem (`workmate.firma.pl:*`); inaczej realny Host daje 421. |
| `WORKMATE_ALLOWED_ORIGINS` | *(puste)* | Lista dozwolonych `Origin` (gdy klient go wysyła). |
| `WORKMATE_TLS_CERTFILE` | *(brak)* | Certyfikat TLS — fallback, gdy uvicorn terminuje TLS bez IIS (razem z kluczem). |
| `WORKMATE_TLS_KEYFILE` | *(brak)* | Klucz TLS — jw. |

## Jak wyznaczana jest ścieżka domyślna

`config.py` szuka korzenia repozytorium, idąc w górę do katalogu z
`pyproject.toml`. Dzięki temu serwer działa niezależnie od bieżącego katalogu
roboczego (np. `uv run --directory ...`), bez zaszywania ścieżek w kodzie.

## Transport

- **`stdio`** (domyślny) — lokalny tryb dla Claude Code (Faza 1, tyg. 1–3).
- **`streamable-http`** — wdrożenie sieciowe na serwerze firmowym (tyg. 4,
  Bramka 3). Przełącza się jedną zmienną: `WORKMATE_TRANSPORT=streamable-http`.
  Uwierzytelnianie per osoba (bearer token) i uprawnienia minimalne opisuje
  [ADR 0007](../adr/0007-gate-3-http-auth-deployment.md); procedura wdrożenia:
  [`how-to/deploy-http.md`](../how-to/deploy-http.md).

## Przykład `.env`

Patrz [`.env.example`](../../.env.example) w korzeniu repozytorium.
