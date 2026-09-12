@echo off
rem  Start the key listener GUI without a console window.
rem  ASCII-only on purpose: cmd.exe parses .bat files with the local code page.
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "key_listener.py"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "key_listener.py"
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 "key_listener.py"
    exit /b 0
)

echo Python was not found in PATH.
echo Install Python 3 from https://www.python.org/downloads/
pause
