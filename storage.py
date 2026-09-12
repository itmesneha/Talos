import json
import os
import threading
from pathlib import Path

USERS_FILE       = "users.json"
STATE_FILE       = "state.json"
EVENT_LOG_FILE   = "event_log.json"
TOOLS_STORE_FILE = "tools_store.json"

# Serialises all read-modify-write operations so parallel user threads
# don't corrupt files by truncating during a concurrent read.
_file_lock = threading.Lock()


def read_json(path: str) -> any:
    with open(path) as f:
        content = f.read().strip()
    if not content:
        raise ValueError(f"{path} is empty")
    return json.loads(content)


def write_json(path: str, data: any) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_users() -> dict:
    return read_json(USERS_FILE)


def get_state() -> dict:
    return read_json(STATE_FILE)


def update_state(state: dict) -> None:
    with _file_lock:
        write_json(STATE_FILE, state)


def append_event(event: dict) -> None:
    with _file_lock:
        try:
            log = read_json(EVENT_LOG_FILE)
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            log = []
        # Deduplicate: skip if an event with the same user, source, and timestamp exists
        if any(
            e["user_id"] == event["user_id"]
            and e["source"] == event["source"]
            and e["time"] == event["time"]
            for e in log
        ):
            return
        log.append(event)
        write_json(EVENT_LOG_FILE, log)


def get_events(user_id: str = None) -> list[dict]:
    with _file_lock:
        try:
            log = read_json(EVENT_LOG_FILE)
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            log = []
    if user_id:
        return [e for e in log if e["user_id"] == user_id]
    return log


def get_tools(user_id: str) -> list[dict]:
    with _file_lock:
        try:
            store = read_json(TOOLS_STORE_FILE)
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            store = {}
    return store.get(user_id, [])


def append_tool(user_id: str, tool: dict) -> None:
    with _file_lock:
        try:
            store = read_json(TOOLS_STORE_FILE)
        except (ValueError, json.JSONDecodeError, FileNotFoundError):
            store = {}
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
