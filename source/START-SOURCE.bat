@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto deps
py -3.13 -m venv .venv
if errorlevel 1 goto failed
:deps
".venv\Scripts\python.exe" -c "import PySide6,piper,soundfile,sounddevice,soxr,requests" >nul 2>&1
if not errorlevel 1 goto engine
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
:engine
if exist "engines\whisper\whisper-cli.exe" goto run
".venv\Scripts\python.exe" fetch_engine.py
if errorlevel 1 goto failed
:run
".venv\Scripts\python.exe" -X utf8 main.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Python 3.13 x64 and internet are required for initial setup.
pause
exit /b 1
