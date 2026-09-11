# Renew the public demo link: restart the Cloudflare quick tunnel to the local iHIS
# server and print (and copy) the new https://<name>.trycloudflare.com URL.
#
#   .\scripts\tunnel_renew.ps1              # renew; starts the server first if port 5000 is idle
#   .\scripts\tunnel_renew.ps1 -Port 5000   # another port
#
# The tunnel keeps running in the background after this script exits (no window to
# keep open). Run it again whenever you want a fresh link. Random names that would
# look odd in a presentation are skipped automatically (a few retries).
param([int]$Port = 5000, [int]$MaxTries = 6)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. server: start it (behind-proxy mode) only when nothing listens on the port
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listening) {
    $py = Join-Path $root 'venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { throw "venv not found at $py" }
    $env:IHIS_BEHIND_PROXY = '1'
    if (-not $env:FLASK_CONFIG) { $env:FLASK_CONFIG = 'development' }
    $code = "import run; run.app.run(port=$Port, debug=False, use_reloader=False, host='127.0.0.1')"
    $serverLog = Join-Path $env:TEMP 'ihis_server.log'
    Start-Process -FilePath $py -ArgumentList @('-c', "`"$code`"") -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardError $serverLog | Out-Null
    Write-Host "iHIS server starting on http://127.0.0.1:$Port ..."
    $ok = $false
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port/auth/login" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok = $true; break } catch {}
    }
    if (-not $ok) { throw "Server did not come up within 90 s (see $serverLog)." }
} else {
    Write-Host "Server already listening on port $Port." -ForegroundColor DarkGray
}

# 2. cloudflared binary
$bin = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $bin) {
    $cand = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
    if (Test-Path $cand) { $bin = $cand } else { throw 'cloudflared not found. Install: winget install Cloudflare.cloudflared' }
}

# 3. restart the quick tunnel until the random name is presentable
$bad = 'underwear|terror|sex|kill|drug|naked|porn|dead|death|mortal|sacrifice|mating|hell|damn|war|gun|bomb|fatal|disease|cancer|suicide|abuse|blood|corpse|toilet|butt|poop|crap|pee|sewage|vomit|drunk|nude|breast|genital|ugly|stupid'
$log = Join-Path $env:TEMP 'ihis_cloudflared.log'
$url = $null
for ($try = 1; $try -le $MaxTries; $try++) {
    Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Remove-Item $log -ErrorAction SilentlyContinue
    Start-Process -FilePath $bin -ArgumentList @('tunnel', '--url', "http://127.0.0.1:$Port", '--no-autoupdate') `
        -WindowStyle Hidden -RedirectStandardError $log | Out-Null
    $found = $null
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Path $log) {
            $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
            if ($m) { $found = $m.Matches[0].Value; break }
        }
    }
    if (-not $found) { Write-Host "try ${try}: no URL yet, retrying..." -ForegroundColor Yellow; continue }
    if ($found -match $bad) { Write-Host "try ${try}: $found (skipped, awkward name)" -ForegroundColor DarkGray; continue }
    $url = $found; break
}
if (-not $url) { throw "Could not obtain a tunnel URL (see $log)." }

# 4. verify from outside (a brand-new name can take up to a minute to appear in DNS), print, copy, remember
$code = -1
Start-Sleep -Seconds 15      # let the new name reach public DNS before the first lookup (avoids a cached "no such host")
for ($i = 0; $i -lt 8; $i++) {
    try { $code = (Invoke-WebRequest -Uri "$url/auth/login" -UseBasicParsing -TimeoutSec 20).StatusCode; break } catch { Start-Sleep -Seconds 8 }
}
if ($code -ne 200) { Write-Host 'External check did not succeed yet: the name may still be propagating in DNS (usually under a minute); the tunnel itself is registered.' -ForegroundColor Yellow }
Set-Content -Path (Join-Path $root 'var\tunnel_url.txt') -Value $url -ErrorAction SilentlyContinue
Write-Host ''
Write-Host '=============================================================' -ForegroundColor Cyan
Write-Host "  PUBLIC URL:  $url" -ForegroundColor Cyan
Write-Host "  external check: HTTP $code   (tunnel keeps running in the background)" -ForegroundColor Cyan
Write-Host '=============================================================' -ForegroundColor Cyan
try { Set-Clipboard -Value $url; Write-Host 'URL copied to clipboard.' } catch {}
