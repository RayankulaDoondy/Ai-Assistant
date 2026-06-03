#!/bin/bash
# Jarvis Setup Script for Linux/Mac

echo ""
echo "================================"
echo "Jarvis - AI Assistant Setup"
echo "================================"
echo ""

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python 3 not found. Please install Python 3.10+ first."
    exit 1
fi

echo "[✓] Python found"
echo ""

# Create virtual environment
if [ ! -d "venv" ]; then
    echo "[•] Creating virtual environment..."
    python3 -m venv venv
    echo "[✓] Virtual environment created"
else
    echo "[✓] Virtual environment already exists"
fi

echo ""

# Activate virtual environment
echo "[•] Activating virtual environment..."
source venv/bin/activate
echo "[✓] Virtual environment activated"
echo ""

# Install dependencies
echo "[•] Installing dependencies (this may take a few minutes)..."
pip install --upgrade pip > /dev/null
pip install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to install dependencies"
    exit 1
fi

echo "[✓] Dependencies installed"
echo ""

# Install Playwright browsers
echo "[•] Installing Playwright browsers..."
playwright install chromium > /dev/null 2>&1
echo "[✓] Playwright browsers installed"
echo ""

# Create data directories
mkdir -p data logs
echo "[✓] Directories created"
echo ""

# Copy .env if not exists
if [ ! -f ".env" ] && [ -f ".env.example" ]; then
    cp .env.example .env
    echo "[✓] Configuration file created (.env)"
fi

echo ""
echo "================================"
echo "Setup Complete!"
echo "================================"
echo ""
echo "Next steps:"
echo "1. Make sure Ollama is installed and running:"
echo "   - Download from https://ollama.ai"
echo "   - Run: ollama serve (in separate terminal)"
echo "   - Download a model: ollama pull deepseek-r1"
echo ""
echo "2. Start Jarvis CLI:"
echo "   python cli.py"
echo ""
echo "3. Or start API server:"
echo "   python -m uvicorn app.main:app --reload"
echo ""
echo "For more help, see SETUP.md or README.md"
echo ""
