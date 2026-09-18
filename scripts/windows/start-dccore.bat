@echo off
setlocal

rem ---------------------------------------------------------------------
rem  DCCore launcher for Windows.
rem
rem  The important line is the cd below. Every data path in config.py is
rem  relative - ./data/bans.txt, ./lists - so they resolve against the
rem  working directory. Double-clicking this file from anywhere, or
rem  running it from a shortcut, would otherwise start the daemon with a
rem  working directory that is not the repository, and it would quietly
rem  create an empty data folder somewhere else and boot with no bans,
rem  no queue and no list.
rem
rem  Usage:
rem    scripts\windows\start-dccore.bat          first run: ask the setup questions;
rem                                              then check the setup and start the daemon
rem    scripts\windows\start-dccore.bat check    check the setup and stop
rem
rem  THE LAUNCHER IS THE INSTALL (#547, Proposal 1). A first-timer's whole
rem  install is: install Python, extract, double-click this file. With no
rem  config yet it runs configure.py right here rather than telling them to
rem  copy a sample; before every start it offers to install Flask if the
rem  dashboard is on and Flask is missing; and it finds Python where the
rem  python.org installer puts it even when the "Add to PATH" box was
rem  missed. Nothing an already-configured install does changes.
rem ---------------------------------------------------------------------

cd /d "%~dp0..\.."

rem --- find an interpreter ----------------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY where python >nul 2>&1 && set "PY=python"

rem  Neither on PATH. The python.org installer puts a per-user install under
rem  %LOCALAPPDATA%\Programs\Python and an all-users one under %ProgramFiles%,
rem  and a missed "Add python.exe to PATH" box is the commonest reason `where`
rem  finds nothing - the interpreter is there, it just was not announced. The
rem  value keeps its own quotes because the path has spaces in it on most
rem  machines ("Program Files") and %PY% is used bare everywhere below.
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%~D\python.exe" set "PY="%%~D\python.exe""
)
if not defined PY for /d %%D in ("%ProgramFiles%\Python3*") do (
    if exist "%%~D\python.exe" set "PY="%%~D\python.exe""
)

if not defined PY (
    echo.
    echo   Python was not found.
    echo.
    echo   Install Python 3.10 or newer from python.org and tick BOTH boxes
    echo   in its installer - "Add python.exe to PATH" and "py launcher" -
    echo   then run this file again.
    echo.
    echo       https://www.python.org/downloads/windows/
    echo.
    pause
    exit /b 1
)

rem --- check-only mode ---------------------------------------------------
rem  NOT written as `if ... ( ... exit /b %errorlevel% )`. cmd.exe expands
rem  %errorlevel% when it PARSES the parenthesised block, before anything
rem  inside it has run, so the value used is whatever it was beforehand -
rem  0 - whatever check-setup.py actually returned. Verified on Windows 11:
rem  the block form exits 0 where this form exits the real code.
rem
rem  It matters because `start-dccore.bat check` is the documented Windows
rem  pre-flight in README.md, docs/INSTALL.md and docs/WINDOWS.md. It would
rem  print "FAIL ..." and "1 problem(s) - fix these before starting", then
rem  exit 0 - so a wrapper, a scheduled task or a CI step gating on the
rem  exit code treated a broken config as verified.
rem
rem  The Linux twin was always right (`"$PY" ... ; exit $?`, outside any
rem  block), so the two launchers had drifted on the one thing this shim
rem  layer exists to keep identical.
if /i "%~1"=="check" goto :run_check
goto :after_check

:run_check
%PY% scripts\windows\check-setup.py
set "CHECK_RC=%errorlevel%"
echo.
pause
exit /b %CHECK_RC%

:after_check

rem --- refuse to start without a local config ---------------------------
rem  settings.conf is fully first-class (see scripts/setup_check.py's own
rem  note) - the daemon starts fine from it alone, so this only refuses
rem  when NEITHER override exists.
rem  An upgrading install has neither, but is NOT unconfigured: #170 renamed
rem  local_config.py to admin_config.py, and that file is gitignored, so the
rem  pull renamed defaults.py for them and could not touch theirs. The daemon
rem  renames it at import time - but this check runs first, so without this
rem  branch the operator is told to copy the sample, and doing so is exactly
rem  the condition that makes the migration skip for good.
if not exist "admin_config.py" if not exist "settings.conf" if exist "local_config.py" (
    echo.
    echo   Found local_config.py, which #170 renamed to admin_config.py.
    echo.
    echo   Nothing to copy - start the daemon once and it renames the file
    echo   for you, keeping every setting in it:
    echo.
    echo       python oserve.py
    echo.
    echo   Do NOT copy admin_config.py.sample over the top: that leaves your
    echo   real settings stranded in local_config.py.
    echo.
    pause
    exit /b 1
)

rem --- first run: ask the questions here ---------------------------------
rem  No config at all means a first run, not a mistake. configure.py asks
rem  everything in the right order - nick, server, channels, admin nick,
rem  music folder, dashboard, password - and writes settings.conf and
rem  admin_config.py; it used to be a separate terminal step this file then
rem  told people to go and do. A tree without configure.py (a broken
rem  extract) still gets the old instruction, so nothing is worse than before.
if not exist "admin_config.py" if not exist "settings.conf" if exist "configure.py" goto :first_run
if not exist "admin_config.py" if not exist "settings.conf" (
    echo.
    echo   No admin_config.py and no settings.conf found, and no configure.py
    echo   to create them with - this does not look like a complete DCCore
    echo   folder. Extract the download again, then run this file.
    echo.
    pause
    exit /b 1
)
goto :configured

:first_run
echo.
echo   Welcome to DCCore. This looks like the first run - a few questions
echo   and it will be set up. You can change every answer later on the
echo   dashboard's Settings page.
echo.
%PY% configure.py
if errorlevel 1 (
    echo.
    echo   Setup did not finish, so DCCore was not started. Run this file
    echo   again to pick it up where it stopped.
    echo.
    pause
    exit /b 1
)
echo.

:configured

rem --- refuse to start on a broken or dangerous config -------------------
rem  check-setup.py fails on a missing music directory, and on a config
rem  still pointing at the production bot's nick or channels. That second
rem  one is worth blocking: it would put a near-identical second bot into
rem  live trading channels, which can get the other operator banned too.
%PY% scripts\windows\check-setup.py >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Setup check failed - not starting. Details:
    echo.
    %PY% scripts\windows\check-setup.py
    echo.
    pause
    exit /b 1
)

rem --- the dashboard's one dependency, offered before it is missed ---------
rem  Silent when the dashboard is off or Flask is already there; otherwise
rem  the same offer configure.py makes during setup. Never stops the start.
%PY% configure.py --flask

rem --- go ----------------------------------------------------------------
echo.
echo   Starting DCCore.  Press Ctrl-C in this window to stop it.
echo   Closing this window stops the bot too - leave it open, or minimise it.
echo.
%PY% oserve.py
set "RC=%errorlevel%"

echo.
if "%RC%"=="0" (
    echo   DCCore exited normally.
) else (
    echo   DCCore exited with code %RC%.
)
echo.
pause
exit /b %RC%
