@echo off
REM Hunt server launcher. Double-click to start.
REM Window stays open while the server runs; close it to stop.
cd /d "%~dp0"
title Hunt server (port 8001)
echo Starting Hunt on http://127.0.0.1:8001 ...
echo Press Ctrl+C to stop.
echo.
REM --loop asyncio + --http h11 avoid a Windows/Python 3.13 bug in uvicorn's
REM Proactor event loop where the server hangs after a client connection
REM reset. No --reload because it kills in-flight requests on every file save.
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --loop asyncio --http h11
echo.
echo Server stopped. Press any key to close.
pause >nul
