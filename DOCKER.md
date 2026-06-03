# Jarvis - Docker Configuration

## Quick Start with Docker

If you prefer containerized setup:

```bash
# Build Docker image
docker build -t jarvis:latest .

# Run container with Ollama
docker run -it \
  --name jarvis \
  -p 8000:8000 \
  -v jarvis_data:/app/data \
  -v jarvis_logs:/app/logs \
  jarvis:latest

# For GPU support (NVIDIA)
docker run -it \
  --gpus all \
  --name jarvis \
  -p 8000:8000 \
  -v jarvis_data:/app/data \
  jarvis:latest
```

## Using Docker Compose

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  ollama:
    image: ollama/ollama:latest
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama
    environment:
      - OLLAMA_MODELS=/root/.ollama/models

  jarvis:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    environment:
      - OLLAMA_BASE_URL=http://ollama:11434
      - DEBUG=True
    depends_on:
      - ollama
    command: python -m uvicorn app.main:app --host 0.0.0.0 --port 8000

volumes:
  ollama_data:
```

Run with: `docker-compose up`

## Notes

- Ollama requires significant disk space for models
- GPU support needs NVIDIA Docker runtime
- Memory settings: Add `--memory 8g` for constraints
