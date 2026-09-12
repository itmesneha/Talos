# Tool-Synthesis Agent — Hackathon MVP Planner

## What We're Building

A multi-user Slack bot that watches Calendar, Notion, and Slack for activity,
detects when a user repeats the same multi-step sequence, proposes turning it
into a reusable tool, saves it, and can run it on demand — per user, isolated,
with a live-updating checklist message as the demo centrepiece.

**The agentic core**: no pattern is hardcoded. Claude infers routines from raw
timestamped events, figures out what's fixed vs. what varies, and does this
independently per user.

---

## Architecture

```
main.py
  ├── Thread 1: poller.py loop (every 15s)
  │     ├── Google Calendar → event_log.json  [stub OK]
  │     ├── Notion DB       → event_log.json  [real]
  │     └── Slack history   → event_log.json  [real]
  │
  ├── Thread 2: detect.py loop (every 15s)
  │     ├── cluster_by_time()     [pure Python, no LLM]
  │     ├── interpret_cluster()   [Claude call — must be real]
  │     ├── find_repeated_pattern()
  │     └── → triggers propose_tool() in slack_bot.py
  │
  └── Thread 3: slack_bolt Socket Mode
        ├── propose_tool(user_id, pattern)  → DM to correct user
        ├── on "yes"  → append to tools_store.json
        └── on other  → match tool → run executors → live checklist
```

---

## File Build Order (critical — each step unlocks the next)

```
Step 1  →  users.json + state.json + event_log.json      (data contracts)
Step 2  →  poller.py                                      (data source)
Step 3  →  detect.py                                      (the agentic brain)
Step 4  →  executors.py                                   (the hands)
Step 5  →  tools_store.json structure                     (memory)
Step 6  →  slack_bot.py                                   (the face)
Step 7  →  main.py                                        (glue)
Step 8  →  mock data + demo seeding                       (demo safety net)
```

---

## Step 1 — Data Contracts (JSON schemas)

**Files**: `users.json`, `state.json`, `event_log.json`, `tools_store.json`

Define the shape of every JSON file before writing any code.
Everything else reads/writes these — getting the schema right first
prevents rewrites later.

### users.json
```json
{
  "u1": {"slack_id": "U0ABC123", "notion_filter": "u1", "label": "Alice"},
  "u2": {"slack_id": "U0DEF456", "notion_filter": "u2", "label": "Bob"}
}
```

### state.json
```json
{
  "u1": {
    "calendar_last_polled": "2024-01-01T00:00:00Z",
    "notion_last_polled":   "2024-01-01T00:00:00Z",
    "slack_last_polled":    "0"
  },
  "u2": { ... }
}
```

### event_log.json
```json
[
  {"user_id": "u1", "source": "calendar", "time": "2024-01-15T09:00:00Z", "text": "Meeting with Alice"},
  {"user_id": "u1", "source": "notion",   "time": "2024-01-15T09:05:00Z", "text": "Checked prep notes for Alice"},
  {"user_id": "u1", "source": "slack",    "time": "2024-01-15T09:08:00Z", "text": "Messaged Alice about agenda"}
]
```

### tools_store.json
```json
{
  "u1": [
    {
      "tool_name": "weekly_checkin_prep",
      "description": "Check calendar + notion + send Slack message before a weekly check-in",
      "sequence": ["calendar", "notion", "slack"],
      "slot": "person",
      "confirmed": true
    }
  ],
  "u2": []
}
```

**Done when**: All 4 files exist with correct shape. Write a `storage.py`
helper with `read_json(path)` and `write_json(path, data)` — used everywhere.

---

## Step 2 — poller.py

**What it does**: Background loop that polls 3 sources per user, appends
normalized events to `event_log.json`, updates `state.json`.

### Functions to build

```python
poll_calendar(user_id, user_config, last_polled) -> list[Event]
  # MVP: read from mock_events/calendar_u1.json if OAuth not ready
  # Real: Google Calendar events.list with updatedMin=last_polled
  # Return: [{"user_id", "source": "calendar", "time", "text"}]

poll_notion(user_id, user_config, last_polled) -> list[Event]
  # Real: POST /v1/databases/{DB_ID}/query with filter on notion_filter
  # Filter results client-side for rows updated after last_polled
  # Return: [{"user_id", "source": "notion", "time", "text"}]

poll_slack(user_id, user_config, last_polled) -> list[Event]
  # Real: conversations.history with oldest=last_polled
  # Filter to messages authored by this user's slack_id
  # Return: [{"user_id", "source": "slack", "time", "text"}]

poll_user(user_id) -> None
  # Calls all three, appends results to event_log.json, updates state.json

poll_now(user_id=None) -> None
  # Polls one user (or all if None) immediately — DEMO SAFETY NET
  # Must always work regardless of interval timer

polling_loop(interval_seconds=15) -> None
  # while True: poll all users, sleep(interval_seconds)
```

### Mock event source (fallback)
```
mock_events/
  calendar_u1.json   # pre-seeded, shaped like Calendar API response
  calendar_u2.json
```

Shape matches real API so swapping to real OAuth requires only
changing the data-fetch line, not the normalization logic.

**Done when**: `python poller.py` appends events to `event_log.json`
and updates `state.json` without error. Verify manually by checking
both files after one poll cycle.

---

## Step 3 — detect.py (the agentic brain — MUST be real)

Two-stage detection. This is the thing that makes the project agentic —
do not stub or simplify the LLM call here.

### Stage 1: cluster_by_time() — pure Python, no LLM

```python
def cluster_by_time(events: list[Event], window_minutes: int = 10) -> list[list[Event]]:
```

- Sort events by time
- Group consecutive events where gap < window_minutes
- Each group = one candidate cluster
- Pure function — testable with no API calls:
  ```
  python -c "from detect import cluster_by_time; print(cluster_by_time([...]))"
  ```

### Stage 2: interpret_cluster() — Claude call

```python
def interpret_cluster(cluster: list[Event]) -> dict | None:
```

Prompt Claude with the raw cluster events. Require strict JSON output:
```json
{
  "is_routine": true,
  "sequence": ["calendar", "notion", "slack"],
  "slot": "person",
  "slot_value": "Alice",
  "description": "Pre-meeting check: look up calendar, check prep notes, message the person"
}
```

- If `is_routine` is false → return None
- If JSON is malformed → retry once, then return None
- If second attempt also fails → log and skip (don't crash)

### Stage 3: find_repeated_pattern() — orchestrator

```python
def find_repeated_pattern(user_id: str) -> dict | None:
```

- Load that user's events from `event_log.json` (filter by user_id)
- Run `cluster_by_time()`
- Run `interpret_cluster()` on each cluster that has ≥ 2 events
- Compare interpreted clusters: if 2+ share the same `sequence` list → repeated pattern found
- Return the pattern dict or None
- **Scoped to one user — never reads another user's events**

### Stage 4: detect_loop()

```python
def detect_loop(interval_seconds=15) -> None:
```

- For each user in `users.json`:
  - Call `find_repeated_pattern(user_id)`
  - If pattern found AND not already in their `tools_store.json` → call `propose_tool()`
- Sleep, repeat

### Standalone test (required)

```bash
python detect.py --user u1
# Should print the detected pattern dict or "No pattern found"
# Must work with no other component running
```

**Done when**: Running with seeded event data detects the pattern and
prints the correct sequence + slot. The Claude call must be real and
returning structured JSON.

---

## Step 4 — executors.py

Three real functions + one composer. Each independently testable from REPL.

```python
def check_calendar(person: str) -> str:
  # MVP stub — returns plausible fake string
  # f"Next meeting with {person}: Thu 4pm"
  # TODO: replace with real Google Calendar API call
  # Must still be called by the bot — stub is fine, skipping is not

def check_notion(person: str) -> str:
  # REAL — must work
  # POST /v1/databases/{DB_ID}/query
  # Filter client-side for rows mentioning person
  # Return first match summary or "No open items for {person}"

def post_slack(text: str, channel: str = None) -> str:
  # REAL — must work
  # chat.postMessage to SLACK_CHANNEL (or override channel)
  # Return "Message sent" or error string

def draft_message(person: str, calendar_result: str, notion_result: str) -> str:
  # MVP: plain f-string is fine
  # f"Hi {person}, checking in — {calendar_result}. Re: tasks: {notion_result}"
  # Optional: LLM call for polish if time allows
```

**Test each from REPL before wiring into bot**:
```python
from executors import check_notion
print(check_notion("Alice"))
```

**Done when**: All 4 functions return strings without crashing.
`check_notion` and `post_slack` must make real API calls.

---

## Step 5 — tools_store.json operations

Add these helpers to `storage.py`:

```python
def get_tools(user_id: str) -> list[dict]:
  # Return user's tool list, [] if none

def append_tool(user_id: str, tool: dict) -> None:
  # Append to user's list — never overwrite
  # Auto-generate tool_name from description if not set:
  # slugify first 3 words of description

def find_matching_tool(user_id: str, message: str) -> list[dict]:
  # Substring match: does message contain tool_name or key word from description?
  # Return ALL matches (may be multiple — caller decides what to do)

def tool_already_saved(user_id: str, sequence: list) -> bool:
  # Check if a tool with this exact sequence already exists for this user
  # Used by detect_loop to avoid re-proposing confirmed patterns
```

**Done when**: Unit-testable from Python with no Slack/Claude running.

---

## Step 6 — slack_bot.py (the demo centrepiece)

Using `slack_bolt` in Socket Mode.

### Pending proposals (in-memory dict — no file needed)
```python
pending_proposals: dict[str, dict] = {}
# { slack_user_id: pattern_dict }
```

### propose_tool(user_id, pattern)
```python
def propose_tool(user_id: str, pattern: dict) -> None:
```
- Look up slack_id from users.json
- DM that user:
  ```
  I noticed you've done this 2+ times:
  1. Check calendar
  2. Check Notion
  3. Send Slack message
  Each time for a different person.

  Want me to save this as a tool called "weekly_checkin_prep"?
  Reply yes to confirm.
  ```
- Store in `pending_proposals[slack_id] = pattern`

### Message handler — route by sender

```python
@app.message("")
def handle_message(message, say, client):
    slack_user_id = message["user"]

    # 1. Pending proposal?
    if slack_user_id in pending_proposals:
        if "yes" in message["text"].lower():
            confirm_tool(slack_user_id, say)
        else:
            say("Reply yes to confirm, or ignore to skip.")
        return

    # 2. Match against this user's saved tools
    user_id = slack_id_to_user_id(slack_user_id)
    matches = find_matching_tool(user_id, message["text"])

    if len(matches) == 0:
        say("No matching tool found.")
    elif len(matches) > 1:
        names = [t["tool_name"] for t in matches]
        say(f"Multiple tools match: {names}. Which one did you mean?")
    else:
        slot_value = extract_slot(message["text"], matches[0]["slot"])
        run_tool(matches[0], slot_value, slack_user_id, say, client)
```

### run_tool() — the live-updating checklist (highest priority)

```python
def run_tool(tool: dict, slot_value: str, slack_user_id: str, say, client) -> None:
```

Step 1 — Post initial message with unchecked boxes:
```
Running weekly_checkin_prep for Alice...
☐ calendar
☐ notion
☐ slack
```

Step 2 — For each step in `tool["sequence"]`:
- Call the corresponding executor
- Edit the original message in place with `chat.update`:
  ```
  Running weekly_checkin_prep for Alice...
  ✅ calendar — Next meeting Thu 4pm
  ☐ notion
  ☐ slack
  ```

Step 3 — After all steps, final update:
```
✅ weekly_checkin_prep complete for Alice
✅ calendar — Next meeting Thu 4pm
✅ notion — No open items for Alice
✅ slack — Message sent
```

**This is the single most important visual. Build and test this before
anything else in the bot.**

### Key isolation requirement
Every message handler reads `message["user"]` first and uses it to
route to the correct user's tools and proposals. No global state shared
between users.

**Done when**:
- propose_tool DMs the right user
- "yes" appends to tools_store.json
- A matching message triggers the live-updating checklist
- Two users can interact with the bot simultaneously without crossing state

---

## Step 7 — main.py

```python
import threading
from poller import polling_loop, poll_now
from detect import detect_loop
from slack_bot import app, start_bot

def main():
    threads = [
        threading.Thread(target=polling_loop,  kwargs={"interval_seconds": 15}, daemon=True),
        threading.Thread(target=detect_loop,   kwargs={"interval_seconds": 15}, daemon=True),
        threading.Thread(target=start_bot,     daemon=False),
    ]
    for t in threads:
        t.start()
    threads[-1].join()  # keep alive via bot thread

if __name__ == "__main__":
    main()
```

### Manual trigger (required for demo)
Handle a special Slack message `!poll now`:
```python
if message["text"] == "!poll now":
    user_id = slack_id_to_user_id(message["user"])
    poll_now(user_id)
    detect_pass(user_id)   # single detection pass immediately
    say("Polled and ran detection.")
```

**Done when**: `python main.py` starts all three threads, bot responds
in Slack, polling appends to event_log.json, detection fires.

---

## Step 8 — Mock data + demo seeding

### Pre-seed event_log.json for the demo

Seed 2 clusters per user, each with the same sequence shape,
so detection fires immediately on first run:

```json
[
  {"user_id":"u1","source":"calendar","time":"2024-01-15T09:00:00Z","text":"Check-in with Alice"},
  {"user_id":"u1","source":"notion",  "time":"2024-01-15T09:04:00Z","text":"Prep notes - Alice"},
  {"user_id":"u1","source":"slack",   "time":"2024-01-15T09:07:00Z","text":"Messaged Alice"},

  {"user_id":"u1","source":"calendar","time":"2024-01-22T09:00:00Z","text":"Check-in with Bob"},
  {"user_id":"u1","source":"notion",  "time":"2024-01-22T09:03:00Z","text":"Prep notes - Bob"},
  {"user_id":"u1","source":"slack",   "time":"2024-01-22T09:06:00Z","text":"Messaged Bob"},

  ... repeat for u2 with a different sequence shape ...
]
```

### mock_events/ (calendar fallback)
```
mock_events/
  calendar_u1.json   # 3-4 events shaped like Calendar API response
  calendar_u2.json
```

---

## Acceptable Shortcuts (time pressure)

| Component | Can shortcut | Cannot shortcut |
|-----------|-------------|-----------------|
| check_calendar | Stub with fake string | Must be called by bot |
| draft_message | f-string template | — |
| Tool-name matching | Substring match | — |
| Error handling | One retry only | — |
| interpret_cluster | — | Must be real Claude call |
| check_notion | — | Must be real Notion API |
| post_slack | — | Must be real Slack API |
| Live checklist update | — | Must use chat.update |
| poll_now() | — | Must always work |
| User isolation | — | Must never cross state |

---

## Out of Scope

- More than 2 demo users
- Any UI beyond Slack messages
- Auth/security hardening
- Fancy NLP for tool matching (substring is fine)

---

## Acceptance Checklist

- [ ] `python main.py` starts all 3 threads cleanly
- [ ] `!poll now` in Slack triggers immediate poll + detection pass
- [ ] Correct user gets DM proposal, other user sees nothing
- [ ] `yes` appends tool to tools_store.json (not overwrites)
- [ ] Second detected pattern for same user → second tool, both invocable
- [ ] New slot value triggers checklist that visibly updates step-by-step
- [ ] Real Notion row created + real Slack message sent at end
- [ ] Running both users concurrently does not cross state

---

## Demo Script (live walkthrough order)

```
1. Show empty tools_store.json — no tools yet

2. Show pre-seeded event_log.json — two clusters for Alice, two for Bob

3. Type !poll now in Slack
   → Agent detects pattern for u1
   → DMs Alice: "I noticed you've done this 2x — want to save it?"

4. Alice replies "yes"
   → tools_store.json updated, bot confirms + shows example invocation

5. Alice types "run it for Carol"
   → Live checklist appears
   → ✅ calendar  ✅ notion  ✅ slack
   → Final result shown

6. Repeat steps 3-5 for Bob with a DIFFERENT sequence
   → Bob's tool added separately, Alice's untouched

7. Alice types "run it for Dave" (new person never seen)
   → Same live checklist, new slot value, works identically
```

---

## File Delivery Order

Build and test in this exact order — each file is a dependency for the next:

```
1. storage.py          (helpers used by everything)
2. users.json          (data)
3. state.json          (data)
4. event_log.json      (data — pre-seeded)
5. tools_store.json    (data — empty)
6. mock_events/        (calendar fallback)
7. poller.py           (verify event_log grows)
8. detect.py           (verify pattern detection standalone)
9. executors.py        (verify each from REPL)
10. slack_bot.py       (verify checklist update first)
11. main.py            (wire it all together)
12. requirements.txt
13. .env.example
```
