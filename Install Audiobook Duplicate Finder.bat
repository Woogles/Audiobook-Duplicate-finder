@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
if errorlevel 1 (
    echo.
    echo Setup failed. Read the error above, correct the issue, and run this installer again.
    pause
)