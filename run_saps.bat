@echo off
title SAPS Case Docket Management System
cd /d "%~dp0"
echo ========================================================
echo  Starting SAPS Case Docket Management System...
echo  Local URL: http://127.0.0.1:5050
echo ========================================================
"%~dp0venv\Scripts\python.exe" "%~dp0app.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Server process ended with code %ERRORLEVEL%
)
pause
