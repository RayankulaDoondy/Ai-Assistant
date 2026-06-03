@echo off
REM Jarvis Setup Script for Windows
REM This script automates the setup process

echo.
echo ================================
echo Jarvis - AI Assistant Setup
echo ================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python not found. Please install Python 3.10+ first.
    pause
    exit /b 1
)

echo [✓] Python found
echo.

REM Create virtual environment
if not exist "venv" (
    echo [•] Creating virtual environment...
    python -m venv venv
    echo [✓] Virtual environment created
) else (
    echo [✓] Virtual environment already exists
)

echo.

REM Activate virtual environment
echo [•] Activating virtual environment...
call venv\Scripts\activate.bat

echo [✓] Virtual environment activated
echo.

REM Install dependencies
echo [•] Installing dependencies (this may take a few minutes)...
pip install --upgrade pip >nul
pip install -r requirements.txt

if %errorlevel% neq 0 (
    echo ERROR: Failed to install dependencies
    pause
    exit /b 1
)

echo [✓] Dependencies installed
echo.

REM Install Playwright browsers
echo [•] Installing Playwright browsers...
playwright install chromium >nul 2>&1
echo [✓] Playwright browsers installed
echo.

REM Create data directories
if not exist "data" mkdir data
if not exist "logs" mkdir logs
echo [✓] Directories created
echo.

REM Copy .env if not exists
if not exist ".env" (
    if exist ".env.example" (
        copy .env.example .env
        echo [✓] Configuration file created (.env)
    )
)

echo.
echo ================================
echo Setup Complete!
echo ================================
echo.
echo Next steps:
echo 1. Make sure Ollama is installed and running:
echo    - Download from https://ollama.ai
echo    - Run: ollama serve (in separate terminal)
echo    - Download a model: ollama pull deepseek-r1
echo.
echo 2. Start Jarvis CLI:
echo    python cli.py
echo.
echo 3. Or start API server:
echo    python -m uvicorn app.main:app --reload
echo.
echo For more help, see SETUP.md or README.md
echo.

pause
