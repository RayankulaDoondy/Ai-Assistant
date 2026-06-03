# Jarvis - Quick Start Guide

Get Jarvis running in 5 minutes!

## What You'll Need

1. **Ollama** - Download from https://ollama.ai
2. **Python 3.10+** - Download from https://python.org
3. **This Jarvis project** - Already here!

## The 5-Minute Setup

### Step 1: Install Ollama (2 min)
```bash
# Download from https://ollama.ai
# Run installer, choose default options

# Then in PowerShell/Terminal:
ollama serve

# Keep this terminal open!
```

### Step 2: Download a Model (while Step 1 runs)
```bash
# In another PowerShell/Terminal:
ollama pull deepseek-r1

# Or alternatives:
ollama pull mistral     # Faster, smaller
ollama pull llama2      # General purpose
```

### Step 3: Setup Jarvis (2 min)
```bash
# Navigate to Jarvis folder
cd "c:\Users\rayan\Downloads\Ai Doonz\Jarvis"

# Run setup (Windows)
setup.bat

# Or manual:
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

### Step 4: Run Jarvis (1 min)
```bash
# Make sure venv is activated
venv\Scripts\activate

# Run CLI
python cli.py

# You should see welcome screen!
```

## Try These Commands

```
You: hello
Jarvis: Hello! I'm Jarvis, your AI assistant...

You: open notepad
Jarvis: Opening notepad...

You: what's 2+2?
Jarvis: 2+2 equals 4.

You: search for python
Jarvis: Searching for python...

You: help
Jarvis: [Shows available commands]

You: exit
Jarvis: Goodbye!
```

## Running the API Server

Instead of CLI, you can run the API:

```bash
# Activate venv first
venv\Scripts\activate

# Start server
python -m uvicorn app.main:app --reload

# Then test (in another terminal)
curl http://localhost:8000/health

# See interactive docs:
# Open browser to http://localhost:8000/docs
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| "Cannot connect to Ollama" | Make sure `ollama serve` is running |
| "Model not found" | Run `ollama pull deepseek-r1` |
| "ModuleNotFoundError" | Run `pip install -r requirements.txt` |
| "Python not found" | Install Python 3.10+ |

## What's Next?

1. **Explore CLI Commands**
   ```bash
   python cli.py
   # Try: help, status, memory
   ```

2. **Read the Docs**
   - `README.md` - Full documentation
   - `ARCHITECTURE.md` - How it works
   - `SETUP.md` - Detailed setup guide

3. **Customize It**
   - Edit `config/settings.py` for settings
   - Add commands in `cli.py`
   - Extend with new agents

4. **Next Phases** (See README for roadmap)
   - Web UI dashboard
   - Multi-agent system
   - Vision capabilities
   - Autonomous workflows

## API Endpoints Reference

```bash
# Check if running
GET /health

# Chat with Jarvis
POST /chat -d '{"message":"Hello"}'

# Store memory
POST /memory/store -d '{"content":"info","memory_type":"project"}'

# Retrieve memories
GET /memory/retrieve?query=important&limit=5

# Execute commands
POST /command -d '{"command":"open_app","parameters":{"app_name":"chrome"}}'

# System status
GET /status
```

## Useful Files

- `cli.py` - Command-line interface
- `app/main.py` - REST API server
- `brain/llm_engine.py` - AI brain
- `memory/memory_store.py` - Memory system
- `voice/voice_engine.py` - Voice I/O
- `automation/automation_engine.py` - Desktop/browser control

## Tips & Tricks

1. **Use smaller models for faster response:**
   ```bash
   ollama pull mistral
   # Then in settings: LLM_MODEL=mistral
   ```

2. **Clear memory if needed:**
   ```bash
   rm -r data/chroma/*
   ```

3. **Check what's installed:**
   ```bash
   pip list | findstr jarvis
   ```

4. **Run with specific settings:**
   ```python
   # Edit .env or config/settings.py
   LLM_TEMPERATURE=0.5  # More precise
   LLM_TEMPERATURE=0.9  # More creative
   ```

## Performance

- **Fast**: Mistral model
- **Balanced**: Neural-Chat model
- **Smart**: DeepSeek model
- **General**: Llama2 model

## System Requirements

| Aspect | Minimum | Recommended |
|--------|---------|-------------|
| RAM | 8GB | 16GB |
| Storage | 30GB | 50GB |
| Disk Speed | SSD | Fast SSD |
| CPU | 4-core | 8-core |
| GPU | Optional | RTX 3060+ |

## Keep Ollama Running

**Always keep this terminal open:**
```bash
ollama serve
```

If you close it, Jarvis won't work. When you want to stop:
```
Ctrl+C
```

Then to restart:
```bash
ollama serve
```

---

**That's it! You now have a personal AI assistant running locally on your machine.** 🚀

For more details, read the full documentation in README.md or SETUP.md
