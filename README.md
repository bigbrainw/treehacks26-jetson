# Focus Agent

Tracks what apps, websites, and files you're using—and how long you've been on the same page. When you stay on one thing for too long, it triggers a mental state check (Emotiv EEG) and an **LLM agent** that organizes the data and decides when/how to offer help.

Built for TreeHacks 26.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│ ActivityMonitor │────▶│  SessionTracker  │────▶│   EEG Bridge    │
│ (app, page,     │     │ (time-on-task)  │     │ (mental state)   │
│  file)          │     │                  │     │ focused/stuck    │
└─────────────────┘     └────────┬────────┘     └────────┬────────┘
        │                         │                       │
        └─────────────────────────┴───────────────────────┘
                                  │
                          ┌───────▼───────┐
                          │    Storage    │──► recent sessions
                          └───────┬───────┘
                                  ▼
                          ┌───────────────┐
                          │  LLM Agent    │  organizes data, decides
                          │ (FocusAssistant) when/how to help
                          └───────────────┘
```

## Features

- **Activity tracking**: Active window, app name, window title (website in browser, file in editor)
- **Time-on-task**: How long on the same page
- **Thresholds**: Warn at 2 min, trigger EEG at 3 min
- **Emotiv Cortex API**: Performance metrics (engagement, attention, stress) → mental state
- **Multi-turn agent**: Asks questions → adjusts help from feedback → follows up. Greylock-style loop. Default: `MultiTurnAssistant`.

## Emotiv Cortex Setup

1. **Install EMOTIV Launcher** from [emotiv.com](https://www.emotiv.com) and log in with your EmotivID.
2. **Register an app** at [emotiv.com/developer](https://www.emotiv.com/developer) to get **Client ID** and **Client Secret**.
3. **Run Launcher** and connect your Emotiv headset (EPOC, Insight, MN8, etc.).
4. Set credentials:
   ```bash
   export EMOTIV_CLIENT_ID="your_client_id"
   export EMOTIV_CLIENT_SECRET="your_client_secret"
   ```
   Or add to `.env` (create from `.env.example`).

On first run, approve access in the Launcher when prompted.

## LLM Agent (Ollama - Edge / Jetson)

Runs locally. No cloud API. Use **Ollama** or any OpenAI-compatible endpoint:

```bash
# Install Ollama, then pull a Jetson-friendly model:
ollama pull phi3:mini    # or qwen2:1.5b, tinyllama
# Agent connects to http://localhost:11434/v1 by default
```

Optional env:

- `OLLAMA_BASE_URL` – default `http://localhost:11434/v1`
- `OLLAMA_MODEL` – default `qwen2.5:1.5b-instruct`

## Install & Run

```bash
cd treehacks26
pip install -r requirements.txt
python main.py
```

## Test Agent Without EEG

Full agent (activity, context handlers, web reader, multi-turn Ollama) but no Emotiv/EEG:

```bash
python test_agent_no_eeg.py                    # real activity, mental_state=stuck
python test_agent_no_eeg.py --dummy             # dummy mode (no X11)
python test_agent_no_eeg.py --mental distracted
python test_agent_no_eeg.py --warn 3 --long 6   # custom thresholds (sec)
```

## Test Activity Detection

Verify the agent sees what you're looking at (X11 desktop required):

```bash
python test_detect.py
```

Switch between browser, Cursor, terminal—each change should print the detected app and window title.

## Test with Dummy Data

Test the full pipeline without X11 or Emotiv:

```bash
python test_agent.py              # focused scenario (default)
python test_agent.py stuck        # high stress, moderate attention
python test_agent.py distracted  # low engagement
python test_agent.py switch      # task switching (Cursor → Firefox → Cursor)
```

- **Mental state**: Uses accelerated thresholds (warn 2s, long 4s) and dummy Emotiv metrics.
- **Task switching**: Simulates Cursor → Firefox (github) → Cursor; verifies context changes are detected and duration resets on each switch.

**Linux (X11)**: Uses `xprop` for activity detection. For Wayland, detection may be limited.

## Jetson Nano / Edge

Designed for edge deployment. No cloud dependencies for the LLM:

1. Install Ollama on the Jetson (or another machine on the network)
2. Pull a small model: `ollama pull phi3:mini` or `qwen2:1.5b`
3. Run the agent; it uses heuristics if Ollama is unreachable

## Docker + ngrok (Mac → Jetson over internet)

When Mac and Jetson are on different networks, use Docker + ngrok. **Mac does all monitoring** (activity + EEG); Jetson receives and runs the agent.

**On Jetson:**
```bash
# Add to .env: NGROK_AUTHTOKEN=<from https://dashboard.ngrok.com>
docker compose up -d

# Get ngrok URL (ngrok.yml uses 4041 to avoid conflict with SSH ngrok on 4040):
curl -s localhost:4041/api/tunnels | python -m json.tool
```

**Start on boot:** Both services use `restart: unless-stopped`. Ensure Docker starts on boot (`sudo systemctl enable docker`). For explicit compose-on-boot, install the systemd unit:
```bash
# Edit WorkingDirectory in compose.service to your project path, then:
sudo cp compose.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable compose.service
```

**On Mac:**
```bash
# Use wss:// if ngrok gives https (replace https -> wss)
JETSON_WS_URL=wss://YOUR_NGROK_URL python collector.py
```

Requirements: Ollama on Jetson; `NGROK_AUTHTOKEN` in `.env`. No X11 needed on Jetson—monitoring runs on Mac.

**Mac permissions:** For activity monitoring, allow "Accessibility" for Terminal (or your Python) in System Preferences → Privacy & Security.

**Collector sends:** Activity (active app/window) + EEG. **Processor receives via:** WebSocket (collector.py) or HTTP POST `/eeg` (StreamToJetson).

## Config (`config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `LONG_SESSION_THRESHOLD` | 180 (3 min) | Duration before mental state check |
| `WARN_SESSION_THRESHOLD` | 120 (2 min) | Early warning |
| `POLL_INTERVAL` | 2 sec | Activity poll interval |
| `OLLAMA_BASE_URL` | http://localhost:11434/v1 | Local LLM (Ollama) |
| `OLLAMA_MODEL` | phi3:mini | Small model for Jetson Nano |
| `MULTITURN_AGENT` | true | Use multi-turn agent (ask → adjust → follow up) |
| `FOLLOW_UP_INTERVAL` | 90 | Sec between follow-ups when still stuck |
| `FETCH_WEB_CONTENT` | true | Fetch page content when browsing (Firefox) |

## Mental State Mapping (Emotiv)

The **"met"** (performance metrics) stream provides:

- **EPOC/Insight/Flex**: `eng` (engagement), `attention`, `str` (stress), `rel` (relaxation)
- **MN8**: `attention`, `cognitiveStress`

Classification:

- **focused**: high engagement + attention, low stress
- **stuck**: high stress + moderate attention
- **distracted**: low engagement + attention

## Task-Specific Context Handlers

Different tasks use different context handlers (and future MCPs):

| Task | Handler | Web data |
|------|---------|----------|
| **Code** (Cursor, VS Code, vim) | `CodeHandler` | File from title |
| **Browser** (Firefox) | `BrowserHandler` | URL + page snippet from Firefox session |
| **Terminal** | `TerminalHandler` | Title hint |
| **Default** | `DefaultHandler` | Generic |

When you're in Firefox, the agent reads the active tab URL from `sessionstore-backups/recovery.jsonlz4` and can fetch page content for context. Set `FETCH_WEB_CONTENT=false` to disable fetching.

Add custom handlers via `ContextRouter.add_handler()`. To plug in an MCP, implement `enrich()` to call the MCP and return richer `extra_for_prompt`.

## Data

Sessions and EEG triggers stored in `data/sessions.db` (SQLite).
