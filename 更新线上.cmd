@echo off
rem MONO one-click push + server sync
chcp 65001 >nul
title MONO Update

where bash >nul 2>nul
if %errorlevel%==0 (
    bash "%~dp0sync_server.sh"
) else (
    if exist "C:\Program Files\Git\bin\bash.exe" (
        "C:\Program Files\Git\bin\bash.exe" "%~dp0sync_server.sh"
    ) else (
        echo [ERROR] Git Bash not found. Please install Git for Windows.
    )
)
echo.
pause
