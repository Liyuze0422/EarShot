@echo off
rem Post-interview review: list the questions that went badly.
rem Works with either the packaged exe or the source checkout.
rem Keep this file ASCII-only: cmd.exe reads it as GBK on this machine.
rem CRLF line endings are required -- cmd.exe mis-parses LF-only batch files.
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\EarShot\EarShot.exe" (
  "%~dp0dist\EarShot\EarShot.exe" --run "%~dp0tools\session_report.py"
) else (
  "%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0tools\session_report.py"
)
set RC=%errorlevel%
echo.
pause
exit /b %RC%
