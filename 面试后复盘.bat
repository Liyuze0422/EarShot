@echo off
rem Post-interview review:
rem   1) session_report - the questions that went badly
rem   2) absorb_session - questions the filter wrongly dropped, plus a regress-set snippet
rem Works with either the packaged exe or the source checkout.
rem Keep this file ASCII-only: cmd.exe reads it as GBK on this machine.
rem CRLF line endings are required -- cmd.exe mis-parses LF-only batch files.
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\EarShot\EarShot.exe" (
  set RUN="%~dp0dist\EarShot\EarShot.exe" --run
) else (
  set RUN="%~dp0.venv\Scripts\python.exe" -X utf8
)
echo === 1/2 session report ===
%RUN% "%~dp0tools\session_report.py"
echo.
echo === 2/2 absorb list ===
%RUN% "%~dp0tools\absorb_session.py"
echo.
pause
exit /b 0
