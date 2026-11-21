@echo off
rem EarShot launcher for the release package.
rem
rem Do NOT double-click EarShot.exe directly: under an install path containing
rem non-ASCII characters (Chinese, Japanese, accented letters...) the PyInstaller
rem 6.x bootloader fails before Python starts -- no dialog, no log line.
rem check_path.ps1 detects that and prints the fix in Chinese.
rem
rem Keep this file ASCII-only + CRLF: cmd.exe reads batch files as GBK here.
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0check_path.ps1" "%~dp0"
if errorlevel 1 (
  echo.
  echo Launch aborted: the install path contains non-ASCII characters.
  echo Move the whole folder to an ASCII path such as C:\EarShot and retry.
  pause
  exit /b 9
)

start "" "%~dp0EarShot.exe"
exit /b 0
