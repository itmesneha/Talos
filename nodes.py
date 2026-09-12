"""
LangGraph node functions for Talos.

Each node receives the current AgentState and returns a dict of updated keys.

Nodes:
    poll_node     — polls Calendar / Notion / Slack for new events
    cluster_node  — groups events into time-based clusters (pure, no LLM)
    llm_node      — calls Ollama per cluster to label routines
    pattern_node  — finds sequences repeated 2+ times (pure, no LLM)
    propose_node  — generates tool definition via Ollama, DMs the user
    human_node    — LangGraph interrupt: pauses until Slack reply arrives
    confirm_node  — saves (with optional inline edits) or discards the tool
    executor_node — calls Ollama to write a custom execute() function for the
                    confirmed tool; saves executor_code to tools_store.json
"""

import json
import re
from collections import Counter
from typing import TypedDict, Optional

import ollama
from langgraph.types import interrupt
from detect import _extract_json

from poller import poll_user as _do_poll
from detect import cluster_by_time, interpret_cluster
from storage import get_events, get_users, append_tool, tool_already_saved, read_json, write_json


# ─────────────────────────────────────────────────────────────────────────────
# State schema
# ─────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict, total=False):
    """
    TypedDict so LangGraph merges partial node returns instead of replacing
    the whole state dict (which would drop user_id and other fields).

    Fields used by the detect-propose graph:
        user_id              str
        events               list[dict]
        clusters             list[list[dict]]
        interpreted_clusters list[dict]
        pattern              dict | None
        tool_definition      dict | None
        dm_channel           str | None
        user_response        str | None

    Fields used by the execute graph:
        args                 dict  — argument values provided by the user (e.g. {"person": "Alice"})
        channel              str   — Slack channel ID to post the checklist into
        execution_result     str | None — one-line summary returned by execute()
    """
    user_id: str
    events: list
    clusters: list
    interpreted_clusters: list
    pattern: Optional[dict]
    tool_definition: Optional[dict]
    dm_channel: Optional[str]
    user_response: Optional[str]
    args: Optional[dict]
    channel: str
    execution_result: Optional[str]
    needs_reconfirmation: Optional[bool]
    user_change_request: Optional[str]


# ─────────────────────────────────────────────────────────────────────────────
# Node 1 — poll_node
# ─────────────────────────────────────────────────────────────────────────────

def poll_node(state: AgentState) -> dict:
    """
    Calls the real Google Calendar / Notion / Slack pollers for this user,
    then loads all their events from event_log.json.
    """
    user_id = state["user_id"]
    print(f"[node:poll] polling {user_id}")
    try:
        _do_poll(user_id)
    except Exception as e:
        print(f"[node:poll] error: {e}")
    events = get_events(user_id)
    print(f"[node:poll] loaded {len(events)} events for {user_id}")
    return {"events": events}


# ─────────────────────────────────────────────────────────────────────────────
# Node 2 — cluster_node
# ─────────────────────────────────────────────────────────────────────────────

def cluster_node(state: AgentState) -> dict:
    """
    Pure function — no API calls.
    Groups consecutive events within a 10-minute sliding window.
    """
    events = state.get("events") or []
    clusters = cluster_by_time(events, window_minutes=10)
    print(f"[node:cluster] {len(events)} events → {len(clusters)} clusters")
    return {"clusters": clusters}


# ─────────────────────────────────────────────────────────────────────────────
# Node 3 — llm_node
# ─────────────────────────────────────────────────────────────────────────────

def llm_node(state: AgentState) -> dict:
    """
    Calls Ollama (llama3.1:8b) on each cluster to decide if it is a routine
    and, if so, extracts the sequence, args, and description.
    """
    clusters = state.get("clusters") or []
    interpreted = []
    for cluster in clusters:
        result = interpret_cluster(cluster)
        if result:
            interpreted.append(result)
    print(f"[node:llm] {len(clusters)} clusters → {len(interpreted)} routines identified")
    return {"interpreted_clusters": interpreted}


# ─────────────────────────────────────────────────────────────────────────────
# Node 4 — pattern_node
# ─────────────────────────────────────────────────────────────────────────────

def pattern_node(state: AgentState) -> dict:
    """
    Pure matching — no API calls.
    Finds sequences that appear in 2+ clusters and aren't already saved as tools.
    """
    user_id = state["user_id"]
    interpreted = state.get("interpreted_clusters") or []

    counts = Counter(tuple(r["sequence"]) for r in interpreted)
    for seq_tuple, count in counts.items():
        if count >= 2 and not tool_already_saved(user_id, list(seq_tuple)):
            for r in reversed(interpreted):
                if tuple(r["sequence"]) == seq_tuple:
                    print(f"[node:pattern] found repeating pattern: {list(seq_tuple)}")
                    return {"pattern": dict(r)}

    print("[node:pattern] no new repeating pattern")
    return {"pattern": None}


# ─────────────────────────────────────────────────────────────────────────────
# Shared proposal message builder (used by propose_node and re_propose_node)
# ─────────────────────────────────────────────────────────────────────────────

def _build_proposal_blocks(pattern: dict, header: str) -> list[dict]:
    steps_str = "\n".join(f"{i+1}. {s}" for i, s in enumerate(pattern.get("steps", [])))
    tool_args = pattern.get("args") or []
    args_str = "\n".join(
        f"• `{a['name']}` — {a.get('description', '')}  _(e.g. {a.get('example', '...')})_"
        for a in tool_args
    ) or "_(none)_"

    sequence_line = ""
    if pattern.get("sequence"):
        sequence_line = (
            f"You've done this *{len(pattern['sequence'])}-step sequence* at least twice:\n"
            f"`{' → '.join(pattern['sequence'])}`\n\n"
        )

    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": (
            f"{sequence_line}"
            f"*Proposed tool:* `{pattern.get('tool_name', '')}`\n_{pattern.get('description', '')}_"
        )}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*What it does:*\n{steps_str}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Arguments you provide each run:*\n{args_str}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Example usage:*\n`{pattern.get('example', '')}`"}},
        {
            "type": "actions",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "✅ Save", "emoji": True}, "style": "primary", "action_id": "proposal_save"},
                {"type": "button", "text": {"type": "plain_text", "text": "✏️ Change", "emoji": True}, "action_id": "proposal_change"},
                {"type": "button", "text": {"type": "plain_text", "text": "❌ Discard", "emoji": True}, "style": "danger", "action_id": "proposal_discard"},
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "You can also just reply here with a change request in plain English."}]},
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Node 5 — propose_node
# ─────────────────────────────────────────────────────────────────────────────

def propose_node(state: AgentState) -> dict:
    """
    Calls Ollama to produce a rich tool definition (name, description, steps,
    what_you_provide, example), then DMs the Slack user with a full explanation
    and a yes / no prompt.
    """
    user_id = state["user_id"]
    pattern = dict(state["pattern"])

    # Build LLM prompt
    args_text = json.dumps(pattern.get("args", []), indent=2)
    summary = (
        f"Sequence: {' → '.join(pattern['sequence'])}. "
        f"Arguments (what varies): {args_text}. "
        f"Description: {pattern.get('description', '')}."
    )
    prompt = f"""A user has been detected repeating this workflow pattern:
{summary}

Generate a clear tool definition for this workflow.

Reply with ONLY valid JSON:
{{
  "tool_name": "snake_case name, max 4 words",
  "description": "one clear sentence: what this tool does and when to use it",
  "args": [
    {{"name": "arg_name", "description": "what the user provides for this argument", "example": "example value"}}
  ],
  "steps": ["step 1 description", "step 2 description", ...],
  "example": "example invocation e.g. /tool weekly_sync person=Alice"
}}"""

    try:
        print("[node:propose] calling Ollama for tool definition ...")
        resp = ollama.chat(model="llama3.1:8b", messages=[{"role": "user", "content": prompt}])
        raw = resp["message"]["content"].strip()
        definition = _extract_json(raw)
        print(f"[node:propose] definition: {definition.get('tool_name')}")
    except Exception as e:
        print(f"[node:propose] LLM failed: {e} — using fallback definition")
        words = pattern.get("description", "routine").lower().split()[:3]
        definition = {
            "tool_name": "_".join(w.strip(".,") for w in words),
            "description": pattern.get("description", "Detected workflow"),
            "steps": [f"Run {s}" for s in pattern["sequence"]],
            "what_you_provide": f"the required arguments",
            "example": f"/tool {pattern.get('tool_name', 'tool')} for [name]",
        }

    pattern.update(definition)

    blocks = _build_proposal_blocks(pattern, ":mag: *I noticed a repeated workflow pattern!*")
    fallback_text = f"I noticed a repeated workflow pattern — proposed tool: {definition.get('tool_name', '')}"

    # Lazy import to avoid circular dependency (slack_bot imports agent_graph)
    from slack_bot import app as _slack_app
    users = get_users()
    slack_id = users[user_id]["slack_id"]
    dm = _slack_app.client.conversations_open(users=slack_id)
    dm_channel = dm["channel"]["id"]
    _slack_app.client.chat_postMessage(channel=dm_channel, blocks=blocks, text=fallback_text)

    print(f"[node:propose] DM sent to {user_id} ({slack_id}) via {dm_channel}")
    return {"pattern": pattern, "tool_definition": definition, "dm_channel": dm_channel}


# ─────────────────────────────────────────────────────────────────────────────
# Node 6 — human_node  (LangGraph interrupt)
# ─────────────────────────────────────────────────────────────────────────────

def human_node(state: AgentState) -> dict:
    """
    Pauses the graph at this node until the Slack bot resumes it.
    Resume by calling:
        agent_graph.invoke(Command(resume="yes …"), config=thread_config)
    """
    user_response = interrupt("waiting_for_slack_reply")
    return {"user_response": user_response}


# ─────────────────────────────────────────────────────────────────────────────
# Node 7 — confirm_node
# ─────────────────────────────────────────────────────────────────────────────

def confirm_node(state: AgentState) -> dict:
    """
    Three outcomes based on user_response:
      "yes"   → save immediately, confirmed=True
      "no"    → discard
      anything else → treat as a change request; route to re_propose_node
    """
    user_id = state["user_id"]
    pattern = dict(state.get("pattern") or {})
    user_text = (state.get("user_response") or "").strip()
    lower = user_text.lower()

    if lower.startswith("yes") and len(lower) <= 4:
        # Plain "yes" — save now
        pattern["confirmed"] = True
        append_tool(user_id, pattern)
        print(f"[node:confirm] saved '{pattern['tool_name']}' for {user_id}")
        return {"pattern": pattern, "needs_reconfirmation": False, "user_change_request": None}

    if lower.startswith("no") and len(lower) <= 3:
        print(f"[node:confirm] discarded for {user_id}")
        return {"pattern": None, "needs_reconfirmation": False, "user_change_request": None}

    # Any other response = change request
    print(f"[node:confirm] change request from {user_id}: '{user_text[:80]}'")
    return {"needs_reconfirmation": True, "user_change_request": user_text}


# ─────────────────────────────────────────────────────────────────────────────
# Node 7b — re_propose_node
# ─────────────────────────────────────────────────────────────────────────────

def _apply_change_request(pattern: dict, change_request: str) -> dict:
    """
    Calls Ollama to apply a natural-language change request to the tool definition.
    Returns an updated copy of pattern.
    """
    prompt = f"""You are updating a workflow tool definition based on user feedback.

Current tool:
  name: {pattern.get('tool_name', '')}
  description: {pattern.get('description', '')}
  args: {json.dumps(pattern.get('args', []))}
  steps: {json.dumps(pattern.get('steps', []))}

User requested change: "{change_request}"

Apply only what the user asked. Return ONLY a valid JSON object with any changed fields.
You may include: "tool_name", "description", "args", "steps".
For "args", return the full updated list.
Omit fields that should stay the same."""

    try:
        print(f"[node:re_propose] applying change via Ollama: '{change_request[:80]}'")
        resp = ollama.chat(model="llama3.1:8b", messages=[{"role": "user", "content": prompt}])
        raw = resp["message"]["content"].strip()
        updates = _extract_json(raw)
        updated = dict(pattern)
        for key in ("tool_name", "description", "args", "steps"):
            if key in updates:
                updated[key] = updates[key]
        return updated
    except Exception as e:
        print(f"[node:re_propose] Ollama change failed: {e} — pattern unchanged")
        return dict(pattern)


def re_propose_node(state: AgentState) -> dict:
    """
    Applies the user's change request to the tool definition via Ollama,
    then re-sends the proposal DM with the same yes / no / describe-change options.
    Loops back through human_node → confirm_node until the user says yes or no.
    """
    from slack_bot import app as _slack_app

    user_id = state["user_id"]
    pattern = dict(state["pattern"])
    change_request = (state.get("user_change_request") or "").strip()

    if change_request:
        pattern = _apply_change_request(pattern, change_request)

    pattern.pop("confirmed", None)

    users = get_users()
    slack_id = users[user_id]["slack_id"]

    blocks = _build_proposal_blocks(pattern, "✏️ *Got it — here's the updated tool:*")
    fallback_text = f"Updated tool proposal: {pattern.get('tool_name', '')}"

    dm = _slack_app.client.conversations_open(users=slack_id)
    dm_channel = dm["channel"]["id"]
    _slack_app.client.chat_postMessage(channel=dm_channel, blocks=blocks, text=fallback_text)

    print(f"[node:re_propose] sent updated proposal for '{pattern['tool_name']}' to {user_id}")
    return {
        "pattern": pattern,
        "needs_reconfirmation": False,
        "user_response": None,
        "user_change_request": None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Node 8 — executor_node
# ─────────────────────────────────────────────────────────────────────────────

_TOOL_DOCS = """
Available tools (already in scope — do NOT import anything):

  READ:
    calendar_read(person, user_id=None) -> str
        Returns the next upcoming Google Calendar event for a person.

    notion_read(query) -> str
        Returns open Notion tasks mentioning the given person or keyword.

    slack_read(channel=None, limit=5) -> str
        Returns recent messages from a Slack channel.

  WRITE:
    calendar_write(person, user_id=None, topic=None, start_datetime=None, end_datetime=None, email=None) -> str
        Creates a Google Calendar event and sends an invite.
        person         — display name of attendee (required)
        topic          — meeting title
        start_datetime — ISO 8601 string, e.g. "2026-09-15T10:00:00Z"
        end_datetime   — ISO 8601 string, e.g. "2026-09-15T11:00:00Z"
        email          — attendee email address

    notion_write(title, notes=None) -> str
        Creates a new Notion page. notes is optional body text.

    slack_write(text, channel=None) -> str
        Posts a message to a Slack channel.

    slack_create_channel(name) -> str
        Creates a new public Slack channel. Returns "Created #<name> (ID: <channel_id>)" on success.

    slack_invite(channel_id, user_ids) -> str
        Invites a list of Slack user IDs (Python list of strings) to a channel.

The function receives args as a dict — access values with args["key"] or args.get("key").
"""


def executor_node(state: AgentState) -> dict:
    """
    Runs only when a tool was confirmed (pattern["confirmed"] == True).

    Calls Ollama to generate a custom Python function:
        def execute(args: dict) -> str: ...

    The function body uses the available executor helpers and is
    tailored to the tool's specific steps and arguments.

    The generated code is:
      1. Validated with compile() — discarded if it has syntax errors.
      2. Patched into the tool entry in tools_store.json as "executor_code".

    At run-time, execute_node execs this code in a sandbox that
    exposes the helper functions.
    """
    pattern = state.get("pattern") or {}
    if not pattern.get("confirmed"):
        return {}  # nothing to do — tool was discarded

    user_id = state["user_id"]
    tool_name = pattern.get("tool_name", "unknown")
    tool_args = pattern.get("args") or []

    args_str = "\n".join(
        f"  args[\"{a['name']}\"]  — {a.get('description', '')}  (e.g. \"{a.get('example', '?')}\")"
        for a in tool_args
    ) or "  (none)"
    steps_str = "\n".join(
        f"  {i+1}. {s}" for i, s in enumerate(pattern.get("steps", []))
    )
    prompt = f"""You are writing a Python executor function for a workflow automation tool.

Tool name: {tool_name}
Description: {pattern.get('description', '')}
Arguments available in args dict:
{args_str}
Steps to perform:
{steps_str}

{_TOOL_DOCS}

Write ONLY this Python function — no imports, no explanation, no markdown:

def execute(args: dict) -> str:
    # access arguments with args["name"]
    # call the tools in the order matching the steps
    ...
    return "<one-line summary of what was done>"

Rules:
- Access argument values via args["name"], e.g. args["person"], args["topic"], args["time"], args["email"].
- For a calendar invite: calendar_write(person=args["person"], topic=args.get("topic"), start_datetime=args.get("start_datetime"), end_datetime=args.get("end_datetime"), email=args.get("email"))
- For a Notion page: write_notion_page(title=args.get("topic") or args.get("person", "Note"), notes=args.get("notes"))
- For a Slack message: send_slack_message(text=<message text>)
- Call tools in the order matching the steps.
- Return a concise one-line summary string.
- Do not use any other imports or globals.
"""

    try:
        print(f"[node:executor] calling Ollama to generate executor for '{tool_name}' ...")
        resp = ollama.chat(model="llama3.1:8b", messages=[{"role": "user", "content": prompt}])
        code = resp["message"]["content"].strip()

        # Strip any markdown code fences (``` or ```python)
        code = re.sub(r"^```(?:python)?", "", code, flags=re.MULTILINE).strip()
        code = re.sub(r"```$", "", code, flags=re.MULTILINE).strip()

        # Validate syntax before saving
        compile(code, f"<executor:{tool_name}>", "exec")
        print(f"[node:executor] generated executor for '{tool_name}':\n{code[:120]}...")

        # Patch executor_code into the saved tool entry
        store = read_json("tools_store.json")
        for tool in store.get(user_id, []):
            if tool.get("tool_name") == tool_name:
                tool["executor_code"] = code
                break
        write_json("tools_store.json", store)

        pattern = dict(pattern)
        pattern["executor_code"] = code

    except SyntaxError as e:
        print(f"[node:executor] generated code has syntax error: {e} — skipping")
    except Exception as e:
        print(f"[node:executor] failed: {e} — tool will fall back to TOOL_MAP")

    return {"pattern": pattern}


# ─────────────────────────────────────────────────────────────────────────────
# Node 9 — execute_node  (entry point of the separate execute_graph)
# ─────────────────────────────────────────────────────────────────────────────

def execute_node(state: AgentState) -> dict:
    """
    Runs the tool stored in state["pattern"] with state["args"].
    Posts a live-updating Slack checklist to state["channel"].

    Two paths:
      1. Custom executor  — tool has "executor_code" generated by executor_node.
                           Runs it in a sandbox with the four helper functions.
      2. Default checklist — iterates over tool["sequence"] and calls TOOL_MAP.
    """
    from slack_bot import app as _slack_app
    import time as _time

    tool      = state["pattern"]
    args      = state.get("args") or {}
    channel   = state["channel"]
    client    = _slack_app.client
    tool_name = tool["tool_name"]
    user_id   = state["user_id"]

    from tools import (
        check_calendar, check_notion,
        send_calendar_invite, write_notion_page, send_slack_message,
        calendar_read, calendar_write,
        notion_read, notion_write,
        slack_read, slack_write, slack_create_channel, slack_invite,
        get_tool_map,
    )

    executor_map = get_tool_map(user_id)

    # Build a short label for display (e.g. "person=Alice, topic=Q3")
    args_label = ", ".join(f"{k}={v}" for k, v in args.items()) if args else ""

    # ── Path 1: custom executor_code ─────────────────────────────────────────
    if tool.get("executor_code"):
        resp = client.chat_postMessage(
            channel=channel,
            text=f"\\ Running *{tool_name}*{(' — ' + args_label) if args_label else ''}..."
        )
        msg_ts = resp["ts"]
        try:
            sandbox = {
                # Granular primitives
                "calendar_read":        lambda person: calendar_read(person, user_id=user_id),
                "calendar_write":       lambda **kw: calendar_write(user_id=user_id, **kw),
                "notion_read":          notion_read,
                "notion_write":         notion_write,
                "slack_read":           slack_read,
                "slack_write":          slack_write,
                "slack_create_channel": slack_create_channel,
                "slack_invite":         slack_invite,
                # Legacy names kept so old executor_code still works
                "check_calendar":       lambda person: check_calendar(person, user_id=user_id),
                "check_notion":         check_notion,
                "send_calendar_invite": lambda **kw: send_calendar_invite(user_id=user_id, **kw),
                "write_notion_page":    write_notion_page,
                "send_slack_message":   send_slack_message,
            }
            exec(tool["executor_code"], sandbox)
            summary = sandbox["execute"](args)
            client.chat_update(
                channel=channel,
                ts=msg_ts,
                text=f"✅ *{tool_name}* complete{(' — ' + args_label) if args_label else ''}.\n{summary}"
            )
            return {"execution_result": summary}
        except Exception as e:
            error_msg = f"❌ *{tool_name}* failed: {e}"
            client.chat_update(channel=channel, ts=msg_ts, text=error_msg)
            return {"execution_result": error_msg}

    # ── Path 2: default checklist (get_tool_map) ─────────────────────────────
    steps = tool["sequence"]

    def render(results: dict, running: str = None) -> str:
        header = f"Running *{tool_name}*{(' — ' + args_label) if args_label else ''}..."
        lines = [header]
        for step in steps:
            if step in results:
                lines.append(f"✅ {step} — {results[step]}")
            elif step == running:
                lines.append(f"\\ {step}")
            else:
                lines.append(f"☐ {step}")
        return "\n".join(lines)

    resp = client.chat_postMessage(channel=channel, text=render({}))
    msg_ts = resp["ts"]
    results = {}

    for step in steps:
        client.chat_update(channel=channel, ts=msg_ts, text=render(results, running=step))
        executor = executor_map.get(step)
        try:
            result = executor(args) if executor else f"no executor for {step}"
        except Exception as e:
            result = f"error: {e}"
        results[step] = result
        client.chat_update(channel=channel, ts=msg_ts, text=render(results))
        _time.sleep(0.5)

    final = render(results) + f"\n\n✅ *{tool_name}* complete."
    client.chat_update(channel=channel, ts=msg_ts, text=final)
    return {"execution_result": final}
