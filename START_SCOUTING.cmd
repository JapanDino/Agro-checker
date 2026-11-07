@echo off
cd /d "%~dp0"
py -3.13 -X utf8 scripts\start_scouting.py %*
if errorlevel 1 (
  echo.
  echo Startup failed. Install Python 3.13 x64 from python.org and read docs\NEW_LAPTOP.md.
  pause
)
