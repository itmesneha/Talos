# Person A — Data Pipeline + Detection

## Your Ownership

```
storage.py        shared helpers (you build, B imports)
users.json        data contract
state.json        data contract
event_log.json    data contract (pre-seeded for demo)
tools_store.json  data contract
mock_events/      calendar mock data
poller.py         polls Calendar/Notion/Slack → event_log
detect.py         clusters events → Claude → pattern → propose
```

## What B is building (don't touch)
```
executors.py      calendar/notion/slack action functions
slack_bot.py      slack bot + live checklist
main.py           glue (you give B the functions, B wires them)
```

---

## Interface Contract with B

**You deliver these exact function signatures. B imports them.**

```python
# from poller.py
def poll_now(user_id: str = None) -> None: ...
def polling_loop(interval_seconds: int = 15) -> None: ...

# from detect.py
def detect_loop(interval_seconds: int = 15) -> None: ...

# from storage.py (B also imports these)
def get_tools(user_id: str) -> list[dict]: ...
def append_tool(user_id: str, tool: dict) -> None: ...
def find_matching_tool(user_id: str, message: str) -> list[dict]: ...
def tool_already_saved(user_id: str, sequence: list) -> bool: ...
def slack_id_to_user_id(slack_id: str) -> str | None: ...
```

**Sync point**: Share `storage.py` and all JSON files with B before
they start on `slack_bot.py`. This is the only hard dependency.

---

## Step A1 — Data Contracts + storage.py (do this first, ~45 min)

### Create all JSON files

**users.json**
```json
{
  "u1": {"slack_id": "U0ABC123", "notion_filter": "u1", "label": "Alice"},
  "u2": {"slack_id": "U0DEF456", "notion_filter": "u2", "label": "Bob"}
}
```
Replace slack_id values with real Slack user IDs from your workspace.

**state.json**
```json
{
  "u1": {
    "calendar_last_polled": "2024-01-01T00:00:00Z",
    "notion_last_polled":   "2024-01-01T00:00:00Z",
    "slack_last_polled":    "0"
  },
  "u2": {
    "calendar_last_polled": "2024-01-01T00:00:00Z",
    "notion_last_polled":   "2024-01-01T00:00:00Z",
    "slack_last_polled":    "0"
  }
}
```

**event_log.json** — pre-seeded with 2 clusters per user (see Step A5)
```json
[]
```
Start empty, seed in Step A5.

**tools_store.json**
```json
{"u1": [], "u2": []}
```

### Create storage.py

```python
import json
import os
from pathlib import Path

USERS_FILE       = "users.json"
STATE_FILE       = "state.json"
EVENT_LOG_FILE   = "event_log.json"
TOOLS_STORE_FILE = "tools_store.json"

def read_json(path: str) -> any:
    with open(path) as f:
        return json.load(f)

def write_json(path: str, data: any) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

def get_users() -> dict:
    return read_json(USERS_FILE)

def get_state() -> dict:
    return read_json(STATE_FILE)

def update_state(state: dict) -> None:
    write_json(STATE_FILE, state)

def append_event(event: dict) -> None:
    log = read_json(EVENT_LOG_FILE)
    log.append(event)
    write_json(EVENT_LOG_FILE, log)

def get_events(user_id: str = None) -> list[dict]:
    log = read_json(EVENT_LOG_FILE)
    if user_id:
        return [e for e in log if e["user_id"] == user_id]
    return log

def get_tools(user_id: str) -> list[dict]:
    store = read_json(TOOLS_STORE_FILE)
    return store.get(user_id, [])

def append_tool(user_id: str, tool: dict) -> None:
    store = read_json(TOOLS_STORE_FILE)
    if user_id not in store:
        store[user_id] = []
    store[user_id].append(tool)
    write_json(TOOLS_STORE_FILE, store)

def find_matching_tool(user_id: str, message: str) -> list[dict]:
    tools = get_tools(user_id)
    message_lower = message.lower()
    return [
        t for t in tools
        if t["tool_name"].lower() in message_lower
        or any(w in message_lower for w in t["description"].lower().split()[:5])
    ]

def tool_already_saved(user_id: str, sequence: list) -> bool:
    return any(t["sequence"] == sequence for t in get_tools(user_id))

def slack_id_to_user_id(slack_id: str) -> str | None:
    users = get_users()
    for uid, cfg in users.items():
        if cfg["slack_id"] == slack_id:
            return uid
    return None
```

**Test**: `python -c "from storage import get_users; print(get_users())"` — should print users.json contents.

---

## Step A2 — poller.py (~60 min)

```python
import time
import requests
from datetime import datetime, timezone
from storage import (
    get_users, get_state, update_state,
    append_event, read_json
)
import os
from dotenv import load_dotenv
load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")


def poll_calendar(user_id: str, config: dict, last_polled: str) -> list[dict]:
    """
    MVP: read from mock_events/calendar_{user_id}.json
    TODO: replace with real Google Calendar API call using last_polled as updatedMin
    """
    mock_path = f"mock_events/calendar_{user_id}.json"
    try:
        raw = read_json(mock_path)
        return [
            {
                "user_id": user_id,
                "source": "calendar",
                "time": item["start"]["dateTime"],
                "text": item["summary"],
            }
            for item in raw
            if item["start"]["dateTime"] > last_polled
        ]
    except FileNotFoundError:
        return []


def poll_notion(user_id: str, config: dict, last_polled: str) -> list[dict]:
    """Real Notion API call."""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    body = {
        "filter": {
            "property": "Assignee",
            "rich_text": {"contains": config["notion_filter"]}
        },
        "sorts": [{"timestamp": "last_edited_time", "direction": "descending"}]
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
        headers=headers,
        json=body,
        timeout=10,
    )
    if resp.status_code != 200:
        return []

    events = []
    for page in resp.json().get("results", []):
        edited = page.get("last_edited_time", "")
        if edited <= last_polled:
            continue
        title = ""
        for prop in page["properties"].values():
            if prop["type"] == "title" and prop["title"]:
                title = prop["title"][0]["plain_text"]
                break
        events.append({
            "user_id": user_id,
            "source": "notion",
            "time": edited,
            "text": title,
        })
    return events


def poll_slack(user_id: str, config: dict, last_polled: str) -> list[dict]:
    """Real Slack conversations.history call."""
    from slack_sdk import WebClient
    client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
    channel = os.getenv("SLACK_CHANNEL")
    resp = client.conversations_history(channel=channel, oldest=last_polled, limit=50)
    events = []
    for msg in resp["messages"]:
        if msg.get("user") == config["slack_id"]:
            events.append({
                "user_id": user_id,
                "source": "slack",
                "time": str(msg["ts"]),
                "text": msg.get("text", ""),
            })
    return events


def poll_user(user_id: str) -> None:
    users = get_users()
    state = get_state()
    config = users[user_id]
    user_state = state.get(user_id, {})

    sources = {
        "calendar": (poll_calendar, "calendar_last_polled"),
        "notion":   (poll_notion,   "notion_last_polled"),
        "slack":    (poll_slack,    "slack_last_polled"),
    }

    for source, (fn, state_key) in sources.items():
        last = user_state.get(state_key, "0")
        try:
            new_events = fn(user_id, config, last)
            for event in new_events:
                append_event(event)
            if new_events:
                latest = max(e["time"] for e in new_events)
                state.setdefault(user_id, {})[state_key] = latest
        except Exception as e:
            print(f"[poller] {user_id}/{source} error: {e}")

    update_state(state)
    print(f"[poller] polled {user_id}")


def poll_now(user_id: str = None) -> None:
    """Trigger an immediate poll. Call this during the demo."""
    users = get_users()
    targets = [user_id] if user_id else list(users.keys())
    for uid in targets:
        poll_user(uid)


def polling_loop(interval_seconds: int = 15) -> None:
    while True:
        poll_now()
        time.sleep(interval_seconds)


if __name__ == "__main__":
    poll_now()
    print("Done. Check event_log.json")
```

**Test**: `python poller.py` — verify `event_log.json` gets new entries.

---

## Step A3 — mock_events/ (~20 min)

Create `mock_events/` directory with two files.

**mock_events/calendar_u1.json**
```json
[
  {"summary": "Weekly check-in with Alice", "start": {"dateTime": "2024-01-15T09:00:00Z"}},
  {"summary": "Weekly check-in with Bob",   "start": {"dateTime": "2024-01-22T09:00:00Z"}}
]
```

**mock_events/calendar_u2.json**
```json
[
  {"summary": "Expense review with Finance", "start": {"dateTime": "2024-01-15T14:00:00Z"}},
  {"summary": "Expense review with Ops",     "start": {"dateTime": "2024-01-22T14:00:00Z"}}
]
```

---

## Step A4 — detect.py (~90 min — the agentic core)

```python
import json
import sys
import time
import anthropic
from storage import get_users, get_events, tool_already_saved
from dotenv import load_dotenv
load_dotenv()

client = anthropic.Anthropic()


def cluster_by_time(events: list[dict], window_minutes: int = 10) -> list[list[dict]]:
    """
    Pure function — no API calls.
    Groups consecutive events within window_minutes of each other.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e["time"])
    clusters = []
    current = [sorted_events[0]]

    for event in sorted_events[1:]:
        from datetime import datetime
        prev_time = datetime.fromisoformat(current[-1]["time"].replace("Z", "+00:00"))
        curr_time = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
        gap_minutes = (curr_time - prev_time).total_seconds() / 60

        if gap_minutes <= window_minutes:
            current.append(event)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [event]

    if len(current) >= 2:
        clusters.append(current)

    return clusters


def interpret_cluster(cluster: list[dict]) -> dict | None:
    """
    Calls Claude. Returns structured JSON or None on failure.
    Retries once on malformed output.
    """
    events_text = "\n".join(
        f"- [{e['source']}] {e['time']}: {e['text']}"
        for e in cluster
    )

    prompt = f"""You are analysing a sequence of user actions across different tools.

Events (in time order):
{events_text}

Determine if these events represent one coherent multi-step routine a user repeats.

Reply with ONLY valid JSON, no explanation:
{{
  "is_routine": true or false,
  "sequence": ["source1", "source2", ...],
  "slot": "the thing that varies each time (e.g. person, topic, vendor)",
  "slot_value": "the specific value in THIS instance",
  "description": "one sentence describing the routine"
}}

If not a routine, reply: {{"is_routine": false}}"""

    for attempt in range(2):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            text = resp.content[0].text.strip()
            result = json.loads(text)
            if result.get("is_routine"):
                return result
            return None
        except (json.JSONDecodeError, KeyError):
            if attempt == 0:
                continue
            return None
    return None


def find_repeated_pattern(user_id: str) -> dict | None:
    """
    Scoped entirely to one user.
    Returns a pattern dict if 2+ clusters share the same sequence, else None.
    """
    events = get_events(user_id)
    if len(events) < 4:
        return None

    clusters = cluster_by_time(events)
    interpreted = []

    for cluster in clusters:
        result = interpret_cluster(cluster)
        if result:
            interpreted.append(result)

    # Find if 2+ clusters share the same sequence
    from collections import Counter
    sequence_counts = Counter(
        tuple(r["sequence"]) for r in interpreted
    )

    for sequence_tuple, count in sequence_counts.items():
        if count >= 2:
            # Return the most recent matching cluster's interpretation
            for r in reversed(interpreted):
                if tuple(r["sequence"]) == sequence_tuple:
                    return r

    return None


def detect_loop(interval_seconds: int = 15) -> None:
    # Import here to avoid circular import
    from slack_bot import propose_tool

    while True:
        users = get_users()
        for user_id in users:
            try:
                pattern = find_repeated_pattern(user_id)
                if pattern and not tool_already_saved(user_id, pattern["sequence"]):
                    propose_tool(user_id, pattern)
            except Exception as e:
                print(f"[detect] {user_id} error: {e}")
        time.sleep(interval_seconds)


if __name__ == "__main__":
    user_id = sys.argv[2] if len(sys.argv) > 2 else "u1"
    if "--user" in sys.argv:
        pattern = find_repeated_pattern(user_id)
        print(json.dumps(pattern, indent=2) if pattern else "No pattern found")
    else:
        print("Usage: python detect.py --user u1")
```

**Test**: `python detect.py --user u1` — should print detected pattern.

---

## Step A5 — Pre-seed event_log.json for demo (~20 min)

Run this once to seed 2 clusters per user:

```python
# seed.py — run once before demo
from storage import write_json

events = [
  # u1 cluster 1 — Alice
  {"user_id":"u1","source":"calendar","time":"2024-01-15T09:00:00Z","text":"Check-in with Alice"},
  {"user_id":"u1","source":"notion",  "time":"2024-01-15T09:04:00Z","text":"Prep notes - Alice"},
  {"user_id":"u1","source":"slack",   "time":"2024-01-15T09:07:00Z","text":"Messaged Alice about agenda"},

  # u1 cluster 2 — Bob (same sequence, different slot_value)
  {"user_id":"u1","source":"calendar","time":"2024-01-22T09:00:00Z","text":"Check-in with Bob"},
  {"user_id":"u1","source":"notion",  "time":"2024-01-22T09:03:00Z","text":"Prep notes - Bob"},
  {"user_id":"u1","source":"slack",   "time":"2024-01-22T09:06:00Z","text":"Messaged Bob about agenda"},

  # u2 cluster 1 — different sequence (slack → notion)
  {"user_id":"u2","source":"slack",  "time":"2024-01-15T14:00:00Z","text":"Chased Finance on expenses"},
  {"user_id":"u2","source":"notion", "time":"2024-01-15T14:08:00Z","text":"Logged expense - Finance"},

  # u2 cluster 2 — same sequence, different slot
  {"user_id":"u2","source":"slack",  "time":"2024-01-22T14:00:00Z","text":"Chased Ops on expenses"},
  {"user_id":"u2","source":"notion", "time":"2024-01-22T14:06:00Z","text":"Logged expense - Ops"},
]

write_json("event_log.json", events)
print("Seeded.")
```

---

## Your Done Criteria

- [ ] `python -c "from storage import get_users; print(get_users())"` works
- [ ] `python poller.py` adds events to event_log.json
- [ ] `python detect.py --user u1` prints a pattern dict (not None)
- [ ] `python detect.py --user u2` prints a DIFFERENT pattern dict
- [ ] Pattern for u1 does not appear when running `--user u2`
- [ ] `tool_already_saved("u1", ["calendar","notion","slack"])` returns False before confirm, True after
