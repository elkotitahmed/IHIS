@echo off
setlocal
cd /d "%~dp0"

rem ---------------------------------------------------------------
rem  iHIS launcher - starts the application automatically on 127.0.0.1:5000
rem  Also provisions the database via Alembic migrations (safe: applies
rem  only pending migrations, not destructive) and seeds any missing
rem  demo accounts (idempotent - safe to run every time).
rem ---------------------------------------------------------------

set PYTHON=venv\Scripts\python.exe
set FLASK=venv\Scripts\flask.exe
set FLASK_APP=run.py
set FLASK_CONFIG=development

rem Use a sensible default secret if none is already set.
if "%SECRET_KEY%"=="" set SECRET_KEY=change-me-in-production-please-123456

echo [iHIS] Preparing the database (Alembic migrations)...
"%FLASK%" db upgrade
if errorlevel 1 (
    echo.
    echo [ERROR] Database migration failed. See messages above.
    pause
    exit /b 1
)

rem Seed demo accounts (seed.py is idempotent: it only creates missing
rem data, so running it on every start is safe and self-healing).
echo [iHIS] Ensuring demo accounts exist...
"%PYTHON%" seed.py
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to seed demo accounts.
    pause
    exit /b 1
)

echo.
echo [iHIS] Starting server on http://127.0.0.1:5000
echo [iHIS] Press Ctrl+C to stop.
echo.
"%PYTHON%" run.py

endlocal
