@echo off
cd /d "%~dp0web-app"
if not exist "node_modules" (
  echo Web packages missing - run setup.bat first.
  exit /b 1
)
title Meeting Assistant - Web
call npm run dev -- --host
