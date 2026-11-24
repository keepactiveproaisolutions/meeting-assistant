@echo off
rem Starts the backend and the web app in their own windows, then opens the desktop app.
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Environments missing - run setup.bat first.
  exit /b 1
)
start "Meeting Assistant - Backend" cmd /k "%~dp0start-backend.bat"
start "Meeting Assistant - Web" cmd /k "%~dp0start-web.bat"
rem Give the backend a few seconds to come up before the desktop app connects.
timeout /t 6 /nobreak >nul
call "%~dp0launch-desktop.bat"
