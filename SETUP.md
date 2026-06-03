# Jarvis Setup & Installation Guide

Complete step-by-step guide to get Jarvis running on Windows.

## Prerequisites Checklist

- [ ] Python 3.10+ installed
- [ ] 16GB RAM recommended
- [ ] 50GB free disk space
- [ ] Administrator access for installations

## Step 1: Install Ollama (The Brain)

### Windows Installation

1. **Download Ollama**
   - Visit https://ollama.ai
   - Click "Download for Windows"
   - Run the installer
   - Follow installation wizard

2. **Start Ollama Server**
   - Open PowerShell as Administrator
   - Run: `ollama serve`
   - You should see: "Listening on 127.0.0.1:11434"
   - **Keep this terminal open** (server must be running)

3. **Pull a Model** (In new PowerShell)
   ```bash
   ollama pull deepseek-r1
   # or alternatives:
   ollama pull llama2
   ollama pull mistral
   ollama pull neural-chat
   ```

4. **Test Ollama**
   ```bash
   ollama list  # Should show downloaded models
   ```

## Step 2: Install Python Dependencies

### 1. Navigate to Jarvis Directory
```bash
cd "c:\Users\rayan\Downloads\Ai Doonz\Jarvis"
```

### 2. Create Virtual Environment
```bash
python -m venv venv
```

### 3. Activate Virtual Environment
```bash
# Windows:
venv\Scripts\activate

# You should see (venv) in your prompt
```

### 4. Install Dependencies
```bash
pip install --upgrade pip
pip install -r requirements.txt
```

**This will install:**
- FastAPI & Uvicorn (API server)
- LangChain & Ollama (LLM integration)
- Whisper & Piper (Voice I/O)
- ChromaDB (Memory)
- PyAutoGUI (Desktop automation)
- Playwright (Browser automation)
- And more...

### 5. Install Playwright Browsers (for web automation)
```bash
playwright install chromium
```

## Step 3: Install Optional Audio Dependencies

### For Better Audio Support (Windows)

```bash
# If you have pip wheels:
pip install pipwin
pipwin install pyaudio

# Or use conda if available:
conda install pyaudio
```

## Step 4: Configuration

### 1. Create .env File
```bash
# In the Jarvis folder, copy example:
copy .env.example .env
```

### 2. (Optional) Edit .env
The default settings work fine, but you can customize:

```env
# Your preferred model
LLM_MODEL=deepseek-r1

# Voice settings
WAKE_WORD=jarvis
VOICE_OUTPUT=enabled

# API port
API_PORT=8000
```

## Step 5: Test Installation

### Option A: CLI Test (Recommended First)

```bash
# Make sure you're in the Jarvis directory and venv is activated
python cli.py
```

You should see:
```
🤖 Welcome to Jarvis
Your Personal AI Assistant (v0.1.0)
```

Try commands:
```
You: help
You: What can you do?
You: Open notepad
You: exit
```

### Option B: API Test

**Terminal 1** (Keep running):
```bash
python -m uvicorn app.main:app --reload
```

**Terminal 2**:
```bash
# Test health check
curl http://localhost:8000/health

# Test chat
curl -X POST http://localhost:8000/chat `
  -H "Content-Type: application/json" `
  -d "{`"message`":`"Hello Jarvis`"}"
```

## Common Installation Issues

### Issue: "ModuleNotFoundError: No module named 'ollama'"

**Solution:**
```bash
# Activate venv first
venv\Scripts\activate

# Then install:
pip install langchain-community ollama
```

### Issue: "Connection refused" or "Cannot connect to Ollama"

**Solution:**
1. Check Ollama is running: `ollama serve` in separate terminal
2. Test connection: `curl http://localhost:11434/api/tags`
3. Restart Ollama if needed

### Issue: "Model not found"

**Solution:**
```bash
# See available models
ollama list

# Download a model
ollama pull deepseek-r1

# Try smaller model if slow
ollama pull mistral  # Smaller, faster
```

### Issue: Audio/Microphone not working

**Solution:**
```bash
# Reinstall PyAudio
pip uninstall pyaudio
pip install pyaudio

# Check if system sees audio
python -m sounddevice
```

### Issue: Playwright browser issues

**Solution:**
```bash
# Reinstall Playwright browsers
playwright install chromium --with-deps
```

## Recommended Settings for Performance

### For Lower Specs (8GB RAM)

```env
LLM_MODEL=mistral           # Lighter model
SPEECH_TO_TEXT_MODEL=tiny   # Smaller Whisper model
API_WORKERS=1               # Fewer workers
```

### For Better Performance (16GB+ RAM)

```env
LLM_MODEL=deepseek-r1       # Full featured model
SPEECH_TO_TEXT_MODEL=small
API_WORKERS=2
```

## Next Steps

1. **Try CLI First**
   ```bash
   python cli.py
   ```

2. **Explore Commands**
   - Type "help" to see available commands
   - Try "status" to check system
   - Try "memory" to see memory

3. **Integrate with Your Apps**
   - Use the REST API
   - Run server: `python -m uvicorn app.main:app`
   - Check docs: `http://localhost:8000/docs`

4. **Customize Configuration**
   - Edit `config/settings.py` for advanced settings
   - Add your own commands in `cli.py`

## Useful Commands

```bash
# Run CLI
python cli.py

# Run API server
python -m uvicorn app.main:app --reload

# Check installed packages
pip list | findstr jarvis

# Update all packages
pip install --upgrade -r requirements.txt

# Deactivate virtual environment
deactivate
```

## Environment Variables (Advanced)

Create `.env` file in Jarvis folder:

```env
# Core Settings
APP_NAME=Jarvis
DEBUG=True
LOG_LEVEL=INFO

# LLM Configuration
LLM_MODEL=deepseek-r1
OLLAMA_BASE_URL=http://localhost:11434
LLM_TEMPERATURE=0.7
LLM_MAX_TOKENS=2048

# Voice Configuration
WAKE_WORD=jarvis
VOICE_OUTPUT=enabled
SPEECH_TO_TEXT_MODEL=base
STT_LANGUAGE=en
TTS_VOICE=en-US-AmberNeural

# Memory Configuration
MEMORY_TYPE=chroma
EMBEDDINGS_MODEL=all-MiniLM-L6-v2

# Automation
AUTOMATION_ENABLED=True

# API Configuration
API_HOST=127.0.0.1
API_PORT=8000
API_WORKERS=1

# Database
DATABASE_URL=sqlite:///./data/jarvis.db
```

## Verification Checklist

- [ ] Ollama running on port 11434
- [ ] Python virtual environment activated
- [ ] All packages installed (`pip list`)
- [ ] CLI runs without errors (`python cli.py`)
- [ ] Can send requests to API (`curl http://localhost:8000/health`)
- [ ] Memory folder exists (`data/chroma`)
- [ ] Logs folder exists (`logs/`)

## Quick Troubleshooting

```bash
# Check Python version
python --version  # Should be 3.10+

# Verify venv is active
python -c "import sys; print(sys.prefix)"

# Check installed packages
pip list

# Test Ollama connection
python -c "from langchain.llms import Ollama; llm = Ollama(model='deepseek-r1'); print(llm.invoke('test'))"

# Clear cache and try again
rm -r data/chroma/* __pycache__/
```

## Getting Help

1. **Check logs:**
   ```bash
   type logs\*.log
   ```

2. **Test components:**
   ```bash
   python cli.py
   # Type: status
   ```

3. **Check docs:**
   - API docs: http://localhost:8000/docs (when running API)
   - README.md for architecture
   - This guide for setup issues

## Ready to Go!

Once everything is installed and working:

```bash
# Terminal 1: Keep Ollama running
ollama serve

# Terminal 2: Run Jarvis CLI
venv\Scripts\activate
python cli.py

# Or run API server:
python -m uvicorn app.main:app
```

**Enjoy your personal AI assistant! 🚀**
