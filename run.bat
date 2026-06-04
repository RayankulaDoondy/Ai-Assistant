@echo off
REM Hunt server launcher. Double-click to start.
REM Window stays open while the server runs; close it to stop.
cd /d "%~dp0"
title Hunt server (port 8001)
echo Starting Hunt on http://127.0.0.1:8001 ...
echo Press Ctrl+C to stop.
echo.
REM run_local.py forces WindowsSelectorEventLoopPolicy before importing
REM uvicorn — this is the actual root-cause fix for the Windows hang
REM after the first browser disconnect. See run_local.py docstring.
.venv\Scripts\python.exe run_local.py
echo.
echo Server stopped. Press any key to close.
pause >nul
