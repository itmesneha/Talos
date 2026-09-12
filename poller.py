import time
import os
import requests
from datetime import datetime, timezone
from storage import (
    get_users, get_state, update_state,
    append_event, read_json
)
from dotenv import load_dotenv
load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DB_ID = os.getenv("NOTION_DB_ID")

CALENDAR_SCOPES  = ["https://www.googleapis.com/auth/calendar.readonly"]
CALENDAR_ID      = os.getenv("GOOGLE_CALENDAR_ID", "primary")


def _get_calendar_service(user_id: str):
    """Returns an authenticated Google Calendar service for the given user, or None."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    token_file = f"token_{user_id}.json"
    if not os.path.exists(token_file):
        print(f"[poller] no calendar token for {user_id} — skipping calendar poll")
        return None

    creds = Credentials.from_authorized_user_file(token_file, CALENDAR_SCOPES)

    # Refresh if expired
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def _safe_updated_min(last_polled: str) -> str:
    """
    Google rejects updatedMin older than ~1 year.
    If last_polled is too old, fall back to 30 days ago.
    """
    from datetime import timedelta
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    try:
        polled_dt = datetime.fromisoformat(last_polled.replace("Z", "+00:00"))
        if polled_dt < cutoff:
            print(f"[poller] updatedMin {last_polled} too old — using 30 days ago")
            return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")
    return last_polled


def poll_calendar(user_id: str, config: dict, last_polled: str) -> list[dict]:
    """Real Google Calendar API call using last_polled as updatedMin."""
    service = _get_calendar_service(user_id)
    if service is None:
        return []

    updated_min = _safe_updated_min(last_polled)

    try:
        result = service.events().list(
            calendarId=config.get("calendar_id", CALENDAR_ID),
            updatedMin=updated_min,
            singleEvents=True,
            orderBy="updated",
            maxResults=50,
        ).execute()
    except Exception as e:
        print(f"[poller] {user_id}/calendar API error: {e}")
        return []

    events = []
    for item in result.get("items", []):
        start     = item.get("start", {})
        date_time = start.get("dateTime") or start.get("date")
        if not date_time:
            continue
        events.append({
            "user_id": user_id,
            "source":  "calendar",
            "time":    date_time,
            "text":    item.get("summary", "(no title)"),
        })

    print(f"[poller] {user_id}/calendar → {len(events)} new events")
    return events


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
