@echo off
rem Double-click: get a fresh public demo link (restarts the Cloudflare quick tunnel; starts the server if needed).
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tunnel_renew.ps1" %*
if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tunnel_renew.ps1" %*
pause
