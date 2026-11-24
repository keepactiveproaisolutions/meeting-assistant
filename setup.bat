@echo off
rem ============================================================================
rem  Meeting Assistant - one-time setup (safe to run again at any time)
rem  Creates the virtual environments, installs every dependency and prepares
rem  the configuration file.
rem ============================================================================
setlocal
cd /d "%~dp0"

set "PY=python"
py -3.12 -c "" >nul 2>nul && set "PY=py -3.12"

echo.
echo === Meeting Assistant setup ===
%PY% --version
if errorlevel 1 (
  echo Python 3.12 was not found. Install it from https://www.python.org/downloads/ and run setup.bat again.
  exit /b 1
)

echo.
echo [1/4] Backend environment
if not exist "backend\.venv\Scripts\python.exe" (
  %PY% -m venv "backend\.venv" || goto :failed
)
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip || goto :failed
"backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "backend\requirements-dev.txt" || goto :failed

echo.
echo [2/4] Desktop app environment
if not exist "desktop-app\.venv\Scripts\python.exe" (
  %PY% -m venv "desktop-app\.venv" || goto :failed
)
"desktop-app\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip || goto :failed
"desktop-app\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r "desktop-app\requirements.txt" || goto :failed

echo.
echo [3/4] Web app packages
where npm >nul 2>nul
if errorlevel 1 (
  echo Node.js was not found. Install it from https://nodejs.org/ and run setup.bat again.
  exit /b 1
)
pushd "web-app"
call npm install --no-audit --no-fund || (popd & goto :failed)
popd

echo.
echo [4/4] Configuration
if not exist "backend\.env" (
  copy /y "backend\.env.example" "backend\.env" >nul
  echo Created backend\.env from the template - open it and set OPENAI_API_KEY and DATABASE_URL.
) else (
  echo backend\.env already exists.
)

echo.
echo Setup finished. Start everything with start-all.bat
exit /b 0

:failed
echo.
echo Setup stopped because a step failed - see the message above.
exit /b 1
