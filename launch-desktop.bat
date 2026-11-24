@echo off
cd /d "%~dp0\desktop-app"
start "" .venv\Scripts\pythonw.exe -m main.app %*
