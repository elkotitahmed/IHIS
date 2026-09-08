@echo off
rem Double-click launcher: exposes the local iHIS server to the internet for a demo.
rem See scripts\tunnel.ps1 for options (-Ngrok, -NgrokDomain, -Port).
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0tunnel.ps1" %*
if errorlevel 1 powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tunnel.ps1" %*
pause
