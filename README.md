# Talos

An AI agent that watches your activity across Slack, Google Calendar, and Notion, detects repeated multi-step workflows, and proposes saving them as reusable tools — all without you having to define anything upfront.

---

## How It Works

The agent runs continuously in the background. Every 15 seconds it runs a LangGraph pipeline for each registered user:

```
poll → cluster → llm → pattern
                          |
                    no pattern → END
                          |
                     pattern found
                          ↓
                       propose → human [INTERRUPT — waits for Slack reply]
                                           ↓
                                        confirm
                                           |
                              ┌────────────┼────────────┐
                           "yes"     change request     "no"
                              ↓            ↓              ↓
                           executor   re_propose → human  END
                              ↓        (loop until yes/no)
                             END
```

When a user runs a saved tool, a separate single-node `execute_graph` runs it with a live Slack checklist.

### Agent Nodes

| Node | What it does |
|---|---|
| `poll` | Fetches new events from Google Calendar, Notion, and Slack |
| `cluster` | Groups events that happen within a 10-minute window |
| `llm` | Calls Ollama (`llama3.1:8b`) on each cluster to decide if it's a routine |
| `pattern` | Finds sequences that repeat 2+ times and aren't already saved |
| `propose` | Generates a full tool definition via Ollama (with named `args`), DMs the user |
| `human` | LangGraph interrupt — pauses the graph until the user replies |
| `confirm` | Routes to `executor` (yes), `re_propose` (change request), or `END` (no) |
| `re_propose` | Applies a natural-language change request via Ollama and re-sends the proposal |
| `executor` | Calls Ollama to generate a custom `execute(args: dict)` function for the tool |
| `execute` | Runs a saved tool and posts a live-updating Slack checklist |

### Background Threads

| Thread | Interval | What it does |
|---|---|---|
| `agent` | every 15s | Runs the full LangGraph pipeline for all registered users |
| `setup-checker` | every 1h | Scans the workspace, auto-registers new members, DMs anyone missing calendar auth |
| Slack bot | — | Socket Mode listener (blocking, keeps the process alive) |

---

## Prerequisites

- Python 3.11+
- [Ollama](https://ollama.com) running locally with `llama3.1:8b` pulled — required for pattern **detection** (`llm_node`). The proposal, re-proposal, and executor-generation steps degrade gracefully to simpler fallbacks without it, but no new patterns will ever be detected if Ollama isn't running.
- A Slack workspace where you can create apps
- A Notion integration (optional)
- A Google Calendar OAuth credential (optional)

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/itmesneha/Talos
cd Talos
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull the local LLM

```bash
ollama pull llama3.1:8b
```

### 3. Create a Slack App

The easiest way is to import the manifest:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Select your workspace → paste the contents of `slack_manifest.json` → create

This automatically configures all scopes, slash commands, socket mode, and event subscriptions.

Alternatively, create **From scratch** and configure manually:

**Enable Socket Mode:**
- **Socket Mode** → Enable Socket Mode
- **Basic Information** → **App-Level Tokens** → Generate a token with `connections:write` scope → this is your `SLACK_APP_TOKEN` (`xapp-...`)

**Add Bot Token Scopes** (under **OAuth & Permissions → Scopes → Bot Token Scopes**):
- `channels:history`
- `chat:write`
- `commands`
- `im:history`
- `im:read`
- `im:write`
- `users:read`

**Subscribe to Bot Events** (under **Event Subscriptions**):
- `app_home_opened`
- `message.channels`
- `message.im`
- `team_join`

**Install the app** to your workspace → copy the **Bot User OAuth Token** (`xoxb-...`) → this is your `SLACK_BOT_TOKEN`

**Get the Signing Secret:**
- **Basic Information** → **App Credentials** → copy **Signing Secret** → this is your `SLACK_SIGNING_SECRET`

**Get the channel ID** for a channel the bot should monitor and post to:
- Right-click the channel in Slack → **View channel details** → copy the ID at the bottom (starts with `C`) → this is your `SLACK_CHANNEL`

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=...
SLACK_CHANNEL=C0...

# Notion (optional)
NOTION_TOKEN=secret_...
NOTION_DB_ID=...
```

### 5. Google Calendar (optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project → enable the **Google Calendar API**
2. **APIs & Services → Credentials** → create an **OAuth 2.0 Client ID** (Desktop app) → download as `credentials.json` into the project root

The bot handles the rest automatically. When it starts, it scans the workspace and DMs any user who hasn't connected their calendar yet with an OAuth link. The user clicks it, approves in the browser, and the token is saved. A local callback server runs on `http://localhost:8080` to receive the OAuth redirect.

---

## Running

```bash
python main.py
```

On startup the bot will:
1. Start the OAuth callback server on `localhost:8080`
2. Connect to Slack via Socket Mode
3. Scan the workspace — auto-register any members not yet in `users.json`, and DM anyone missing calendar auth
4. Begin the agent loop (runs every 15 seconds)

### Manual triggers (for testing)

Send these as a message to Talos (DM or any channel it's been invited to):

| Command | What it does |
|---|---|
| `!poll now` | Immediately poll and run detection, then DM you if a pattern is found |
| `!fake proposal` | Skip detection entirely and send yourself a demo proposal DM — useful for previewing the Save/Change/Discard buttons without needing real repeated activity or Ollama |

### Seeding mock events (for demos)

`seed.py` contains mock events for 10 users with a variety of workflow patterns. It is currently commented out (live polling is active). To restore mock data for a demo:

1. Open `seed.py` and uncomment the `events` list and `write_json` call
2. Run `python seed.py`

---

## Interacting with the Bot

### Slash commands

| Command | What it does |
|---|---|
| `/setup` | Sends you a Google Calendar OAuth link |
| `/tool` | Lists your saved tools with argument hints |
| `/tool weekly_sync person=Alice` | Runs the `weekly_sync` tool with `person=Alice` |
| `/tool prep_meeting person=Alice topic=Q3` | Runs a tool with multiple arguments |

### App Home

Open Talos's App Home tab in Slack to see:

- **Your saved tools** — each shown as a card with a **▶ Run** button. Tools with no arguments run immediately in a DM; tools with arguments open a modal to fill them in.
- **Recent activity** — the last 8 events Talos has observed for you (calendar/Notion/Slack), newest first.
- A **🔄 Refresh** button to re-pull the view.

If you haven't connected Google Calendar yet, Home shows a **Connect Google Calendar** button instead (opens the OAuth link directly).

### When a pattern is detected

Talos DMs you a proposal card with the sequence, proposed tool name/description, steps, arguments, and example usage, followed by three buttons:

- **✅ Save** — saves the tool immediately
- **✏️ Change** — opens a modal to describe what you'd like different (e.g. _call it prep_meeting_ or _add a topic argument_); Talos applies it via Ollama and re-sends an updated proposal with fresh buttons. This loops until you Save or Discard.
- **❌ Discard** — drops the proposal

You can also just reply in the thread with a change request in plain English instead of clicking Change — both work.

### Running a saved tool

```
/tool weekly_sync person=Alice
```

Or just message the bot directly:

```
weekly_sync person=Alice
```

Positional args also work if you only have one argument:

```
weekly_sync Alice
```

The bot posts a live Slack checklist that ticks off each step as it completes.

---

## Executor Sandbox

When a tool is confirmed, `executor_node` calls Ollama to generate a custom `execute(args: dict)` function. The generated code runs in a restricted sandbox with only these helper functions available:

| Function | What it does |
|---|---|
| `check_calendar(person)` | Returns the next upcoming calendar event involving the person |
| `check_notion(person)` | Returns open Notion tasks mentioning the person |
| `create_calendar_event(person, topic, time_str, duration_minutes)` | Creates a calendar event |
| `add_notion_page(title, notes)` | Creates a new Notion page |
| `post_slack(text)` | Posts a message to the default Slack channel |
| `draft_message(person, calendar_result, notion_result)` | Builds a check-in message combining calendar and Notion results |

---

## Project Structure

```
main.py            — entry point, starts all threads
agent_graph.py     — LangGraph graph wiring and public API
nodes.py           — all node functions + AgentState schema
detect.py          — time-based clustering + Ollama routine interpretation
poller.py          — Google Calendar, Notion, and Slack pollers
tools.py           — executor helper functions (calendar, notion, slack)
executors.py       — deprecated shim that re-exports from tools.py
slack_bot.py       — Slack Bolt app, setup loop, message routing, tool runner
calendar_auth.py   — Google OAuth flow + local callback server (port 8080)
storage.py         — JSON file read/write helpers
seed.py            — mock event data for demos (currently commented out)

slack_manifest.json     — import this to configure the Slack app in one step
users.json              — registered users (auto-populated by setup_loop)
event_log.json          — append-only log of polled events
tools_store.json        — saved tools per user (includes generated executor code)
state.json              — per-user last-polled timestamps
credentials.json        — Google OAuth client credentials (you provide this)
token_{user_id}.json    — per-user Google Calendar tokens (auto-generated)
```

`users.json`, `state.json`, `tools_store.json`, `event_log.json`, `credentials.json`, and `token_{user_id}.json` are all gitignored — they hold real workspace/user data generated at runtime, not source. A fresh clone starts with none of them; they're created automatically as people set up and use the bot.

---

## Data Flow

```
Google Calendar ──┐
Notion           ──┼──► poller ──► event_log.json ──► cluster ──► Ollama (llm_node)
Slack history    ──┘                                                      │
                                                                          ▼
                                                                   pattern detection
                                                                    (args schema)
                                                                          │
                                                                 Ollama (propose_node)
                                                                          │
                                                                 Slack DM to user
                                                                          │
                                                      ┌───────────────────┤
                                                   "yes"            change request
                                                      │                   │
                                                      │          Ollama (re_propose)
                                                      │                   │
                                                      │            re-DM user (loop)
                                                      │
                                             Ollama (executor_node)
                                              generates execute(args)
                                                      │
                                                tools_store.json
                                                      │
                                          user: /tool name key=value
                                                      │
                                            live Slack checklist
```
