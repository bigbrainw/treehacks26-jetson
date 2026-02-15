"""Configuration for the focus/activity tracking agent."""

import os
from pathlib import Path

# Load .env
_env_file = Path(__file__).parent / ".env"
try:
    from dotenv import load_dotenv
    load_dotenv(_env_file)
except ImportError:
    # Fallback: manual parse for critical vars
    if _env_file.exists():
        for line in _env_file.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# Data directory
DATA_DIR = Path(__file__).parent / "data"
_default_db = DATA_DIR / "sessions.db"
_user_db = Path.home() / ".local" / "share" / "focus_agent" / "sessions.db"
# Use default if writable; else user-local path (avoids readonly when sessions.db is root-owned)
if (_default_db.exists() and os.access(_default_db, os.W_OK)) or (not _default_db.exists() and os.access(DATA_DIR, os.W_OK)):
    DB_PATH = _default_db
else:
    _user_db.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH = _user_db

# Time thresholds (seconds)
# How long on same page before we consider checking mental state
LONG_SESSION_THRESHOLD = int(os.environ.get("LONG_SESSION_THRESHOLD", "30"))   # 30s for testing (prod: 180)
WARN_SESSION_THRESHOLD = int(os.environ.get("WARN_SESSION_THRESHOLD", "20"))   # 20s early warning (prod: 120)
# Min seconds between help triggers (avoid rapid-fire feedback)
FEEDBACK_COOLDOWN_SEC = int(os.environ.get("FEEDBACK_COOLDOWN_SEC", "30"))   # 30s for testing (prod: 180)

# Poll interval for activity monitoring (seconds)
POLL_INTERVAL = 2

# Emotiv Cortex API (from https://www.emotiv.com/developer)
EMOTIV_CLIENT_ID = os.environ.get("EMOTIV_CLIENT_ID", "")
EMOTIV_CLIENT_SECRET = os.environ.get("EMOTIV_CLIENT_SECRET", "")

# Mental commands: train one action (e.g. "push") in Emotiv; when detected, trigger pizza order
MENTAL_COMMAND_PIZZA = os.environ.get("MENTAL_COMMAND_PIZZA", "push")
MENTAL_COMMAND_POWER_THRESHOLD = float(os.environ.get("MENTAL_COMMAND_POWER_THRESHOLD", "0.5"))

# Pizza delivery address (MCPizza/Domino's/Zomato)
PIZZA_DELIVERY_ADDRESS = os.environ.get("PIZZA_DELIVERY_ADDRESS", "475 Via Ortega, Stanford, CA 94305")
# Provider: mcpizza (Domino's) | zomato (Zomato MCP - requires ZOMATO_ACCESS_TOKEN)
PIZZA_PROVIDER = os.environ.get("PIZZA_PROVIDER", "mcpizza").lower()
# Zomato MCP OAuth token - obtain via Postman (oauth.pstmn.io) or Cursor MCP; see agent/zomato_mcp_client.py
ZOMATO_ACCESS_TOKEN = os.environ.get("ZOMATO_ACCESS_TOKEN", "").strip()

# Uber Eats flow (optional, deprecated in favor of MCPizza)
UBER_EATS_FLOW_ENABLED = os.environ.get("UBER_EATS_FLOW_ENABLED", "false").lower() in ("1", "true", "yes")
UBER_EATS_DELIVERY_ADDRESS = os.environ.get("UBER_EATS_DELIVERY_ADDRESS", PIZZA_DELIVERY_ADDRESS)
# Headless = true for servers/Jetson without display; false to watch the flow in a browser window
UBER_EATS_HEADLESS = os.environ.get("UBER_EATS_HEADLESS", "true").lower() in ("1", "true", "yes")

# LLM Agent - Anthropic Claude
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
USE_AGENT_SDK = os.environ.get("USE_AGENT_SDK", "true").lower() in ("1", "true", "yes")
MULTITURN_AGENT = os.environ.get("MULTITURN_AGENT", "true").lower() in ("1", "true", "yes")
FOLLOW_UP_INTERVAL = int(os.environ.get("FOLLOW_UP_INTERVAL", "60"))  # 60s between follow-ups for testing (prod: 300)
FETCH_WEB_CONTENT = os.environ.get("FETCH_WEB_CONTENT", "true").lower() in ("1", "true", "yes")

# Split architecture: Mac (activity + EEG) -> Jetson (processor only)
# - Mac: collector.py - activity monitoring + EEG, sends both via WebSocket
# - Jetson: processor_main.py - receives activity + EEG, runs agent. No local monitoring.
JETSON_WS_URL = os.environ.get("JETSON_WS_URL", "ws://localhost:8765")
JETSON_WS_PORT = int(os.environ.get("JETSON_WS_PORT", "8765"))
# HTTP base URL for Mac app (POST /eeg, etc). Derived from JETSON_WS_URL if not set.
JETSON_BASE = os.environ.get("JETSON_BASE", "").strip() or (
    JETSON_WS_URL.replace("wss://", "https://").replace("ws://", "http://").rstrip("/").split("/ws")[0]
    if "/ws" in JETSON_WS_URL else JETSON_WS_URL.replace("wss://", "https://").replace("ws://", "http://").rstrip("/")
)
# Feedback overlay (FeedbackWindow) polls this URL for agent messages
FEEDBACK_POLL_URL = os.environ.get("FEEDBACK_POLL_URL", "").strip() or None  # default: derived from JETSON_WS_URL
