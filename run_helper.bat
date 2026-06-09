@echo off
REM ============================================================================
REM Hunt Desktop Helper — Windows-native action executor
REM
REM Run this in a separate console window before using Hunt's action chips
REM (open chrome / close X / open URL / search). Hunt's Docker container will
REM POST action requests to this script, which executes them on your real
REM Windows desktop.
REM
REM Stop with Ctrl+C or close this window. See DESKTOP_HELPER.md for details.
REM ============================================================================

title Hunt Desktop Helper

echo.
echo  =========================================================
echo   Hunt Desktop Helper
echo   Listening on: http://127.0.0.1:9100
echo   Hunt's action chips will work while this window is open.
echo   Press Ctrl+C to stop.
echo  =========================================================
echo.

REM %~dp0 expands to the directory containing this .bat file, so this works
REM regardless of where the user double-clicks from.
python "%~dp0automation\desktop_helper.py"

REM Pause on exit so any final error messages stay visible if Python crashed.
echo.
echo Helper stopped.
pause
