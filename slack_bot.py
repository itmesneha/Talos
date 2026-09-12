import os
import re
import threading
import time
from datetime import datetime
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler
from dotenv import load_dotenv
load_dotenv()

from storage import (
    get_users, get_tools, get_events, write_json, read_json,
    find_matching_tool, slack_id_to_user_id,
)
from calendar_auth import (
    is_calendar_connected, generate_auth_url, start_callback_server,
)

SOURCE_ICON = {"calendar": "📅", "notion": "📝", "slack": "💬"}


def _format_event_time(raw: str) -> str:
    # Slack event timestamps are Unix epoch seconds (e.g. "1789191640.425229"),
    # calendar/notion timestamps are ISO8601 — try both.
    try:
        return datetime.fromtimestamp(float(raw)).strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except (ValueError, OSError):
        pass
    try:
        return datetime.fromisoformat(raw).strftime("%b %d, %I:%M %p").replace(" 0", " ")
    except ValueError:
        return raw

app = App(token=os.getenv("SLACK_BOT_TOKEN"))


# ── PART 4: Slash command — /tool ─────────────────────────────────────────────

@app.command("/setup")
def handle_slash_setup(ack, body, client):
    """Lets any user manually trigger the calendar setup flow."""
    ack()
    slack_user_id = body["user_id"]
    user_id = _register_user_if_new(slack_user_id, client)

    if is_calendar_connected(user_id):
        client.chat_postMessage(
            channel=slack_user_id,
            text="✅ Your Google Calendar is already connected."
        )
        return

    _prompt_calendar_setup(user_id, slack_user_id, client)


@app.command("/tool")
def handle_slash_tool(ack, body, say, client):
    """
    /tool                                    → list this user's saved tools
    /tool <tool_name> key=value key2=value2  → run the named tool with given args
    """
    ack()

    slack_user_id = body["user_id"]
    channel       = body["channel_id"]
    text          = body.get("text", "").strip()

    user_id = slack_id_to_user_id(slack_user_id)
    if not user_id:
        say("I don't recognise your Slack user ID. Ask an admin to add you to users.json.")
        return

    tools = get_tools(user_id)

    # No args → list available tools
    if not text:
        if not tools:
            say("You have no saved tools yet. I'll propose one when I detect a repeated pattern.")
            return
        lines = ["*Your saved tools:*"]
        for t in tools:
            tool_args = t.get("args") or []
            arg_hint  = " ".join(f'{a["name"]}=...' for a in tool_args) if tool_args else ""
            example   = f"`/tool {t['tool_name']}{(' ' + arg_hint) if arg_hint else ''}`"
            lines.append(f"• {example}  —  _{t.get('description', '')}_")
        say("\n".join(lines))
        return

    # Args → match and run
    matches = find_matching_tool(user_id, text)

    if len(matches) == 0:
        names = [f"`/tool {t['tool_name']}`" for t in tools]
        available = "\n".join(names) if names else "none yet"
        say(f"No matching tool found for `{text}`.\nAvailable:\n{available}")
    elif len(matches) > 1:
        names = [f"`{t['tool_name']}`" for t in matches]
        say(f"Multiple tools match: {', '.join(names)}. Be more specific.")
    else:
        tool = matches[0]
        args = parse_args(text, tool)
        if not args:
            tool_args = tool.get("args") or []
            hint = " ".join(f'{a["name"]}=...' for a in tool_args)
            say(f"Please provide arguments: `/tool {tool['tool_name']} {hint}`")
            return
        from agent_graph import run_tool_graph
        run_tool_graph(user_id, tool, args, channel)


# ── PART 3: Message routing ───────────────────────────────────────────────────

def parse_args(text: str, tool: dict) -> dict:
    """
    Parse key=value (or key="multi word") pairs from text.
    Falls back to positional mapping against the tool's args list if no pairs found.
    Returns {} if nothing could be parsed.
    """
    # Remove leading tool name
    tool_name = tool.get("tool_name", "")
    remaining = re.sub(r"^\s*" + re.escape(tool_name) + r"\s*", "", text, flags=re.I).strip()

    # Try key=value or key="quoted value"
    kv_pairs = re.findall(r'(\w+)=(?:"([^"]*)"|(\\S+))', remaining)
    if kv_pairs:
        return {name: (quoted or unquoted) for name, quoted, unquoted in kv_pairs}

    # Positional fallback: assign remaining tokens to arg names in order
    tool_args = tool.get("args") or []
    if not tool_args or not remaining:
        return {}
    tokens = remaining.split()
    return {tool_args[i]["name"]: tokens[i] for i in range(min(len(tool_args), len(tokens)))}


@app.message("")
def handle_message(message, say, client):
    slack_user_id = message.get("user")
    text = message.get("text", "").strip()
    channel = message["channel"]

    if not slack_user_id or message.get("bot_id"):
        return  # ignore bot messages

    # Register + prompt calendar if this user isn't set up yet
    user_id = _register_user_if_new(slack_user_id, client)
    if not is_calendar_connected(user_id):
        _prompt_calendar_setup(user_id, slack_user_id, client)
        return

    # Manual demo trigger — run the full LangGraph agent cycle now
    if text == "!poll now":
        from agent_graph import run_for_user
        run_for_user(user_id)
        say("Agent run triggered — check your DMs if a new pattern was found.")
        return

    # Manual demo trigger — skip detection and send a fake proposal DM
    # (useful for previewing the Save/Change/Discard buttons without real repeated activity)
    if text == "!fake proposal":
        import time as _time
        from agent_graph import agent_graph as _graph, _pending_threads

        fake_pattern = {
            "sequence": ["calendar", "notion", "slack"],
            "description": "DEMO — preview of the proposal buttons (safe to Discard).",
            "args": [{"name": "person", "description": "who to sync with", "example": "Alice"}],
        }
        thread_id = f"demo-{user_id}-{int(_time.time())}"
        config = {"configurable": {"thread_id": thread_id}}
        _graph.update_state(config, {"user_id": user_id, "pattern": fake_pattern}, as_node="pattern")
        _graph.invoke(None, config=config)
        _pending_threads[slack_user_id] = (thread_id, _time.time())
        if message.get("channel_type") != "im":
            say("Sent you a demo proposal — check your DMs!")
        return

    # Pending proposal? Route reply through LangGraph resume
    from agent_graph import resume_for_user
    if resume_for_user(slack_user_id, text, say):
        return

    matches = find_matching_tool(user_id, text)

    if len(matches) == 0:
        say("No matching tool found. Type `!poll now` to check for new patterns.")
    elif len(matches) > 1:
        names = [f"`{t['tool_name']}`" for t in matches]
        say(f"Multiple tools match: {', '.join(names)}. Which one did you mean?")
    else:
        tool = matches[0]
        args = parse_args(text, tool)
        from agent_graph import run_tool_graph
        run_tool_graph(user_id, tool, args, channel)


# ── PART 5: New user registration + calendar setup ───────────────────────────

def _register_user_if_new(slack_user_id: str, client) -> str | None:
    """
    Checks if the Slack user is in users.json.
    If not, auto-registers them and returns the new user_id.
    Returns existing user_id if already registered.
    """
    users = get_users()

    # Already registered
    existing = slack_id_to_user_id(slack_user_id)
    if existing:
        return existing

    # Fetch their Slack display name
    try:
        profile = client.users_info(user=slack_user_id)["user"]
        label   = profile.get("real_name") or profile.get("name") or slack_user_id
    except Exception:
        label = slack_user_id

    # Assign next user_id
    next_num = len(users) + 1
    user_id  = f"u{next_num}"

    users[user_id] = {
        "slack_id":      slack_user_id,
        "notion_filter": user_id,
        "label":         label,
    }
    write_json("users.json", users)

    # Add to state.json
    import json as _json
    state = read_json("state.json")
    state[user_id] = {
        "calendar_last_polled": "2024-01-01T00:00:00Z",
        "notion_last_polled":   "2024-01-01T00:00:00Z",
        "slack_last_polled":    "0",
    }
    write_json("state.json", state)

    # Add to tools_store.json
    store = read_json("tools_store.json")
    store[user_id] = []
    write_json("tools_store.json", store)

    print(f"[slack_bot] registered new user {user_id} ({label} / {slack_user_id})")
    return user_id


def _open_dm(slack_user_id: str, client) -> str:
    """Opens a DM channel with the user and returns the channel ID."""
    resp = client.conversations_open(users=slack_user_id)
    dm_channel = resp["channel"]["id"]
    print(f"[slack_bot] opened DM channel {dm_channel} for {slack_user_id}")
    return dm_channel


def _prompt_calendar_setup(user_id: str, slack_user_id: str, client):
    """DM the user with a Google Calendar auth link."""
    try:
        auth_url = generate_auth_url(user_id, slack_user_id)
        dm_channel = _open_dm(slack_user_id, client)
        resp = client.chat_postMessage(
            channel=dm_channel,
            text=(
                f"Hi! I'm Talos.\n\n"
                f"To watch your calendar activity I need access to your Google Calendar.\n"
                f"*<{auth_url}|Click here to connect Google Calendar>*\n\n"
                f"After you approve, I'll start learning your workflows automatically."
            ),
        )
        print(f"[slack_bot] DM sent ok={resp['ok']} ts={resp.get('ts')} to {user_id} ({slack_user_id})")
    except FileNotFoundError as e:
        print(f"[slack_bot] calendar setup skipped: {e}")


def _on_calendar_connected(user_id: str, slack_user_id: str):
    """Called by calendar_auth after token is saved — DM the user to confirm."""
    dm_channel = _open_dm(slack_user_id, app.client)
    app.client.chat_postMessage(
        channel=dm_channel,
        text=(
            f"✅ Google Calendar connected! I'll start watching your calendar activity.\n"
            f"Type `/tool` anytime to see your saved tools."
        ),
    )
    print(f"[slack_bot] calendar connected for {user_id}")


def setup_loop(interval_seconds: int = 3600) -> None:
    """
    Agentic setup checker — runs in a background thread.
    On every cycle:
      1. Fetches all real members in the workspace
      2. Registers any who aren't in users.json yet
      3. DMs anyone who hasn't connected Google Calendar
    Runs once immediately on start, then repeats every interval_seconds (default 1 hour).
    """
    import time as _time

    # Wait for the bot socket connection to be established before making API calls
    _time.sleep(5)

    while True:
        print("[setup_loop] scanning workspace for unconfigured members ...")
        try:
            # Fetch all non-bot, non-deleted workspace members
            response = app.client.users_list()
            members  = [
                m for m in response["members"]
                if not m.get("is_bot")
                and not m.get("deleted")
                and not m.get("is_app_user")
                and not m.get("is_restricted")
                and m["id"] != "USLACKBOT"
            ]
            print(f"[setup_loop] found {len(members)} real members")

            for member in members:
                slack_user_id = member["id"]
                user_id = _register_user_if_new(slack_user_id, app.client)

                if not is_calendar_connected(user_id):
                    print(f"[setup_loop] {user_id} ({slack_user_id}) has no calendar — prompting ...")
                    _prompt_calendar_setup(user_id, slack_user_id, app.client)

        except Exception as e:
            print(f"[setup_loop] error: {e}")

        _time.sleep(interval_seconds)


@app.event("team_join")
def handle_team_join(event, client):
    """Fires when a new member joins the workspace."""
    slack_user_id = event["user"]["id"]
    print(f"[slack_bot] new member joined: {slack_user_id}")

    # Register and prompt for calendar setup
    user_id = _register_user_if_new(slack_user_id, client)
    _prompt_calendar_setup(user_id, slack_user_id, client)


def _build_home_view(user_id: str) -> dict:
    """Builds the App Home view: saved tools (with Run buttons) + a recent activity feed."""
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🔺 Talos", "emoji": True}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "I watch your activity and turn repeated workflows into tools you can run on demand."}]},
        {"type": "divider"},
        {"type": "section", "text": {"type": "mrkdwn", "text": "*🧰 Your saved tools*"}},
    ]

    tools = get_tools(user_id)
    if not tools:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_No tools saved yet — I'll DM you a proposal when I spot a repeated pattern._"},
        })
    else:
        for t in tools:
            tool_args = t.get("args") or []
            arg_hint = ", ".join(a["name"] for a in tool_args) if tool_args else "no arguments"
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*`{t['tool_name']}`*\n{t.get('description', '')}\n_args: {arg_hint}_",
                },
                "accessory": {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "▶ Run", "emoji": True},
                    "style": "primary",
                    "action_id": "run_tool",
                    "value": t["tool_name"],
                },
            })

    blocks.append({"type": "divider"})
    blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*📜 Recent activity*"}})

    events = sorted(get_events(user_id), key=lambda e: e.get("time", ""), reverse=True)[:8]
    if not events:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": "_Nothing observed yet._"},
        })
    else:
        for e in events:
            icon = SOURCE_ICON.get(e.get("source"), "🔹")
            blocks.append({
                "type": "context",
                "elements": [{
                    "type": "mrkdwn",
                    "text": f"{icon} *{e.get('source', 'event')}* — {e.get('text', '')}  ·  _{_format_event_time(e.get('time', ''))}_",
                }],
            })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "🔄 Refresh", "emoji": True},
            "action_id": "refresh_home",
        }],
    })

    return {"type": "home", "blocks": blocks}


def _build_calendar_prompt_view(user_id: str, slack_user_id: str) -> dict:
    """Home view shown until the user connects Google Calendar."""
    try:
        auth_url = generate_auth_url(user_id, slack_user_id)
        button = {
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Connect Google Calendar", "emoji": True},
                "style": "primary",
                "url": auth_url,
                "action_id": "noop_connect_calendar",
            }],
        }
    except FileNotFoundError:
        button = {"type": "section", "text": {"type": "mrkdwn", "text": "_Calendar setup isn't configured yet — ask an admin._"}}

    return {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "🔺 Talos", "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": "To start watching your activity I need access to your Google Calendar."}},
            button,
        ],
    }


@app.event("app_home_opened")
def handle_app_home_opened(event, client):
    slack_user_id = event["user"]

    # Register if new
    user_id = _register_user_if_new(slack_user_id, client)

    if not is_calendar_connected(user_id):
        client.views_publish(user_id=slack_user_id, view=_build_calendar_prompt_view(user_id, slack_user_id))
        return

    client.views_publish(user_id=slack_user_id, view=_build_home_view(user_id))


# A button with a "url" doesn't fire an action we act on server-side, but Bolt still
# requires block actions to be acknowledged or it logs an "unhandled request" warning.
@app.action("noop_connect_calendar")
def handle_noop_connect_calendar(ack):
    ack()


@app.action("refresh_home")
def handle_refresh_home(ack, body, client):
    ack()
    slack_user_id = body["user"]["id"]
    user_id = slack_id_to_user_id(slack_user_id)
    if user_id:
        client.views_publish(user_id=slack_user_id, view=_build_home_view(user_id))


@app.action("run_tool")
def handle_run_tool_button(ack, body, client):
    ack()
    slack_user_id = body["user"]["id"]
    user_id = slack_id_to_user_id(slack_user_id)
    if not user_id:
        return

    tool_name = body["actions"][0]["value"]
    tool = next((t for t in get_tools(user_id) if t["tool_name"] == tool_name), None)
    if not tool:
        return

    tool_args = tool.get("args") or []
    if not tool_args:
        # No arguments needed — run it straight away in a DM
        dm_channel = _open_dm(slack_user_id, client)
        from agent_graph import run_tool_graph
        threading.Thread(
            target=run_tool_graph, args=(user_id, tool, {}, dm_channel), daemon=True
        ).start()
        return

    # Has arguments — collect them via a modal
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "run_tool_modal",
            "private_metadata": tool_name,
            "title": {"type": "plain_text", "text": tool_name[:24] or "Run tool"},
            "submit": {"type": "plain_text", "text": "Run"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": f"arg_{a['name']}",
                    "label": {"type": "plain_text", "text": a["name"]},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "value",
                        "placeholder": {"type": "plain_text", "text": a.get("example", "")[:150] or " "},
                    },
                }
                for a in tool_args
            ],
        },
    )


@app.view("run_tool_modal")
def handle_run_tool_modal(ack, body, client, view):
    ack()
    slack_user_id = body["user"]["id"]
    user_id = slack_id_to_user_id(slack_user_id)
    if not user_id:
        return

    tool_name = view["private_metadata"]
    tool = next((t for t in get_tools(user_id) if t["tool_name"] == tool_name), None)
    if not tool:
        return

    args = {
        block_id[len("arg_"):]: values["value"]["value"]
        for block_id, values in view["state"]["values"].items()
        if values.get("value", {}).get("value")
    }

    dm_channel = _open_dm(slack_user_id, client)
    from agent_graph import run_tool_graph
    threading.Thread(
        target=run_tool_graph, args=(user_id, tool, args, dm_channel), daemon=True
    ).start()


# ── PART 6: Proposal DM buttons (Save / Change / Discard) ────────────────────

def _update_proposal_message(client, channel: str, ts: str, text: str) -> None:
    """Replaces a proposal message's buttons with a plain status line once resolved."""
    client.chat_update(
        channel=channel,
        ts=ts,
        text=text,
        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    )


@app.action("proposal_save")
def handle_proposal_save(ack, body, client):
    ack()
    slack_id = body["user"]["id"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]

    from agent_graph import resume_for_user
    handled = resume_for_user(slack_id, "yes", lambda text: _update_proposal_message(client, channel, ts, text))
    if not handled:
        _update_proposal_message(client, channel, ts, "_This proposal has expired._")


@app.action("proposal_discard")
def handle_proposal_discard(ack, body, client):
    ack()
    slack_id = body["user"]["id"]
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]

    from agent_graph import resume_for_user
    handled = resume_for_user(slack_id, "no", lambda text: _update_proposal_message(client, channel, ts, text))
    if not handled:
        _update_proposal_message(client, channel, ts, "_This proposal has expired._")


@app.action("proposal_change")
def handle_proposal_change(ack, body, client):
    ack()
    client.views_open(
        trigger_id=body["trigger_id"],
        view={
            "type": "modal",
            "callback_id": "proposal_change_modal",
            "private_metadata": f"{body['channel']['id']}|{body['message']['ts']}",
            "title": {"type": "plain_text", "text": "Request a change"},
            "submit": {"type": "plain_text", "text": "Send"},
            "close": {"type": "plain_text", "text": "Cancel"},
            "blocks": [
                {
                    "type": "input",
                    "block_id": "change_request",
                    "label": {"type": "plain_text", "text": "What would you like to change?"},
                    "element": {
                        "type": "plain_text_input",
                        "action_id": "value",
                        "multiline": True,
                        "placeholder": {"type": "plain_text", "text": "e.g. call it prep_meeting, or add a topic argument"},
                    },
                }
            ],
        },
    )


@app.view("proposal_change_modal")
def handle_proposal_change_modal(ack, body, client, view):
    ack()
    slack_id = body["user"]["id"]
    channel, ts = view["private_metadata"].split("|")
    change_text = view["state"]["values"]["change_request"]["value"]["value"]

    _update_proposal_message(client, channel, ts, f"✏️ _You asked to change: \"{change_text}\"_")

    from agent_graph import resume_for_user
    resume_for_user(slack_id, change_text, lambda text: None)


def start_bot():
    # Start OAuth callback server before the Slack bot
    start_callback_server(on_token_saved=_on_calendar_connected)

    handler = SocketModeHandler(app, os.getenv("SLACK_APP_TOKEN"))
    handler.start()
