# Jarvis - Personal AI Assistant

🤖 **Your intelligent personal AI assistant** combining local LLMs, voice interaction, automation, memory, and multi-agent intelligence.

```
                       ┌────────────────────┐
                       │   USER (VOICE/UI)  │
                       └─────────┬──────────┘
                                 │
                     ┌───────────▼───────────┐
                     │   INPUT LAYER         │
                     │───────────────────────│
                     │ Voice Input           │
                     │ Text Input            │
                     │ Screen Capture        │
                     └───────────┬───────────┘
                                 │
                         ┌───────▼──────────┐
                         │  MAIN BRAIN      │
                         │  (DeepSeek LLM)  │
                         └───────┬──────────┘
                                 │
                    ┌────────────┼────────────┐
                    ▼            ▼            ▼
            ┌─────────────┐ ┌──────────┐ ┌──────────┐
            │Memory       │ │Automation│ │Task      │
            │System       │ │Layer     │ │Planner   │
            └─────────────┘ └──────────┘ └──────────┘
```

## 🚀 Phase 1 (Current) - Basic Assistant

### Features Implemented
- ✅ **Local LLM Engine** - DeepSeek/Llama via Ollama
- ✅ **Voice I/O** - Whisper (STT) + Piper (TTS)
- ✅ **Semantic Memory** - ChromaDB vector database
- ✅ **Desktop Automation** - Open/close apps, click, type
- ✅ **Browser Control** - Search, navigate, fill forms
- ✅ **Context Management** - Conversation history & reasoning
- ✅ **FastAPI Backend** - REST API with WebSocket support
- ✅ **CLI Interface** - Rich terminal UI for testing

## 📋 Requirements

### System
- **RAM**: 16GB recommended (8GB minimum)
- **Storage**: 50GB for models
- **GPU**: Optional (works on CPU)

### Software
- Python 3.10+
- Ollama (for local LLMs)
- FFmpeg (for audio processing)

## 🛠️ Installation

### 1. Setup Ollama (Required)

```bash
# Download from https://ollama.ai
# Or on Linux:
curl https://ollama.ai/install.sh | sh

# Pull a model
ollama pull deepseek-r1
# or: ollama pull llama2, ollama pull mistral, etc.

# Start Ollama server (keep running)
ollama serve
```

### 2. Clone & Setup Jarvis

```bash
cd "c:\Users\rayan\Downloads\Ai Doonz"
cd Jarvis

# Create virtual environment
python -m venv venv
venv\Scripts\activate  # On Windows

# Install dependencies
pip install -r requirements.txt

# Setup Piper TTS (Windows specific)
python -m pip install piper-tts
```

### 3. Configuration

```bash
# Copy example config
cp .env.example .env

# Edit .env if needed (optional, defaults work fine)
```

## 🎯 Quick Start

### Option A: CLI (Local Testing)
```bash
python cli.py

# Then type:
# "What's the weather?"
# "Open VS Code"
# "Search for Python tutorials"
```

### Option B: API Server
```bash
# Terminal 1: Start API server
python -m uvicorn app.main:app --reload

# Terminal 2: Test the API
curl http://localhost:8000/health
curl -X POST http://localhost:8000/chat -H "Content-Type: application/json" -d '{"message":"Hello Jarvis"}'
```

### Option C: Web Interface (Coming Soon)
```bash
cd ui/frontend
npm start
```

## 📚 API Endpoints

### Chat
```bash
POST /chat
{
  "message": "What can you do?",
  "include_context": true,
  "memory_type": "conversation"
}
```

### Memory
```bash
POST /memory/store
{
  "content": "Important information",
  "memory_type": "project",
  "metadata": {"project": "Jarvis"}
}

GET /memory/retrieve?query=Jarvis&limit=5
```

### Commands
```bash
POST /command
{
  "command": "open_app",
  "parameters": {"app_name": "vs code"}
}
```

### Status
```bash
GET /health
GET /status
```

## 💾 Project Structure

```
Jarvis/
├── app/                  # FastAPI backend
│   └── main.py          # API routes
├── brain/               # Core AI/LLM
│   ├── llm_engine.py    # Ollama integration
│   └── context_manager.py
├── memory/              # ChromaDB memory
│   └── memory_store.py
├── voice/               # Whisper + Piper
│   └── voice_engine.py
├── automation/          # PyAutoGUI + Playwright
│   └── automation_engine.py
├── agents/              # Multi-agent framework
├── vision/              # OpenCV + OCR (Phase 2)
├── ui/                  # Frontend (Phase 2)
├── config/              # Settings
├── data/                # Vector DB & cache
├── logs/                # Logs
├── cli.py               # CLI interface
├── requirements.txt
└── README.md
```

## 🎮 Usage Examples

### 1. Chat Interaction
```
You: What's your name?
Jarvis: I'm Jarvis, your personal AI assistant built with DeepSeek and local intelligence.

You: Open VS Code
Jarvis: Opening VS Code... ✓ Opened vs code

You: Search for Python tutorials
Jarvis: Searching for "Python tutorials"... ✓ Searched for: Python tutorials
```

### 2. Memory System
```
You: Remember that I'm working on a Flask API
Jarvis: ✓ I'll remember that for our session

You: What am I working on?
Jarvis: You mentioned you're working on a Flask API
```

### 3. Automation
```
You: Open Chrome and search for Python documentation
Jarvis: Opening browser... Searching... Done!
```

## 🔧 Configuration

Edit `config/settings.py` or `.env`:

```env
# LLM
LLM_MODEL=deepseek-r1          # or llama2, mistral, etc.
OLLAMA_BASE_URL=http://localhost:11434
LLM_TEMPERATURE=0.7

# Voice
WAKE_WORD=jarvis
SPEECH_TO_TEXT_MODEL=base       # tiny, base, small, medium, large
TTS_VOICE=en-US-AmberNeural

# Memory
MEMORY_TYPE=chroma
EMBEDDINGS_MODEL=all-MiniLM-L6-v2

# API
API_HOST=127.0.0.1
API_PORT=8000
```

## 🚦 Troubleshooting

### "Cannot connect to Ollama"
```bash
# Make sure Ollama is running:
ollama serve

# Check connection:
curl http://localhost:11434/api/tags
```

### "Model not found"
```bash
# List models
ollama list

# Pull a model
ollama pull deepseek-r1
```

### Memory errors
```bash
# Clear memory cache
rm -rf data/chroma/*

# Reinstall ChromaDB
pip install --upgrade chromadb
```

### Audio issues (Windows)
```bash
# Install Python audio library
pip install pyaudio

# Or use pip wheels:
pip install pipwin
pipwin install pyaudio
```

## 📈 Development Roadmap

### ✅ Phase 1 (Current)
- [x] Local LLM integration
- [x] Voice I/O
- [x] Memory system
- [x] Desktop/browser automation
- [x] REST API

### 🔄 Phase 2 (Next)
- [ ] Multi-agent system
- [ ] Vision/OCR capabilities
- [ ] Web UI dashboard
- [ ] Advanced task planning
- [ ] File intelligence

### 🎯 Phase 3 (Advanced)
- [ ] Autonomous workflows
- [ ] Self-improvement
- [ ] Predictive actions
- [ ] Mobile sync
- [ ] Continuous learning

## 🔐 Security

- 🔒 All AI processing is **local** (no cloud)
- 🔒 Memory stored locally only
- 🔒 No personal data sent anywhere
- ⚠️ Change `SECRET_KEY` in production

## 📖 Additional Resources

- [Ollama Models](https://ollama.ai/library)
- [LangChain Docs](https://python.langchain.com)
- [ChromaDB Docs](https://docs.trychroma.com)
- [FastAPI Docs](https://fastapi.tiangolo.com)

## 🤝 Contributing

This is a personal project. Feel free to fork and customize!

## 📝 License

MIT License - Use freely

---

**Built with ❤️ using local AI, voice, memory, and automation**

*Questions? Check the docs or run `python cli.py` and type "help"*
