@echo off
rem Launcher: idempotent start (backend + floating window).
rem All logic lives in tools/launch.py so it can be tested and logged.
rem Keep this file ASCII-only: cmd.exe reads it as GBK on this machine.
rem CRLF line endings are required -- cmd.exe mis-parses LF-only batch files.
setlocal
cd /d "%~dp0"
set DOUBLECLICK=
echo %cmdcmdline% | find /i "%~nx0" >nul && set DOUBLECLICK=1

rem Fail fast on a non-ASCII repo path: .venv\Scripts\python.exe only says
rem "No pyvenv.cfg file" there, and the packaged exe cannot even load its own
rem PKG archive. tools/check_path.ps1 prints the reason in Chinese and exits 9.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\check_path.ps1" "%~dp0"
if errorlevel 1 (
  echo.
  echo Launch aborted: the repo path contains non-ASCII characters.
  if defined DOUBLECLICK pause
  exit /b 9
)

echo Starting interview teleprompter ...
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0tools\launch.py"
set RC=%errorlevel%
if not "%RC%"=="0" (
  echo.
  echo Launch failed ^(exit %RC%^).
  echo See logs\launch.log and logs\backend_console.log
)
if defined DOUBLECLICK (
  echo.
  pause
)
exit /b %RC%
