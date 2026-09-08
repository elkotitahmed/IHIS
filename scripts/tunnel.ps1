# Expose the local iHIS server to the internet for a demo (free, no account).
#
#   .\scripts\tunnel.ps1            -> Cloudflare quick tunnel, random *.trycloudflare.com URL
#   .\scripts\tunnel.ps1 -Ngrok     -> ngrok (needs `ngrok config add-authtoken` once);
#                                      add -NgrokDomain xxx.ngrok-free.app for a fixed URL
#
# Starts the Flask server from the venv with IHIS_BEHIND_PROXY=1 (so https links and the
# real client IP are correct behind the tunnel), then the tunnel, and prints the public URL.
# Ctrl+C stops both. Everything (all local AI models, database, uploads) stays on this PC.
param(
    [switch]$Ngrok,
    [string]$NgrokDomain = '',
    [int]$Port = 5000
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root 'venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "venv not found at $py - create it first (see README)." }

# 1. Flask server (only if nothing already listens on the port)
$listening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
$server = $null
if ($listening) {
    Write-Host "Port $Port already in use - reusing the running server (make sure it was started with IHIS_BEHIND_PROXY=1)." -ForegroundColor Yellow
} else {
    $env:IHIS_BEHIND_PROXY = '1'
    $env:FLASK_CONFIG = if ($env:FLASK_CONFIG) { $env:FLASK_CONFIG } else { 'development' }
    $code = "import run; run.app.run(port=$Port, debug=False, use_reloader=False, host='127.0.0.1')"
    $server = Start-Process -FilePath $py -ArgumentList @('-c', "`"$code`"") -PassThru -NoNewWindow
    Write-Host "iHIS server starting on http://127.0.0.1:$Port (pid $($server.Id))..."
    $ok = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port/auth/login" -UseBasicParsing -TimeoutSec 2 | Out-Null; $ok = $true; break } catch {}
    }
    if (-not $ok) { throw 'Server did not come up within 60 s.' }
}

# 2. Tunnel
try {
    if ($Ngrok) {
        $bin = (Get-Command ngrok -ErrorAction SilentlyContinue).Source
        if (-not $bin) { throw 'ngrok not found. Install: winget install ngrok.ngrok ; then: ngrok config add-authtoken <token>' }
        $args = @('http', "$Port")
        if ($NgrokDomain) { $args = @('http', "--url=$NgrokDomain", "$Port") }
        Write-Host "Starting ngrok... the public URL is shown in the ngrok window / at http://127.0.0.1:4040" -ForegroundColor Green
        & $bin @args
    } else {
        $bin = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
        if (-not $bin) {
            $cand = 'C:\Program Files (x86)\cloudflared\cloudflared.exe'
            if (Test-Path $cand) { $bin = $cand } else { throw 'cloudflared not found. Install: winget install Cloudflare.cloudflared' }
        }
        Write-Host 'Starting Cloudflare quick tunnel (no account needed)...' -ForegroundColor Green
        $log = Join-Path $env:TEMP 'ihis_cloudflared.log'
        $tun = Start-Process -FilePath $bin -ArgumentList @('tunnel', '--url', "http://127.0.0.1:$Port", '--no-autoupdate') `
            -RedirectStandardError $log -PassThru -NoNewWindow
        $url = $null
        for ($i = 0; $i -lt 40; $i++) {
            Start-Sleep -Seconds 1
            if (Test-Path $log) {
                $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' | Select-Object -First 1
                if ($m) { $url = $m.Matches[0].Value; break }
            }
        }
        if ($url) {
            Write-Host ''
            Write-Host '=============================================================' -ForegroundColor Cyan
            Write-Host "  PUBLIC URL:  $url" -ForegroundColor Cyan
            Write-Host '  (changes every run; keep this window open during the demo)' -ForegroundColor Cyan
            Write-Host '=============================================================' -ForegroundColor Cyan
            Write-Host ''
            Set-Clipboard -Value $url
            Write-Host 'URL copied to clipboard. Press Ctrl+C to stop.'
        } else {
            Write-Host "Tunnel URL not found yet - see $log" -ForegroundColor Yellow
        }
        Wait-Process -Id $tun.Id
    }
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue; Write-Host 'Server stopped.' }
}
