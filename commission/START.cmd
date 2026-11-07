@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.13 -X utf8 launch.py
) else (
  python -X utf8 launch.py
)
if errorlevel 1 echo Check README.txt: Windows x64 and Python 3.13 x64 are required.
pause
