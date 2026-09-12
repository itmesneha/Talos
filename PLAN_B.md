# Person B — Executors + Slack Bot + Glue

## Your Ownership

```
executors.py      calendar/notion/slack action functions
slack_bot.py      bot logic, proposals, live checklist
main.py           wires all three threads together
requirements.txt
.env.example
```

## What A is building (don't touch)
```
storage.py        shared helpers — import from here, don't rewrite
poller.py         polling loop + poll_now()
detect.py         pattern detection + detect_loop()
users.json / state.json / event_log.json / tools_store.json
```

---

## Interface Contract with A

**You import these from A's files. Stub them locally until A delivers.**

```python
# from storage.py (A delivers first — highest priority)
from storage import (
    get_tools,
    append_tool,
    find_matching_tool,
    tool_already_saved,
    slack_id_to_user_id,
    get_users,
)

# from poller.py
from poller import poll_now, polling_loop

# from detect.py
from detect import detect_loop
```

### Stub these until A delivers (drop in executors.py temporarily)

```python
# TEMP STUBS — delete when A delivers storage.py
def get_tools(user_id): return []
def append_tool(user_id, tool): pass
def find_matching_tool(user_id, msg): return []
def slack_id_to_user_id(slack_id): return None
def get_users(): return {}
```

Replace all stubs the moment A shares `storage.py`.

**Sync point**: Ask A for `storage.py` + JSON files before starting
`slack_bot.py`. You can build `executors.py` without it.

---

## Step B1 — executors.py (~45 min)

Build and test each function from the REPL before wiring anything together.

```python
import os
import requests
import anthropic
from slack_sdk import WebClient
from dotenv import load_dotenv
load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL = os.getenv("SLACK_CHANNEL")

_slack_client = WebClient(token=SLACK_BOT_TOKEN)
_anthropic_client = anthropic.Anthropic()


def check_calendar(person: str) -> str:
    # TODO: replace with real Google Calendar API call
    return f"Next meeting with {person}: Thu 4pm (stub)"


def check_notion(person: str) -> str:
    """Real Notion query — filter rows mentioning person."""
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DB_ID}/query",
        headers=headers,
        json={},
        timeout=10,
    )
    if resp.status_code != 200:
        return f"Notion error: {resp.status_code}"

    pages = resp.json().get("results", [])
    for page in pages:
        for prop in page["properties"].values():
            if prop["type"] == "title":
                title = prop["title"][0]["plain_text"] if prop["title"] else ""
                if person.lower() in title.lower():
                    return f"Open item for {person}: {title}"

    return f"No open items for {person}"


def post_slack(text: str, channel: str = None) -> str:
    """Real Slack message post."""
    target = channel or SLACK_CHANNEL
    resp = _slack_client.chat_postMessage(channel=target, text=text)
    if resp["ok"]:
        return "Message sent"
    return f"Slack error: {resp.get('error')}"


def draft_message(person: str, calendar_result: str, notion_result: str) -> str:
    # MVP: f-string template — fast and reliable for demo
    return (
        f"Hi {person}, just checking in ahead of our meeting. "
        f"{calendar_result}. "
        f"On the task front: {notion_result}. Let me know if anything needs attention!"
    )


# Map source name → executor function
EXECUTOR_MAP = {
    "calendar": lambda slot: check_calendar(slot),
    "notion":   lambda slot: check_notion(slot),
    "slack":    lambda slot: post_slack(draft_message(slot, check_calendar(slot), check_notion(slot))),
}
```

### Test each from REPL before continuing

```python
from executors import check_notion, post_slack, check_calendar

print(check_calendar("Alice"))          # stub — always works
print(check_notion("Alice"))            # real Notion call
print(post_slack("Test from executor")) # real Slack post — check channel
```

**Do not proceed to slack_bot.py until all three print without error.**

---

## Step B2 — slack_bot.py (build the checklist FIRST)

**Build order inside this file:**
1. Live-updating checklist → `run_tool()`
2. Proposal flow → `propose_tool()` + confirm
3. Message routing → `handle_message()`

This order matters. The checklist is the demo centrepiece. Get it
working on its own before wiring up the rest.

```python
import os
import time
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv
load_dotenv()

from executors import EXECUTOR_MAP, draft_message, check_calendar, check_notion
from storage import (
    get_users, get_tools, append_tool,
    find_matching_tool, slack_id_to_user_id, tool_already_saved,
)

app = App(token=os.getenv("SLACK_BOT_TOKEN"))

# In-memory pending proposals — { slack_user_id: pattern_dict }
pending_proposals: dict[str, dict] = {}


# ── PART 1: Live-updating checklist ──────────────────────────────────────────

def run_tool(tool: dict, slot_value: str, channel: str, client) -> None:
    """
    Posts a checklist message and updates it step-by-step as each executor runs.
    This is the most important function — build and test this first.
    """
    steps = tool["sequence"]

    def render_checklist(results: dict[str, str | None]) -> str:
        lines = [f"Running *{tool['tool_name']}* for *{slot_value}*..."]
        for step in steps:
            if step in results:
                result = results[step]
                lines.append(f"✅ {step} — {result}")
            else:
                lines.append(f"☐ {step}")
        return "\n".join(lines)

    # Post initial message — all boxes unchecked
    resp = client.chat_postMessage(
        channel=channel,
        text=render_checklist({})
    )
    msg_ts = resp["ts"]

    results = {}
    for step in steps:
        executor = EXECUTOR_MAP.get(step)
        if executor:
            try:
                result = executor(slot_value)
            except Exception as e:
                result = f"error: {e}"
        else:
            result = f"no executor for {step}"

        results[step] = result

        # Update the message in place — this is what the demo shows
        client.chat_update(
            channel=channel,
            ts=msg_ts,
            text=render_checklist(results)
        )
        time.sleep(0.5)  # small delay so the audience sees each step tick off

    # Final update — all checked
    client.chat_update(
        channel=channel,
        ts=msg_ts,
        text=render_checklist(results) + f"\n\n✅ *{tool['tool_name']}* complete."
    )


# ── PART 2: Proposal + confirmation ──────────────────────────────────────────

def propose_tool(user_id: str, pattern: dict) -> None:
    """Called by detect.py when a repeated pattern is found for a user."""
    users = get_users()
    slack_id = users[user_id]["slack_id"]

    # Auto-generate tool name from description
    words = pattern.get("description", "routine").lower().split()[:3]
    tool_name = "_".join(w.strip(".,") for w in words)
    pattern["tool_name"] = tool_name

    sequence_str = " → ".join(pattern["sequence"])
    msg = (
        f"I noticed you've done this {len(pattern['sequence'])}-step sequence at least twice:\n"
        f"*{sequence_str}*\n"
        f"Each time for a different *{pattern['slot']}*.\n\n"
        f"Want me to save this as a reusable tool called `{tool_name}`?\n"
        f"Reply *yes* to confirm."
    )

    app.client.chat_postMessage(channel=slack_id, text=msg)
    pending_proposals[slack_id] = pattern


def confirm_tool(slack_id: str, user_id: str, say) -> None:
    pattern = pending_proposals.pop(slack_id)
    pattern["confirmed"] = True
    append_tool(user_id, pattern)
    say(
        f"✅ Saved `{pattern['tool_name']}`.\n"
        f"Run it anytime by typing: `{pattern['tool_name']} for [name]`"
    )


# ── PART 3: Message routing ───────────────────────────────────────────────────

def extract_slot(text: str, slot: str) -> str:
    """
    Simple extraction — looks for 'for <name>' or 'with <name>' pattern.
    Falls back to last word if nothing found.
    """
    import re
    match = re.search(r"\b(?:for|with)\s+(\w+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return text.split()[-1]


@app.message("")
def handle_message(message, say, client):
    slack_user_id = message.get("user")
    text = message.get("text", "").strip()
    channel = message["channel"]

    if not slack_user_id or message.get("bot_id"):
        return  # ignore bot messages

    # Manual demo trigger
    if text == "!poll now":
        from poller import poll_now
        from detect import find_repeated_pattern
        user_id = slack_id_to_user_id(slack_user_id)
        poll_now(user_id)
        if user_id:
            pattern = find_repeated_pattern(user_id)
            if pattern and not tool_already_saved(user_id, pattern["sequence"]):
                propose_tool(user_id, pattern)
                say("Polled and found a pattern — check your DMs.")
            else:
                say("Polled. No new pattern found yet.")
        return

    # Pending proposal?
    if slack_user_id in pending_proposals:
        user_id = slack_id_to_user_id(slack_user_id)
        if "yes" in text.lower():
            confirm_tool(slack_user_id, user_id, say)
        else:
            say("Reply *yes* to confirm saving the tool, or ignore to skip.")
        return

    # Match against this user's saved tools
    user_id = slack_id_to_user_id(slack_user_id)
    if not user_id:
        say("I don't recognise your Slack user ID. Ask an admin to add you to users.json.")
        return

    matches = find_matching_tool(user_id, text)

    if len(matches) == 0:
        say("No matching tool found. Type `!poll now` to check for new patterns.")
    elif len(matches) > 1:
        names = [f"`{t['tool_name']}`" for t in matches]
        say(f"Multiple tools match: {', '.join(names)}. Which one did you mean?")
    else:
        tool = matches[0]
        slot_value = extract_slot(text, tool["slot"])
        run_tool(tool, slot_value, channel, client)


def start_bot():
    handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
    handler.start()
```

### Test the checklist in isolation before wiring anything else

Write a one-off script to verify `run_tool()` works end-to-end:

```python
# test_checklist.py — run once manually to verify
from slack_sdk import WebClient
import os
from dotenv import load_dotenv
load_dotenv()

from executors import EXECUTOR_MAP
from slack_bot import run_tool

client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
channel = os.getenv("SLACK_CHANNEL")

fake_tool = {
    "tool_name": "test_checkin",
    "sequence": ["calendar", "notion", "slack"],
    "slot": "person",
    "confirmed": True,
}

run_tool(fake_tool, "Alice", channel, client)
```

Watch the Slack channel — you should see the message post and update
step-by-step without any refresh.

---

## Step B3 — main.py (~20 min)

```python
import threading
from poller import polling_loop
from detect import detect_loop
from slack_bot import start_bot

def main():
    threads = [
        threading.Thread(
            target=polling_loop,
            kwargs={"interval_seconds": 15},
            daemon=True,
            name="poller",
        ),
        threading.Thread(
            target=detect_loop,
            kwargs={"interval_seconds": 15},
            daemon=True,
            name="detector",
        ),
    ]

    for t in threads:
        t.start()
        print(f"[main] started {t.name}")

    print("[main] starting Slack bot...")
    start_bot()  # blocking — keeps process alive


if __name__ == "__main__":
    main()
```

---

## Step B4 — requirements.txt + .env.example

**requirements.txt**
```
anthropic
slack-bolt
slack-sdk
requests
python-dotenv
```

**.env.example**
```
ANTHROPIC_API_KEY=
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_SIGNING_SECRET=
SLACK_CHANNEL=C0...
NOTION_TOKEN=secret_...
NOTION_DB_ID=
```

### Slack app setup (do this before running anything)
1. Go to api.slack.com/apps → Create New App → From scratch
2. Socket Mode → Enable Socket Mode → generate `SLACK_APP_TOKEN`
3. OAuth & Permissions → Bot Token Scopes → add:
   `chat:write`, `channels:history`, `im:write`, `im:history`
4. Event Subscriptions → Enable → Subscribe to bot events:
   `message.channels`, `message.im`
5. Install to workspace → copy `SLACK_BOT_TOKEN`
6. Invite the bot to your channel: `/invite @your-bot-name`

---

## Your Done Criteria

- [ ] `python -c "from executors import check_notion; print(check_notion('Alice'))"` returns a string
- [ ] `python -c "from executors import post_slack; print(post_slack('test'))"` posts to Slack
- [ ] `python test_checklist.py` posts a message that visibly updates step-by-step
- [ ] Typing `!poll now` in Slack triggers a detection pass
- [ ] Typing a tool name (after A confirms detection works) runs the checklist
- [ ] Two users can interact simultaneously without crossing state
- [ ] `python main.py` starts all threads and bot responds in Slack

---

## Integration Checklist (do with A together)

When A delivers `storage.py` and JSON files:
- [ ] Delete all temp stubs from your local copy
- [ ] Run `python detect.py --user u1` — should print a pattern
- [ ] Run `python main.py` — all three threads start
- [ ] Type `!poll now` — correct user gets DM, other user sees nothing
- [ ] Reply `yes` — tools_store.json updated
- [ ] Type tool name + slot value — checklist runs and updates live
