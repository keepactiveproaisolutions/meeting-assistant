@echo off
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Environment missing - run setup.bat first.
  exit /b 1
)
title Meeting Assistant - Backend
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8000
