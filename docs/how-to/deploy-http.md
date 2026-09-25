# How-to: wdrożenie HTTP na serwerze firmowym (Bramka 3)

Ten przewodnik przełącza Sufler z lokalnego `stdio` na hostowany transport
`streamable-http` z uwierzytelnianiem per osoba — realizacja Bramki 3
([ADR 0007](../adr/0007-gate-3-http-auth-deployment.md)). Docelowa architektura:
**uvicorn jako usługa Windows** na loopbacku, za **IIS jako reverse proxy
terminujący TLS**. Drzwi HTTP są **tylko do odczytu** — `save_note` zostaje na
lokalnych, zaufanych drzwiach `stdio` dev.

## Model bezpieczeństwa w skrócie

- Każdy dev dostaje własny **opaque token** (bearer). Serwer trzyma tylko jego
  `sha256`; porównanie jest stałoczasowe. Brak zewnętrznego IdP.
- **Magazyn tokenów leży poza `data/`** i poza repo — narzędzia odczytu widzą
  wyłącznie `data/`, więc fizycznie nie sięgną sekretów.
- **Least privilege per drzwi:** `SUFLER_ENABLE_WRITE=false` → wystawione są
  tylko 4 narzędzia odczytu.
- **TLS obowiązkowy** — token leci w nagłówku, więc nigdy po gołym HTTP na zewnątrz.

## 1. Zmienne środowiskowe usługi

Ustaw je **na poziomie usługi Windows** (nie w sesji powłoki) — serwer czyta
konfigurację przy starcie procesu:

```
SUFLER_TRANSPORT=streamable-http
SUFLER_ENABLE_WRITE=false
SUFLER_TOKENS_FILE=C:\ProgramData\Sufler\tokens.json
SUFLER_BIND_HOST=127.0.0.1
SUFLER_BIND_PORT=8000
SUFLER_ALLOWED_HOSTS=sufler.firma.pl,sufler.firma.pl:*,127.0.0.1:*,localhost:*
# SUFLER_ALLOWED_ORIGINS=https://sufler.firma.pl   # jeśli klient wysyła Origin
```

> ⚠️ **`SUFLER_ALLOWED_HOSTS` musi zawierać publiczny host — w OBU formach.**
> Za IIS uvicorn stoi na loopbacku, a ochrona przed DNS-rebinding domyślnie
> wpuszcza tylko loopback. Wpis `sufler.firma.pl:*` dopasowuje się **tylko gdy
> Host zawiera port**, a klient HTTPS na 443 wysyła `Host: sufler.firma.pl` bez
> portu — dlatego podaj jednocześnie wariant bez portu (`sufler.firma.pl`) i z
> portem (`sufler.firma.pl:*`). Bez tego realny ruch dostanie **421**.

## 2. Magazyn tokenów

Utwórz katalog i plik **poza repo**:

```powershell
New-Item -ItemType Directory -Force C:\ProgramData\Sufler
```

`C:\ProgramData\Sufler\tokens.json` — lista wpisów, jeden na osobę:

```json
[
  { "person": "anna.kowalska", "token_sha256": "<sha256 tokenu Anny>", "scopes": ["read"] },
  { "person": "marek.nowak",   "token_sha256": "<sha256 tokenu Marka>", "scopes": ["read"] }
]
```

**Nigdy nie wpisuj surowego tokenu** — tylko jego `sha256`. Ogranicz plik ACL-em
NTFS do konta usługi + administratorów (wyłącz dziedziczenie):

```powershell
icacls C:\ProgramData\Sufler\tokens.json /inheritance:r `
  /grant "NT SERVICE\Sufler:(R)" "BUILTIN\Administrators:(F)"
```

### Wydanie tokenu deweloperowi

```powershell
# 1. Wygeneruj opaque token i jego hash:
$raw  = python -c "import secrets; print(secrets.token_urlsafe(32))"
$hash = python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" $raw
# 2. Dopisz { person, token_sha256=$hash, scopes:["read"] } do tokens.json
# 3. Przekaż $raw deweloperowi bezpiecznym kanałem (NIE mailem, NIE w repo).
```

## 3. Usługa Windows (uvicorn na loopbacku)

Uruchom serwer jako usługę, np. przez [NSSM](https://nssm.cc/):

```powershell
nssm install Sufler "C:\Path\do\uv.exe" "run --no-sync sufler"
nssm set Sufler AppDirectory "C:\Path\do\PROJEKT"
nssm set Sufler AppEnvironmentExtra ^
  SUFLER_TRANSPORT=streamable-http ^
  SUFLER_ENABLE_WRITE=false ^
  SUFLER_TOKENS_FILE=C:\ProgramData\Sufler\tokens.json ^
  SUFLER_ALLOWED_HOSTS=sufler.firma.pl,sufler.firma.pl:*,127.0.0.1:*
nssm start Sufler
```

Serwer nasłuchuje wtedy na `http://127.0.0.1:8000/mcp` (ścieżka `/mcp` jest stała).

## 4. IIS jako reverse proxy TLS

1. Zainstaluj **URL Rewrite** + **Application Request Routing (ARR)**.
2. Powiąż certyfikat firmowy na `443` dla hosta `sufler.firma.pl`.
3. Reguła reverse proxy: `https://sufler.firma.pl/mcp` → `http://127.0.0.1:8000/mcp`.
4. **Wyłącz buforowanie odpowiedzi** (ARR: `responseBufferLimit=0`, wyłącz output
   caching). `streamable-http` strumieniuje po SSE/chunked — z buforowaniem
   klient zawiśnie.
5. Ustal politykę nagłówka `Host`: albo `preserveHostHeader=true` (i publiczny
   host w `SUFLER_ALLOWED_HOSTS`), albo przepisz `Host` na `127.0.0.1`.

### Fallback bez IIS (jedna maszyna)

uvicorn może terminować TLS sam — ustaw obie zmienne i pomiń IIS:

```
SUFLER_TLS_CERTFILE=C:\ProgramData\Sufler\sufler.crt
SUFLER_TLS_KEYFILE=C:\ProgramData\Sufler\sufler.key
```

## 5. Klient — `.mcp.json` dla HTTP

Repo-`.mcp.json` zostaje na `stdio` (dev lokalny). Dla dostępu do serwera
firmowego dev kopiuje szablon
[`deploy/http/mcp.team.json.sample`](../../deploy/http/mcp.team.json.sample) do własnego,
niewersjonowanego `.mcp.json` i podmienia placeholdery `<HOST>`/`<TOKEN>`:

```json
{
  "mcpServers": {
    "workmate": {
      "type": "http",
      "url": "https://sufler.firma.pl/mcp",
      "headers": { "Authorization": "Bearer ${SUFLER_TOKEN}" }
    }
  }
}
```

Dev ustawia `SUFLER_TOKEN` w swoim środowisku (token nigdy nie trafia do repo).

> Rozwinięcie `${SUFLER_TOKEN}` po stronie klienta Claude Code potwierdź przy
> onboardingu pierwszej osoby; alternatywnie dev wkleja token wprost w swojej
> lokalnej (niewersjonowanej) konfiguracji.

### Onboarding nowej osoby (skrót)

1. Poproś administratora o token (`manage_tokens.py issue --person <ty>`, krok 2) —
   dostajesz surowy token bezpiecznym kanałem (NIE mailem, NIE na czacie publicznym).
2. Skopiuj `deploy/http/mcp.team.json.sample` do swojego `.mcp.json` (poza repo albo
   w prywatnej, niewersjonowanej ścieżce), wstaw prawdziwy host i token.
3. Uruchom Claude Code — narzędzia Sufler powinny być widoczne (4 odczytowe + ewentualnie
   `read_events_since`/`get_my_jira_tasks`, zależnie od konfiguracji serwera).

## 6. Weryfikacja po wdrożeniu

```powershell
# bez tokenu -> 401
curl.exe -i https://sufler.firma.pl/mcp
# zły token -> 401
curl.exe -i -H "Authorization: Bearer zly" https://sufler.firma.pl/mcp
# obcy Host z WAŻNYM tokenem -> 421 (ochrona hosta działa).
# Uwaga: bez tokenu byłoby 401 — middleware auth jest przed kontrolą Host.
curl.exe -i -H "Host: obcy.host" -H "Authorization: Bearer $env:SUFLER_TOKEN" https://sufler.firma.pl/mcp
```

Następnie podłącz Claude Code przez `.mcp.json` HTTP i sprawdź, że widoczne są
**cztery** narzędzia odczytu (bez `save_note`).

## 7. Run-book rotacji tokenu

1. Wygeneruj nowy token + `sha256` (jak w kroku 2).
2. **Dopisz** nowy wpis obok starego (dev ma chwilę na podmianę).
3. Dev podmienia `SUFLER_TOKEN` na nowy.
4. Usuń stary wpis z `tokens.json`.
5. Restart usługi (`nssm restart Sufler`), by przeładować magazyn.

Przy podejrzeniu wycieku: usuń wpis i zrestartuj usługę natychmiast — token
przestaje działać od razu.
