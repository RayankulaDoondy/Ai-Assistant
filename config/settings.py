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
