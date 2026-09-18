@echo off
rem ---------------------------------------------------------------------------
rem  EarShot updater. Runs detached AFTER the app exits, then replaces files.
rem  Usage: apply_update.bat <install_root> <staged_files_dir> <deletes_txt> [wait_seconds]
rem
rem  Why a batch file: Windows locks a running exe, so the program cannot
rem  overwrite itself. Something outside it has to wait, swap, and restart.
rem
rem  NOTE 1: delayed expansion is required. Inside a parenthesised block %N% is
rem  expanded once at parse time, so set /a N+=1 followed by %N% would always
rem  compare the stale value and the counter would never advance.
rem  NOTE 2: every start is guarded by if exist. Launching a missing exe pops a
rem  modal "cannot find file" box that blocks the script forever.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions EnableDelayedExpansion
set "INSTALL=%~1"
set "STAGE=%~2"
set "DELS=%~3"
set "WAIT=%~4"
if "%WAIT%"=="" set "WAIT=60"
set "EXE=EarShot.exe"
set "LOG=%~dp0apply_update.log"

if "%INSTALL%"=="" goto :bad
if "%STAGE%"=="" goto :bad

echo [%DATE% %TIME%] update start, install=%INSTALL% >> "%LOG%"

rem --- 1. wait for the app to exit (up to WAIT seconds, then force through) --
set /a N=0
:wait
tasklist /FI "IMAGENAME eq %EXE%" /NH 2>nul | find /I "%EXE%" >nul
if errorlevel 1 goto :proceed
set /a N+=1
if !N! GEQ %WAIT% goto :proceed
timeout /t 1 /nobreak >nul
goto :wait

:proceed
echo [%DATE% %TIME%] copying %STAGE% >> "%LOG%"

rem --- 2. keep a copy of the old exe in case the new one will not start -----
if exist "%INSTALL%\%EXE%" copy /Y "%INSTALL%\%EXE%" "%INSTALL%\%EXE%.bak" >nul 2>&1

rem --- 3. copy the staged files over the installation ------------------------
xcopy "%STAGE%\*" "%INSTALL%\" /E /Y /I /Q >> "%LOG%" 2>&1
if errorlevel 1 goto :fail

rem --- 4. remove files the new version no longer ships ----------------------
if exist "%DELS%" (
  for /f "usebackq delims=" %%F in ("%DELS%") do (
    if not "%%F"=="" del /F /Q "%INSTALL%\%%F" >nul 2>&1
  )
)

echo [%DATE% %TIME%] done, restarting >> "%LOG%"
if exist "%INSTALL%\%EXE%" start "" "%INSTALL%\%EXE%"
goto :done

:fail
echo [%DATE% %TIME%] FAILED (xcopy errorlevel) >> "%LOG%"
if exist "%INSTALL%\%EXE%" start "" "%INSTALL%\%EXE%"

:done
endlocal
goto :eof

:bad
echo [%DATE% %TIME%] bad arguments: %* >> "%LOG%"
endlocal
goto :eof
