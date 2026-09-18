@echo off
setlocal

rem ---------------------------------------------------------------------
rem  The twin of allow-firewall.bat: takes DCCore's two rules out of
rem  Windows Defender Firewall again. Needs an administrator's yes, like
rem  adding them did, so it re-opens itself elevated when it was not.
rem ---------------------------------------------------------------------

call net session >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Removing a firewall rule needs an administrator's yes - Windows
    echo   will ask now.
    echo.
    call powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
call netsh advfirewall firewall delete rule name="DCCore DCC sends"
call netsh advfirewall firewall delete rule name="DCCore dashboard"
echo.
echo   DCCore's firewall rules are gone. (A "No rules match" above means
echo   there was none to remove.)
echo.
pause
exit /b 0
