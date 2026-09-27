@echo off
rem  Start the key listener as Administrator (a UAC prompt will appear).
rem
rem  Needed when the game itself runs elevated: Windows UIPI blocks input
rem  injection from a lower-integrity process to a higher-integrity one, so a
rem  normal-privilege listener cannot deliver keys to an elevated game.
rem
rem  ASCII-only on purpose: cmd.exe parses .bat files with the local code page.
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    powershell -NoProfile -Command "Start-Process pythonw -ArgumentList 'key_listener.py' -WorkingDirectory '%CD%' -Verb RunAs"
    exit /b 0
)

where pyw >nul 2>nul
if %errorlevel%==0 (
    powershell -NoProfile -Command "Start-Process pyw -ArgumentList '-3','key_listener.py' -WorkingDirectory '%CD%' -Verb RunAs"
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    powershell -NoProfile -Command "Start-Process py -ArgumentList '-3','key_listener.py' -WorkingDirectory '%CD%' -Verb RunAs"
    exit /b 0
)

echo Python was not found in PATH.
echo Install Python 3 from https://www.python.org/downloads/
pause
