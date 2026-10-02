@echo off
setlocal
cd /d "%~dp0"
py -3.13 -c "import struct; assert struct.calcsize('P')==8, '64-bit Python required'"
if errorlevel 1 goto failed
py -3.13 -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements-build.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" fetch_engine.py
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -m unittest discover -s tests -v
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -X utf8 -m PyInstaller --clean --noconfirm Tixi-Voice.spec
if errorlevel 1 goto failed
echo Built: dist\Tixi-Voice\Tixi-Voice.exe
echo Keep _internal alongside the EXE. Retain notices, source and LICENSES when redistributing.
pause
exit /b 0
:failed
echo Build failed. Read the error above.
pause
exit /b 1
