@echo off
setlocal
title pgvector Installer for PostgreSQL 18

echo ========================================================
echo Installing pgvector v0.8.7 for PostgreSQL 18
echo ========================================================
echo.

:: Check for administrative rights
net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [INFO] Administrator rights needed to write to C:\Program Files\PostgreSQL\18.
    echo [INFO] Requesting elevation...
    powershell -Command "Start-Process cmd -ArgumentList '/k \"\"%~f0\"\"' -Verb RunAs"
    exit /b
)

set "SOURCE=C:\Users\RavishankarGavhane\Desktop\meeting-assistant\pgvector-bin"
set "DEST=C:\Program Files\PostgreSQL\18"

echo [1/4] Stopping PostgreSQL 18 service...
net stop postgresql-x64-18

echo [2/4] Copying pgvector binaries...
copy /Y "%SOURCE%\lib\vector.dll" "%DEST%\lib\vector.dll"
copy /Y "%SOURCE%\share\extension\*" "%DEST%\share\extension\"

if not exist "%DEST%\include\server\extension\vector" (
    mkdir "%DEST%\include\server\extension\vector"
)
copy /Y "%SOURCE%\include\server\extension\vector\*" "%DEST%\include\server\extension\vector\"

echo [3/4] Starting PostgreSQL 18 service...
net start postgresql-x64-18

echo [4/4] Running Alembic migrations...
cd /d "C:\Users\RavishankarGavhane\Desktop\meeting-assistant\backend"
.venv\Scripts\alembic.exe upgrade head

echo.
echo ========================================================
echo SUCCESS! pgvector is installed and database tables created.
echo ========================================================
echo.
pause
