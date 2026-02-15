"""Configuration for the focus/activity tracking agent."""

import os
from pathlib import Path

# Load .env if python-dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

# Data directory
DATA_DIR = Path(__file__).parent / "data"
DB_PATH = DATA_DIR / "sessions.db"

# Time thresholds (seconds)
# How long on same page before we consider checking mental state
LONG_SESSION_THRESHOLD = 180   # 3 minutes - trigger EEG check (stuck/focused?)
WARN_SESSION_THRESHOLD = 120   # 2 minutes - early warning

# Poll interval for activity monitoring (seconds)
POLL_INTERVAL = 2

# Emotiv Cortex API (from https://www.emotiv.com/developer)
EMOTIV_CLIENT_ID = os.environ.get("EMOTIV_CLIENT_ID", "")
EMOTIV_CLIENT_SECRET = os.environ.get("EMOTIV_CLIENT_SECRET", "")

# LLM Agent - local / edge (Ollama or OpenAI-compatible)
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "phi3:mini")  # small models for Jetson
MULTITURN_AGENT = os.environ.get("MULTITURN_AGENT", "true").lower() in ("1", "true", "yes")
FOLLOW_UP_INTERVAL = int(os.environ.get("FOLLOW_UP_INTERVAL", "90"))  # sec between follow-ups
FETCH_WEB_CONTENT = os.environ.get("FETCH_WEB_CONTENT", "true").lower() in ("1", "true", "yes")

# Split architecture: Mac (activity + EEG) -> Jetson (processor only)
# - Mac: collector.py - activity monitoring + EEG, sends both via WebSocket
# - Jetson: processor_main.py - receives activity + EEG, runs agent. No local monitoring.
JETSON_WS_URL = os.environ.get("JETSON_WS_URL", "ws://localhost:8765")
JETSON_WS_PORT = int(os.environ.get("JETSON_WS_PORT", "8765"))
