# Focus Agent

Receives activity and EEG data, processes it, and helps the user when stuck. An **LLM agent** organizes the data and decides when/how to offer help. No local monitoring—data arrives via WebSocket or HTTP POST.

Built for TreeHacks 26.

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│  Incoming Data  │────▶│  SessionTracker  │────▶│   EEG Bridge    │
│ (WebSocket/HTTP)│     │ (time-on-task)  │     │ (mental state)   │
│ context + EEG   │     │                  │     │ focused/stuck    │
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

- **Data in**: Activity (app, window title, page content) and EEG via WebSocket or POST /eeg
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

## LLM Agent (Claude Agent SDK)

Uses the **Claude Agent SDK** when user is stuck—enables WebSearch and WebFetch for real synthesis (not raw link dumps).

```bash
pip install claude-agent-sdk
export ANTHROPIC_API_KEY="sk-ant-..."
```

- **Agent SDK** (default): When stuck, agent uses WebSearch + WebFetch to gather and summarize. Better output.
- **Fallback**: If SDK unavailable, uses Anthropic Messages API.

Optional env:

- `ANTHROPIC_API_KEY` – required
- `ANTHROPIC_MODEL` – default `claude-3-5-sonnet-20241022`
- `USE_AGENT_SDK` – set `false` to skip Agent SDK and use Messages API only

## Install & Run

```bash
cd treehacks26
pip install -r requirements.txt
export ANTHROPIC_API_KEY="sk-ant-..."
python processor_main.py --port 8765
```

## Test Agent Without EEG

Full agent (context handlers, web reader, multi-turn Claude) but no Emotiv/EEG:

```bash
python test_agent_no_eeg.py
python test_agent_no_eeg.py --mental distracted
python test_agent_no_eeg.py --warn 3 --long 6   # custom thresholds (sec)
```

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

## Test Reading Contexts (lectures, papers, articles)

5 test cases. Each case defines **only** what the user is reading. Prep (Bright Data, context handlers) comes from the program—not hardcoded in tests. Scenario: user stays on page (e.g. 30 sec), EEG shows stuck.

```bash
python test_reading_contexts.py --list      # list cases
python test_reading_contexts.py --context-only   # context handler only (quick)
python test_reading_contexts.py             # full run (default: 30s on page)
python test_reading_contexts.py --case 1 --long 5   # quick: 5s trigger
```

| # | What user is reading |
|---|----------------------|
| 1 | Attention Is All You Need — arXiv |
| 2 | CS224N Lecture 5 — Backpropagation |
| 3 | Understanding LLMs: A Survey — Nature |
| 4 | React Hooks — A Complete Guide |
| 5 | The Concept of Mind — Stanford SEP |


## Jetson Nano / Edge

1. Set `ANTHROPIC_API_KEY` in `.env`
2. Run `python processor_main.py`; it receives data via WebSocket/HTTP

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

Requirements: `ANTHROPIC_API_KEY` and `NGROK_AUTHTOKEN` in `.env`. Data arrives via WebSocket.

**Mac permissions:** For activity monitoring, allow "Accessibility" for Terminal (or your Python) in System Preferences → Privacy & Security.

**Collector sends:** Activity (active app/window) + EEG. **Processor receives via:** WebSocket (collector.py) or HTTP POST `/eeg` (StreamToJetson).

## Config (`config.py`)

| Setting | Default | Description |
|---------|---------|-------------|
| `LONG_SESSION_THRESHOLD` | 180 (3 min) | Duration before mental state check |
| `WARN_SESSION_THRESHOLD` | 120 (2 min) | Early warning |
| `POLL_INTERVAL` | 2 sec | Activity poll interval |
| `ANTHROPIC_API_KEY` | - | Required for Claude |
| `ANTHROPIC_MODEL` | claude-3-5-sonnet-20241022 | Claude model |
| `MULTITURN_AGENT` | true | Use multi-turn agent (ask → adjust → follow up) |
| `FOLLOW_UP_INTERVAL` | 90 | Sec between follow-ups when still stuck |
| `FETCH_WEB_CONTENT` | true | Fetch page content when browsing (Firefox) |

When the user is stuck, web search/synthesis uses **Claude Agent SDK** (WebSearch, WebFetch) for real-time help—no external SERP API.

## Mental Command → Order Pizza

Use Emotiv mental commands to trigger actions. Train one command (e.g. **push**) in Emotiv, then when detected:

1. **Collector** subscribes to `met` + `com` streams, sends mental commands to processor
2. **Processor** matches `MENTAL_COMMAND_PIZZA` (default: `push`) with power ≥ threshold
3. **Agent** uses Claude SDK (WebSearch/WebFetch) to find pizza options and links

**Setup:**
1. Train "push" (or another action) in Emotiv Launcher / Cortex
2. Set `MENTAL_COMMAND_PIZZA=push` in `.env`
3. Run collector + processor; think the command → pizza order help

**Test without headset:**
```bash
python test_mental_command_pizza.py                    # configured flow (Uber Eats or search)
python test_mental_command_pizza.py --uber-eats-only   # full Uber Eats browser flow, stop at pay
python test_mental_command_pizza.py --search-only      # Agent SDK search only
```

**MCPizza (Domino's):** Uses [MCPizza](https://github.com/GrahamMcBain/mcpizza)-style flow via `pizzapi` and the MCPizza API: find store → search menu → show order summary. **No order placed.** Set `PIZZA_DELIVERY_ADDRESS` for your location.

## Snack Suggestion (preferences + budget)

Let the agent decide what you're having for a late-night snack—give your preferences and a budget so suggestions stay affordable. **No order placed.**

**CLI:**
```bash
python -m agent.snack_suggestion "I like savory, not too heavy — chips and ice cream" 15
python -m agent.snack_suggestion "pizza and wings" $20 --address "475 Via Ortega, Stanford CA"
```

**HTTP (when processor is running):**
```bash
curl -X POST http://localhost:8765/suggest_snack \
  -H "Content-Type: application/json" \
  -d '{"preferences": "I like savory chips", "budget": 15}'
```

Optional `address` for delivery area. Uses WebSearch when available for real options (DoorDash, Uber Eats, etc.).

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
| **Browser** (Firefox) | `BrowserHandler` | URL + page snippet |
| **Terminal** | `TerminalHandler` | Title hint |
| **Default** | `DefaultHandler` | Generic |

When you're in Firefox, the agent reads the active tab URL from `sessionstore-backups/recovery.jsonlz4` and can fetch page content for context. Set `FETCH_WEB_CONTENT=false` to disable fetching.

Add custom handlers via `ContextRouter.add_handler()`. To plug in an MCP, implement `enrich()` to call the MCP and return richer `extra_for_prompt`.

## Data

Sessions and EEG triggers stored in `data/sessions.db` (SQLite).
