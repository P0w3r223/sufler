<#
.SYNOPSIS
    Instaluje WorkMate (streamable-http) jako usługę Windows na loopbacku (Bramka 3, ADR 0007).

.DESCRIPTION
    Szkielet do wdrożenia PÓŹNIEJ, gdy serwer będzie gotowy. Rejestruje uvicorn
    przez NSSM (https://nssm.cc/) jako usługę czytającą konfigurację ze zmiennych
    środowiskowych usługi. TLS terminuje IIS (reverse proxy, web.config.sample) —
    uvicorn słucha tylko po HTTP na 127.0.0.1.

    Domyślnie DRY-RUN: wypisuje polecenia NSSM, nic nie wykonuje. Uruchom z
    -Apply, gdy serwer, ścieżki i tokens.json są gotowe.

.EXAMPLE
    ./install-service.ps1 -UvExe 'C:\Path\uv.exe' -ProjectDir 'C:\Path\PROJEKT' `
        -PublicHost 'workmate.firma.pl'
    # podgląd; dodaj -Apply, by faktycznie zainstalować usługę
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $UvExe,
    [Parameter(Mandatory)] [string] $ProjectDir,
    [Parameter(Mandatory)] [string] $PublicHost,
    [string] $ServiceName = 'WorkMate',
    [string] $TokensFile  = 'C:\ProgramData\WorkMate\tokens.json',
    [string] $BindHost    = '127.0.0.1',
    [int]    $BindPort    = 8000,
    [switch] $Apply
)

$ErrorActionPreference = 'Stop'

# ALLOWED_HOSTS MUSI zawierać publiczny host w OBU formach (bez portu i z ":*"),
# inaczej realny ruch na 443 (Host bez portu) dostanie 421 — patrz deploy-http.md.
$allowedHosts = "$PublicHost,${PublicHost}:*,127.0.0.1:*"

$envPairs = [ordered]@{
    'WORKMATE_TRANSPORT'     = 'streamable-http'
    'WORKMATE_ENABLE_WRITE'  = 'false'          # drzwi HTTP tylko do odczytu (4 narzędzia)
    'WORKMATE_TOKENS_FILE'   = $TokensFile
    'WORKMATE_BIND_HOST'     = $BindHost
    'WORKMATE_BIND_PORT'     = "$BindPort"
    'WORKMATE_ALLOWED_HOSTS' = $allowedHosts
}

function Test-Prereqs {
    if (-not (Get-Command nssm -ErrorAction SilentlyContinue)) {
        throw "Brak 'nssm' w PATH. Zainstaluj NSSM (https://nssm.cc/) i spróbuj ponownie."
    }
    if (-not (Test-Path $UvExe))      { throw "Nie znaleziono uv.exe: $UvExe" }
    if (-not (Test-Path $ProjectDir)) { throw "Nie znaleziono katalogu projektu: $ProjectDir" }
    if (-not (Test-Path $TokensFile)) {
        Write-Warning "Magazyn tokenów jeszcze nie istnieje: $TokensFile"
        Write-Warning "Wygeneruj go przed startem: deploy/http/manage_tokens.py issue --person ..."
    }
}

# Każda para MUSI być OSOBNYM argumentem NSSM. Splatujemy tablicę (@envArgs) — NIE
# sklejamy w jeden string, bo `& nssm ... $string` przekazałby wszystko jako jeden
# wpis NAME=VALUE i pozostałe zmienne przepadłyby (serwer wtedy nie wstaje).
$envArgs = @($envPairs.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" })

if (-not $Apply) {
    Write-Host "== DRY-RUN (dodaj -Apply, by wykonać) ==" -ForegroundColor Yellow
    Write-Host "nssm install $ServiceName `"$UvExe`" `"run --no-sync workmate`""
    Write-Host "nssm set $ServiceName AppDirectory `"$ProjectDir`""
    Write-Host "nssm set $ServiceName AppEnvironmentExtra ``"
    $envArgs | ForEach-Object { Write-Host "    $_" }
    Write-Host "nssm start $ServiceName"
    Write-Host ""
    Write-Host "Serwer nasłucha na http://${BindHost}:${BindPort}/mcp (ścieżka /mcp stała)."
    return
}

Test-Prereqs
& nssm install $ServiceName $UvExe 'run --no-sync workmate'
& nssm set $ServiceName AppDirectory $ProjectDir
& nssm set $ServiceName AppEnvironmentExtra @envArgs
& nssm start $ServiceName
Write-Host "Usługa $ServiceName uruchomiona. Weryfikacja: deploy/http/smoke-transport.ps1"
