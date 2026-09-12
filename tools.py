import os
import requests
from datetime import datetime, timedelta, timezone
from slack_sdk import WebClient
from dotenv import load_dotenv
load_dotenv()

NOTION_TOKEN    = os.getenv("NOTION_TOKEN")
NOTION_DB_ID    = os.getenv("NOTION_DB_ID")
SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = os.getenv("SLACK_CHANNEL")

_slack_client = WebClient(token=SLACK_BOT_TOKEN)


# ── Read helpers ──────────────────────────────────────────────────────────────

def check_calendar(person: str, user_id: str = None) -> str:
    """Returns the next upcoming Google Calendar event involving the given person."""
    from poller import _get_calendar_service
    service = _get_calendar_service(user_id) if user_id else None
    if not service:
        return "Calendar not connected"

    now = datetime.now(timezone.utc).isoformat()
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=now,
            q=person,
            singleEvents=True,
            orderBy="startTime",
            maxResults=1,
        ).execute()
        items = result.get("items", [])
        if items:
            ev = items[0]
            start = ev.get("start", {}).get("dateTime") or ev.get("start", {}).get("date", "")
            return f"Next event with {person}: {ev.get('summary', '(no title)')} at {start}"
        return f"No upcoming events with {person}"
    except Exception as e:
        return f"Calendar error: {e}"


def check_notion(person: str) -> str:
    """Returns open Notion tasks mentioning the given person."""
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

    for page in resp.json().get("results", []):
        for prop in page["properties"].values():
            if prop["type"] == "title":
                title = prop["title"][0]["plain_text"] if prop["title"] else ""
                if person.lower() in title.lower():
                    return f"Open item for {person}: {title}"
    return f"No open items for {person}"


# ── Tools ─────────────────────────────────────────────────────────────────────

def send_calendar_invite(
    person: str,
    user_id: str = None,
    topic: str = None,
    time_str: str = None,
    duration_minutes: int = 30,
    email: str = None,
) -> str:
    """
    Sends a Google Calendar invite via the Calendar API.

    person           — display name of who the meeting is with
    email            — attendee email address (invite is sent to this address)
    topic            — meeting title (defaults to "Meeting with <person>")
    time_str         — natural-language time, e.g. "tomorrow 2pm", "Friday 10am"
                       Defaults to tomorrow at 10 AM UTC if omitted or unparsable.
    duration_minutes — length of the event (default 30)
    """
    from poller import _get_calendar_service
    service = _get_calendar_service(user_id) if user_id else None
    if not service:
        return "Calendar not connected — skipping invite"

    # Parse time_str; fall back to tomorrow 10 AM UTC
    start = None
    if time_str:
        try:
            import dateparser
            parsed = dateparser.parse(
                time_str,
                settings={"PREFER_DATES_FROM": "future", "RETURN_AS_TIMEZONE_AWARE": True},
            )
            if parsed:
                start = parsed.astimezone(timezone.utc)
        except Exception:
            pass

    if start is None:
        start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

    end   = start + timedelta(minutes=duration_minutes)
    title = topic or f"Meeting with {person}"

    body: dict = {
        "summary": title,
        "start": {"dateTime": start.isoformat(), "timeZone": "UTC"},
        "end":   {"dateTime": end.isoformat(),   "timeZone": "UTC"},
    }

    # Add attendee so Google sends an actual invite email
    if email:
        body["attendees"] = [{"email": email, "displayName": person}]

    try:
        created = service.events().insert(
            calendarId="primary",
            body=body,
            sendUpdates="all",   # sends invite emails to all attendees
        ).execute()
        link = created.get("htmlLink", "")
        when = start.strftime("%a %b %d at %I%p UTC")
        recipient = f"{person} ({email})" if email else person
        return f"Calendar invite sent to {recipient}: '{title}' on {when} — {link}"
    except Exception as e:
        return f"Calendar error: {e}"


def write_notion_page(title: str, notes: str = None) -> str:
    """
    Writes a new page in the configured Notion database.
    Optionally adds notes as the page body paragraph.
    """
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    body = {
        "parent": {"database_id": NOTION_DB_ID},
        "properties": {
            "Name": {"title": [{"text": {"content": title}}]}
        },
    }
    if notes:
        body["children"] = [{
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [{"type": "text", "text": {"content": notes}}]
            },
        }]

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        json=body,
        timeout=10,
    )
    if resp.status_code == 200:
        return f"Notion page written: '{title}'"
    return f"Notion error {resp.status_code}: {resp.text[:120]}"


def send_slack_message(text: str, channel: str = None) -> str:
    """Sends a Slack message to the default channel (or the given channel ID)."""
    target = channel or SLACK_CHANNEL
    resp = _slack_client.chat_postMessage(channel=target, text=text)
    if resp["ok"]:
        return "Slack message sent"
    return f"Slack error: {resp.get('error')}"


# ── Tool map ──────────────────────────────────────────────────────────────────

def get_tool_map(user_id: str) -> dict:
    """
    Returns the three tools with user_id bound for calendar auth.
    Keys match the sequence identifiers produced by the LLM in detect.py.
    """
    return {
        "calendar": lambda args: send_calendar_invite(
            person=args.get("person", ""),
            user_id=user_id,
            topic=args.get("topic"),
            time_str=args.get("time"),
            email=args.get("email"),
        ),
        "notion": lambda args: write_notion_page(
            title=args.get("topic") or args.get("person", "Note"),
            notes=args.get("notes"),
        ),
        "slack": lambda args: send_slack_message(
            text=(
                f"Hi {args.get('person', 'there')}, just checking in. "
                + check_calendar(args.get("person", ""), user_id=user_id)
                + ". "
                + check_notion(args.get("person", ""))
            )
        ),
    }


# Fallback map — used when user_id is unavailable (e.g. tests)
TOOL_MAP = {
    "calendar": lambda args: send_calendar_invite(
        person=args.get("person", ""),
        topic=args.get("topic"),
        time_str=args.get("time"),
        email=args.get("email"),
    ),
    "notion": lambda args: write_notion_page(
        title=args.get("topic") or args.get("person", "Note"),
        notes=args.get("notes"),
    ),
    "slack": lambda args: send_slack_message(
        text=(
            f"Hi {args.get('person', 'there')}, just checking in. "
            + check_calendar(args.get("person", ""))
            + ". "
            + check_notion(args.get("person", ""))
        )
    ),
}
