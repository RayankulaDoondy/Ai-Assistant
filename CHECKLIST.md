# Getting Started Checklist

Use this checklist to get Jarvis up and running.

## Pre-Installation

- [ ] Python 3.10+ installed (check: `python --version`)
- [ ] 16GB RAM available
- [ ] 50GB free disk space
- [ ] Administrator access for installations

## Step 1: Ollama Setup

- [ ] Download Ollama from https://ollama.ai
- [ ] Install Ollama (run installer)
- [ ] Open terminal and run: `ollama serve`
- [ ] In another terminal, run: `ollama pull deepseek-r1`
- [ ] Test: `ollama list` (should show model)

## Step 2: Jarvis Setup

- [ ] Navigate to Jarvis folder: `cd "c:\Users\rayan\Downloads\Ai Doonz\Jarvis"`
- [ ] Run setup script: `setup.bat` (Windows) or `bash setup.sh` (Mac/Linux)
- [ ] Wait for installation to complete
- [ ] Verify installation: `python verify_install.py`

## Step 3: First Run

- [ ] Open terminal (activate venv: `venv\Scripts\activate`)
- [ ] Run: `python cli.py`
- [ ] See welcome message
- [ ] Type: `help` to see commands

## Step 4: Try It Out

### Chat
- [ ] "Hello Jarvis"
- [ ] "What's your name?"
- [ ] "What can you do?"

### Automation
- [ ] "Open notepad"
- [ ] "Search for Python"
- [ ] "Close notepad"

### Memory
- [ ] "Remember I like Python"
- [ ] "What do you know about me?"
- [ ] "memory" (view stored memories)

### System
- [ ] "status" (check system)
- [ ] "models" (list available models)
- [ ] "clear" (clear context)

## Step 5: API Server (Optional)

- [ ] Run: `python -m uvicorn app.main:app --reload`
- [ ] Visit: http://localhost:8000/docs
- [ ] Try POST /chat endpoint
- [ ] Test GET /health

## Troubleshooting

| Problem | Fix |
|---------|-----|
| "Cannot connect to Ollama" | Run `ollama serve` in separate terminal |
| "ModuleNotFoundError" | Run `pip install -r requirements.txt` |
| Slow response | Use smaller model: `ollama pull mistral` |
| Virtual env not working | Try: `python -m venv venv` then activate |

## After Setup

- [ ] Read README.md for full documentation
- [ ] Check ARCHITECTURE.md to understand design
- [ ] Review ROADMAP.md for future features
- [ ] Explore cli.py to see available commands
- [ ] Customize config/settings.py

## Next Steps

### Immediate (Today)
- [ ] Get CLI working
- [ ] Try all commands
- [ ] Explore memory system
- [ ] Test automation features

### This Week
- [ ] Set up API server
- [ ] Write custom commands
- [ ] Integrate with your workflow
- [ ] Report issues/feedback

### Later
- [ ] Phase 2: Web UI
- [ ] Phase 2: Multi-agent system
- [ ] Phase 3: Autonomous workflows
- [ ] Contribute improvements

## Quick Commands

```bash
# Setup (Windows)
setup.bat

# Activate environment
venv\Scripts\activate

# Run CLI
python cli.py

# Run API
python -m uvicorn app.main:app --reload

# Verify installation
python verify_install.py

# See all packages
pip list

# Ollama commands
ollama serve              # Start Ollama
ollama pull deepseek-r1   # Download model
ollama list               # Show models
```

## Key Files to Know

- `cli.py` - Command-line interface (start here!)
- `app/main.py` - REST API backend
- `brain/llm_engine.py` - AI engine
- `memory/memory_store.py` - Memory system
- `config/settings.py` - Configuration
- `README.md` - Full documentation

## Resources

- **Ollama**: https://ollama.ai
- **LangChain**: https://python.langchain.com
- **FastAPI**: https://fastapi.tiangolo.com
- **ChromaDB**: https://docs.trychroma.com

## Getting Help

1. Check README.md
2. Read SETUP.md for detailed steps
3. Review error messages in terminal
4. Check logs/ folder for detailed logs
5. Run `python verify_install.py` to diagnose issues

---

## ✓ Ready?

Once you've checked everything above:

```bash
python cli.py
```

Type `help` to get started!

**Enjoy your personal AI assistant! 🤖**
