"""
Jarvis Configuration Settings
Central configuration for all Jarvis components
"""
from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Jarvis"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    
    # LLM Settings
    LLM_MODEL: str = "deepseek-r1"  # Default model
    LLM_PROVIDER: str = "ollama"
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 2048
    LLM_NUM_PREDICT: int = 80  # Maximum tokens to predict
    LLM_REQUEST_TIMEOUT: int = 45  # Request timeout in seconds

    # OpenRouter (cloud, OpenAI-compatible). Used for the heavy roles in the
    # hybrid setup below. A model is routed to OpenRouter when its name carries
    # the "openrouter:" prefix (e.g. "openrouter:anthropic/claude-sonnet-4");
    # everything else stays on local Ollama. Paste your key into .env as
    # OPENROUTER_API_KEY — never commit it. Without a key, openrouter: models
    # are treated as unavailable and the router falls back to local.
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_BASE_URL: str = "https://openrouter.ai/api/v1"
    OPENROUTER_TIMEOUT: int = 90  # Cloud models can be slower than local
    # Optional attribution headers OpenRouter shows on your dashboard.
    OPENROUTER_SITE_URL: str = "http://localhost:8001"
    OPENROUTER_APP_NAME: str = "Jarvis"

    # Groq (cloud, OpenAI-compatible). Generous free tier (30 req/min). Prefix
    # models with "groq:" — e.g. "groq:llama-3.3-70b-versatile". Get a key at
    # https://console.groq.com/keys.
    GROQ_API_KEY: str = ""
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"
    GROQ_TIMEOUT: int = 60

    # Groq hosted Whisper for STT. When GROQ_API_KEY is set and
    # GROQ_WHISPER_ENABLED is True, /voice/transcribe tries Groq first and
    # falls back to local Whisper on any failure (network, rate limit, 5xx).
    # whisper-large-v3-turbo is the fast/cheap tier (~200ms typical, ~10% WER).
    GROQ_WHISPER_ENABLED: bool = True
    GROQ_WHISPER_MODEL: str = "whisper-large-v3-turbo"
    GROQ_WHISPER_TIMEOUT: int = 30

    # Google AI Studio / Gemini direct (OpenAI-compatible). Free tier on
    # gemini-2.0-flash-exp etc. Prefix models with "gemini:" — e.g.
    # "gemini:gemini-2.0-flash-exp". Get a key at https://aistudio.google.com/apikey.
    GEMINI_API_KEY: str = ""
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    GEMINI_TIMEOUT: int = 60

    # Multi-model routing. JSON-encoded {role: model_name} map. The router
    # picks a role from the detected intent (or an explicit per-request override)
    # and falls back to LLM_ROLE_FALLBACK when the chosen role's model isn't
    # available (pulled in Ollama, or — for openrouter: models — a key is set),
    # then to LLM_MODEL as a final fallback.
    #
    # Hybrid setup: fast/voice stay LOCAL (cheap, low latency, high frequency);
    # brain (deep reasoning) and coder go to OpenRouter for accuracy. Mixed
    # best-value picks: brain -> DeepSeek R1, coder -> Claude Sonnet.
    LLM_ROUTING_ENABLED: bool = True
    LLM_ROLE_MODELS_JSON: str = (
        '{"brain":"openrouter:deepseek/deepseek-r1",'
        '"coder":"openrouter:anthropic/claude-sonnet-4",'
        '"fast":"qwen2.5:0.5b","vision":"llava:7b"}'
    )
    LLM_ROLE_FALLBACK: str = "fast"
    
    # Voice Settings
    WAKE_WORD: str = "jarvis"
    VOICE_OUTPUT: str = "enabled"
    AUTO_START_VOICE_CONVERSATION: bool = True
    SPEECH_TO_TEXT_MODEL: str = "base"  # base, small, medium, large
    STT_LANGUAGE: str = "en"
    TTS_VOICE: str = "en-US-AmberNeural"
    AUDIO_SAMPLE_RATE: int = 16000
    AUDIO_INPUT_DEVICE: Optional[str] = "12"  # Microphone Array 2 - WORKING device
    VOICE_LISTEN_DURATION: int = 7  # Duration in seconds for microphone recording
    VOICE_MIN_PEAK_LEVEL: float = 0.001  # Minimum audio peak level to accept recording
    VOICE_RESPONSE_MAX_WORDS: int = 16  # Maximum words per voice response
    VOICE_STOP_KEYWORDS: str = "stop,exit,quit,goodbye"  # Keywords to stop voice conversation
    VOICE_MAX_EMPTY_ATTEMPTS: int = 3  # Max consecutive empty/silent recordings before exit
    
    # Memory Settings
    MEMORY_TYPE: str = "chroma"
    CHROMA_PATH: str = "./data/chroma"
    EMBEDDINGS_MODEL: str = "all-MiniLM-L6-v2"
    MEMORY_SEARCH_RESULTS: int = 3
    SESSIONS_DIR: str = "./data/sessions"
    SESSION_FILE: str = "current.json"

    # Advanced RAG ---------------------------------------------------------
    # Reranker (Upgrade A). When enabled, retrieve K*FETCH_MULTIPLIER candidates
    # from the vector store, then a cross-encoder scores (query, candidate)
    # pairs and the top K survive. Local model — no API cost. First call
    # downloads ~90MB to the persistent Chroma volume.
    MEMORY_RERANK_ENABLED: bool = True
    MEMORY_RERANK_MODEL: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    MEMORY_RERANK_FETCH_MULTIPLIER: int = 4  # fetch 4x what we ultimately return

    # Hybrid search (Upgrade B). When enabled, every retrieval runs vector
    # search AND BM25 keyword search in parallel, then merges via Reciprocal
    # Rank Fusion. Catches exact terms (URLs, IDs, file paths) that pure
    # cosine similarity often misses.
    MEMORY_HYBRID_ENABLED: bool = True
    MEMORY_HYBRID_RRF_K: int = 60  # standard RRF constant; lower = more weight to top ranks

    # Multi-source retrieval (Upgrade C). Which memory_type values to search
    # at retrieval time. "conversation" was the only one before; now projects,
    # tasks, and documents can join the same query.
    MEMORY_SEARCH_TYPES: str = "conversation,project,task,document"

    # File RAG (Upgrade D). Chunk size / overlap in characters (not tokens —
    # close enough for MiniLM at this size). Documents above
    # DOCUMENT_MAX_BYTES are rejected to keep ingestion sane.
    DOCUMENT_CHUNK_CHARS: int = 1200
    DOCUMENT_CHUNK_OVERLAP: int = 150
    DOCUMENT_MAX_BYTES: int = 10 * 1024 * 1024  # 10 MB
    DOCUMENT_STORE_DIR: str = "./data/documents"

    # MongoDB Atlas cloud sync (Phase D). Disabled by default — local files
    # remain the source of truth. When enabled, Hunt writes sessions, profile,
    # facts, and action history to Mongo on each change (fire-and-forget,
    # via a background worker thread). Set MONGO_ENABLED=True in .env and
    # paste your Atlas URI to turn it on.
    MONGO_ENABLED: bool = False
    MONGO_URI: str = ""
    MONGO_DB: str = "hunt"
    MONGO_DEVICE_ID: str = "default"
    
    # Automation Settings
    AUTOMATION_ENABLED: bool = True
    BROWSER_AUTOMATION: str = "playwright"
    
    # Web Search Settings
    WEB_SEARCH_ENABLED: bool = True
    WEB_SEARCH_RESULTS: int = 3
    
    # Vision Settings
    VISION_ENABLED: bool = True
    OCR_ENGINE: str = "easyocr"
    SCREEN_CAPTURE_FPS: int = 5
    
    # API Settings
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8001
    API_WORKERS: int = 1
    CORS_ORIGINS: list = ["*"]
    
    # Database
    DATABASE_URL: str = "sqlite:///./data/.db"
    
    # Paths
    BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    LOG_DIR: str = os.path.join(BASE_DIR, "logs")
    DATA_DIR: str = os.path.join(BASE_DIR, "data")
    
    # Security
    SECRET_KEY: str = "jarvis-secret-key-change-in-production"
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
