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
| `llm` | Calls OpenRouter on each cluster to decide if it's a routine |
| `pattern` | Finds sequences that repeat 2+ times and aren't already saved |
| `propose` | Generates a full tool definition via OpenRouter, DMs the user a proposal card |
| `human` | LangGraph interrupt — pauses the graph until the user replies in Slack |
| `confirm` | Routes to `executor` (yes), `re_propose` (change request), or `END` (no) |
| `re_propose` | Applies a natural-language change request via OpenRouter and re-sends the proposal |
| `executor` | Calls OpenRouter to generate a custom `execute(args: dict)` function for the tool |
| `execute` | Runs a saved tool and posts a live-updating Slack checklist |

### Background Threads

| Thread | Interval | What it does |
|---|---|---|
| `agent` | every 15s | Runs the full LangGraph pipeline for all registered users |
| `setup-checker` | every 1h | Scans the workspace, auto-registers new members, DMs anyone missing calendar auth |
| `proposal-expiry` | every 10s | Discards proposals that have been pending > 2 minutes |
| Slack bot | — | Socket Mode listener (blocking, keeps the process alive) |

---

## Prerequisites

- Python 3.11+
- An [OpenRouter](https://openrouter.ai) API key — used for pattern detection, proposal generation, and executor code generation
- A Slack workspace where you can create apps
- A Notion integration and database (optional)
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

### 2. Get an OpenRouter API key

Sign up at [openrouter.ai](https://openrouter.ai), create an API key, and add it to `.env` in step 4.

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
- `channels:manage`
- `channels:read`
- `channels:write.invites`
- `chat:write`
- `commands`
- `groups:history`
- `groups:read`
- `groups:write`
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

# OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=openai/gpt-4o-mini

# Notion (optional)
NOTION_TOKEN=secret_...
NOTION_DB_ID=...
```

### 5. Notion (optional)

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) → **New integration** → copy the **Internal Integration Token** → this is your `NOTION_TOKEN`
2. Copy the database ID from the database URL:
   `https://notion.so/workspace/`**`<database-id>`**`?v=...`
   → this is your `NOTION_DB_ID`
3. **Connect the integration to your database** — this step is required even if your token and ID are correct:
   - Open the database in Notion
   - Click `...` (top-right) → **Connections** → find your integration → click **Connect**

### 6. Google Calendar (optional)

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project → enable the **Google Calendar API**
2. **APIs & Services → Credentials** → create an **OAuth 2.0 Client ID** (Desktop app) → download as `credentials.json` into the project root

The bot handles the rest automatically. When it starts, it scans the workspace and DMs any user who hasn't connected their calendar yet with an OAuth link. The user clicks it, approves in the browser, and a token is saved locally. A callback server runs on `http://localhost:8080` to receive the OAuth redirect.

> **Note:** The OAuth token is created with full `calendar` read+write scope so Talos can both read events and create calendar invites.

To manually trigger the OAuth flow for a user:

```bash
python test_calendar_auth.py u11
```

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

### Manual triggers

Send these as a message to Talos (DM or any channel it's been invited to):

| Command | What it does |
|---|---|
| `!poll now` | Immediately poll and run detection, then DM you if a pattern is found |

### Seeding mock events (for demos)

`seed.py` contains mock events for 10 users with a variety of workflow patterns. Run it to pre-populate `event_log.json` so the agent detects patterns immediately without waiting for real activity:

```bash
python seed.py
```

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

Open Talos's App Home tab in Slack to see your saved tools. If you haven't connected Google Calendar yet, Home shows a **Connect Google Calendar** button instead.

### When a pattern is detected

Talos DMs you a proposal card with the sequence, proposed tool name, description, steps, and arguments, followed by three buttons:

- **✅ Save** — saves the tool immediately
- **✏️ Change** — describe what you'd like different (e.g. _call it prep\_meeting_ or _add a topic argument_); Talos applies it via OpenRouter and re-sends an updated proposal. This loops until you Save or Discard.
- **❌ Discard** — drops the proposal

You can also reply in plain English instead of clicking Change — both work. Proposals expire after **2 minutes** of no reply.

### Running a saved tool

```
/tool weekly_sync person=Alice
```

Or message the bot directly:

```
weekly_sync person=Alice
```

Positional args work too:

```
weekly_sync Alice
```

The bot posts a live Slack checklist that ticks off each step as it completes.

---

## Executor Sandbox

When a tool is confirmed, `executor_node` calls OpenRouter to generate a custom `execute(args: dict)` function tailored to the tool's steps. The generated code runs in a restricted sandbox with only these helper functions available:

| Function | What it does |
|---|---|
| `calendar_read(person)` | Returns the next upcoming calendar event for a person |
| `calendar_write(person, topic, start_datetime, end_datetime, email)` | Creates a calendar event and sends an invite (`start_datetime` / `end_datetime` are ISO 8601 strings) |
| `notion_read(query)` | Returns open Notion tasks mentioning a person or keyword |
| `notion_write(title, notes)` | Creates a new Notion page |
| `slack_read(channel, limit)` | Returns recent messages from a Slack channel |
| `slack_write(text, channel)` | Posts a message to a Slack channel |
| `slack_create_channel(name)` | Creates a new public Slack channel |
| `slack_invite(channel_id, user_ids)` | Invites a list of Slack user IDs to a channel |

---

## Testing

`test_tools.py` runs each tool against real credentials to verify everything is wired up correctly:

```bash
python test_tools.py                   # all tests, user u11
python test_tools.py --user u1         # different user
python test_tools.py --only calendar   # one section only
python test_tools.py --only notion
python test_tools.py --only slack
```

`test_calendar_auth.py` tests the Google OAuth flow in isolation (opens a browser):

```bash
python test_calendar_auth.py u11
```

`test_checklist.py` posts a live tool-execution checklist to Slack using a fake tool:

```bash
python test_checklist.py
```

---

## Project Structure

```
main.py            — entry point, starts all threads
agent_graph.py     — LangGraph graph wiring and public API
nodes.py           — all node functions + AgentState schema
detect.py          — time-based clustering + OpenRouter routine interpretation
poller.py          — Google Calendar, Notion, and Slack pollers
tools.py           — executor helper functions (calendar, notion, slack)
executors.py       — deprecated shim that re-exports from tools.py
slack_bot.py       — Slack Bolt app, setup loop, message routing, tool runner
calendar_auth.py   — Google OAuth flow + local callback server (port 8080)
storage.py         — JSON file read/write helpers
seed.py            — mock event data for demos

slack_manifest.json     — import this to configure the Slack app in one step
event_log.json          — append-only log of polled events
tools_store.json        — saved tools per user (includes generated executor code)
state.json              — per-user last-polled timestamps
credentials.json        — Google OAuth client credentials (you provide this)
token_{user_id}.json    — per-user Google Calendar tokens (auto-generated)

test_tools.py           — integration tests for all tools (uses real credentials)
test_calendar_auth.py   — manual OAuth flow test
test_checklist.py       — manual Slack checklist test
```

`event_log.json`, `tools_store.json`, `state.json`, `credentials.json`, and `token_{user_id}.json` are gitignored — they hold runtime data generated as people set up and use the bot.

---

## Data Flow

```
Google Calendar ──┐
Notion           ──┼──► poller ──► event_log.json ──► cluster ──► OpenRouter (llm_node)
Slack history    ──┘                                                      │
                                                                          ▼
                                                                   pattern detection
                                                                          │
                                                                 OpenRouter (propose_node)
                                                                          │
                                                                 Slack DM to user
                                                                          │
                                                      ┌───────────────────┤
                                                   "yes"            change request
                                                      │                   │
                                                      │          OpenRouter (re_propose)
                                                      │                   │
                                                      │            re-DM user (loop)
                                                      │
                                              OpenRouter (executor_node)
                                              generates execute(args)
                                                      │
                                                tools_store.json
                                                      │
                                          user: /tool name key=value
                                                      │
                                            live Slack checklist
```
