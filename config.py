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
FOLLOW_UP_INTERVAL = int(os.environ.get("FOLLOW_UP_INTERVAL", "90"))  # sec between follow-ups
FETCH_WEB_CONTENT = os.environ.get("FETCH_WEB_CONTENT", "true").lower() in ("1", "true", "yes")

# Split architecture: Mac (activity + EEG) -> Jetson (processor only)
# - Mac: collector.py - activity monitoring + EEG, sends both via WebSocket
# - Jetson: processor_main.py - receives activity + EEG, runs agent. No local monitoring.
JETSON_WS_URL = os.environ.get("JETSON_WS_URL", "ws://localhost:8765")
JETSON_WS_PORT = int(os.environ.get("JETSON_WS_PORT", "8765"))
