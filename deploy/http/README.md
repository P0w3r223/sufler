# Szkielet wdrożenia HTTP (Bramka 3, ADR 0007)

Ten katalog to **gotowy do użycia szkielet** wdrożenia WorkMate po `streamable-http`
z uwierzytelnianiem per osoba. Kod serwera (`config.py`, `adapters/inbound/mcp/auth.py`,
gałąź HTTP w `server.py`) jest domknięty i przetestowany — tu leżą **narzędzia
operacyjne i szablony**, których runbook wcześniej opisywał tylko prozą.

> ⏳ **Serwer firmowy jest na późniejszy etap.** Wszystko tu da się przygotować i
> uruchomić lokalnie już teraz (generowanie/walidacja magazynu tokenów); kroki
> zależne od serwera (IIS, usługa Windows, smoke na żywym adresie) czekają na
> udostępnienie maszyny. Pełna procedura: [`docs/how-to/deploy-http.md`](../../docs/how-to/deploy-http.md).

## Zawartość

| Plik | Rola | Kiedy |
|------|------|-------|
| `manage_tokens.py` | Wydawanie / rotacja / rewokacja / walidacja tokenów. Hashuje token dokładnie jak weryfikator drzwi. | już teraz (offline) |
| `tokens.example.json` | Wzór struktury magazynu (tylko `sha256`, bez sekretów). | referencja |
| `web.config.sample` | IIS reverse proxy (ARR) + wyłączone buforowanie SSE. | wdrożenie |
| `install-service.ps1` | Rejestracja uvicorn jako usługi Windows (NSSM). Domyślnie dry-run. | wdrożenie |
| `smoke-transport.ps1` | Weryfikacja 401/401/200/421 po wdrożeniu. | po wdrożeniu |
| `mcp.team.json.sample` | Szablon `.mcp.json` dla zespołu (transport HTTP + token) — repo-`.mcp.json` zostaje na `stdio`. | onboarding dev |

## Kolejność wdrożenia

1. **Magazyn tokenów** (offline, można teraz):
   ```powershell
   $raw = uv run --no-sync python deploy/http/manage_tokens.py issue --person anna.kowalska
   # $raw = surowy token — przekaż bezpiecznym kanałem; magazyn trzyma tylko sha256
   uv run --no-sync python deploy/http/manage_tokens.py list
   uv run --no-sync python deploy/http/manage_tokens.py verify
   ```
   Domyślny magazyn: `C:\ProgramData\WorkMate\tokens.json` (poza repo i poza `data/`).
   Nadpisz przez `--store` albo `WORKMATE_TOKENS_FILE`. Zabezpiecz ACL-em NTFS
   (patrz runbook §2).

2. **Usługa Windows** (serwer): `install-service.ps1` — podejrzyj dry-runem, potem `-Apply`.

3. **IIS reverse proxy** (serwer): skopiuj `web.config.sample` → `web.config` witryny,
   podmień host, odblokuj/ustaw ARR `responseBufferLimit=0`.

4. **Smoke transportu** (po wdrożeniu): `smoke-transport.ps1 -BaseUrl https://workmate.firma.pl`.

5. **Klient**: skopiuj [`mcp.team.json.sample`](mcp.team.json.sample) → `.mcp.json` LOKALNY
   (poza repo albo w `.gitignore`), podmień `<HOST>`/`<TOKEN>` na własne. Repo-`.mcp.json`
   celowo zostaje na `stdio` (dev bez serwera nadal działa lokalnie). Szczegóły: runbook §5.

## Rotacja i rewokacja

```powershell
# rotacja: wydaj nowy (obok starego), dev podmienia, usuń stary po prefiksie hasha
uv run --no-sync python deploy/http/manage_tokens.py issue  --person anna.kowalska
uv run --no-sync python deploy/http/manage_tokens.py revoke --hash 1a2b3c
# po każdej zmianie zrestartuj usługę: nssm restart WorkMate
```

Przy podejrzeniu wycieku: `revoke` + restart usługi — token przestaje działać od razu.

## Do potwierdzenia na serwerze (nie da się zweryfikować bez żywej maszyny)

- **Buforowanie SSE (IIS/ARR).** `web.config.sample` ustawia `responseBufferLimit=0` w
  sekcji ARR `system.webServer/proxy`. Jeśli sekcja jest zablokowana, ustaw ją w
  `applicationHost.config` (patrz notka w pliku). Objaw niepowodzenia: klient MCP wisi
  na pierwszym żądaniu (`smoke-transport.ps1` pokaże `000`).
- **Kod odpowiedzi na dobry token.** `smoke-transport.ps1` sprawdza „drzwi wpuściły"
  (status ≠ 401) z nagłówkiem `Accept: text/event-stream`. Zanotuj realny kod (200/SSE),
  jeśli chcesz zaostrzyć asercję po pierwszym udanym wdrożeniu.
- **Polityka nagłówka `Host`.** `WORKMATE_ALLOWED_HOSTS` MUSI zawierać publiczny host w
  OBU formach (bez portu i z `:*`), inaczej realny ruch na 443 dostanie `421`.
