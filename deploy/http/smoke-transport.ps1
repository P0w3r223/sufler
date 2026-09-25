<#
.SYNOPSIS
    Smoke-test transportu HTTP Sufler (Bramka 3, ADR 0007) — do wykonania PO wdrożeniu.

.DESCRIPTION
    Automatyzuje weryfikację z deploy-http.md §6. Sprawdza warstwę uwierzytelniania
    i ochronę hosta, które są deterministyczne:

      1. bez tokenu                 -> 401 (WWW-Authenticate: Bearer)
      2. zły token                  -> 401
      3. dobry token                -> drzwi wpuszczają (auth przeszedł => NIE 401).
                                        Sprawdzamy „nie 401", a nie twarde 200: endpoint
                                        streamable-http na GET wymaga Accept: text/event-stream
                                        i bywa, że zwraca 200 (SSE, ucinane po -m) albo inny
                                        2xx/406 zależnie od wersji — twarde ==200 dałoby
                                        fałszywy FAIL na POPRAWNIE wdrożonym serwerze.
      4. obcy Host + dobry token    -> 421 (ochrona przed DNS-rebinding; middleware
                                            auth jest PRZED kontrolą Host, więc token
                                            musi być ważny, inaczej dostaniesz 401)

    Wymaga curl.exe (wbudowany w Windows 10+). Token bierze z -Token albo z
    $env:SUFLER_TOKEN. NIE wpisuj tokenu do repo ani do historii powłoki.

.EXAMPLE
    $env:SUFLER_TOKEN = '<surowy token dewelopera>'
    ./smoke-transport.ps1 -BaseUrl 'https://sufler.firma.pl'
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $BaseUrl,
    [string] $Token = $env:SUFLER_TOKEN,
    [string] $ForeignHost = 'obcy.host',
    [int]    $TimeoutSec = 5
)

$ErrorActionPreference = 'Stop'
$mcp = "$($BaseUrl.TrimEnd('/'))/mcp"

if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
    throw "Brak curl.exe (wbudowany w Windows 10+). Zainstaluj cURL albo użyj innej maszyny."
}
if ([string]::IsNullOrWhiteSpace($Token)) {
    throw "Brak tokenu. Ustaw `$env:SUFLER_TOKEN albo podaj -Token."
}

function Get-Status {
    param([string[]] $CurlArgs)
    # -o NUL: odrzuć ciało; -w '%{http_code}': tylko kod; -m: nie wisić na SSE.
    $code = & curl.exe -s -o NUL -m $TimeoutSec -w '%{http_code}' @CurlArgs $mcp
    return [int]$code
}

$auth = "Authorization: Bearer $Token"
$sse  = 'Accept: text/event-stream'  # streamable-http wymaga tego na GET
# Expect = kod dokładny; Forbid = pass, gdy status jest INNY niż podany (i nie 000).
$checks = @(
    @{ Name = 'bez tokenu';              Expect = 401; Args = @() }
    @{ Name = 'zły token';               Expect = 401; Args = @('-H', 'Authorization: Bearer zly') }
    @{ Name = 'dobry token';             Forbid = 401; Args = @('-H', $auth, '-H', $sse) }
    @{ Name = 'obcy Host + dobry token'; Expect = 421;
       Args = @('-H', "Host: $ForeignHost", '-H', $auth, '-H', $sse) }
)

$failed = 0
foreach ($c in $checks) {
    $status = Get-Status -CurlArgs $c.Args
    if ($c.ContainsKey('Expect')) {
        $ok = ($status -eq $c.Expect)
        $want = "== $($c.Expect)"
    } else {
        $ok = ($status -ne $c.Forbid -and $status -ne 0)
        $want = "!= $($c.Forbid)"
    }
    if ($ok) {
        Write-Host ("[OK]   {0,-26} -> {1}" -f $c.Name, $status) -ForegroundColor Green
    } else {
        Write-Host ("[FAIL] {0,-26} -> {1} (oczekiwano {2})" -f $c.Name, $status, $want) `
            -ForegroundColor Red
        $failed++
    }
}

Write-Host ""
if ($failed -gt 0) {
    Write-Host "$failed z $($checks.Count) sprawdzeń nie przeszło." -ForegroundColor Red
    Write-Host "Podpowiedzi: 421 na dobrym ruchu => brak publicznego hosta w SUFLER_ALLOWED_HOSTS"
    Write-Host "             (obie formy!). 000 => proxy buforuje SSE lub usługa nie działa."
    exit 1
}
Write-Host "Transport OK. Dalej: podłącz Claude Code (.mcp.json HTTP) i sprawdź 4 narzędzia odczytu."
