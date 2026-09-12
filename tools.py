import os
import re
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
    start_datetime: str = None,
    end_datetime: str = None,
    email: str = None,
) -> str:
    """
    Sends a Google Calendar invite via the Calendar API.

    person         — display name of who the meeting is with
    email          — attendee email address (invite is sent to this address)
    topic          — meeting title (defaults to "Meeting with <person>")
    start_datetime — ISO 8601 string, e.g. "2026-09-15T10:00:00Z"
                     Defaults to tomorrow at 10 AM UTC if omitted.
    end_datetime   — ISO 8601 string, e.g. "2026-09-15T11:00:00Z"
                     Defaults to 30 minutes after start if omitted.
    """
    from poller import _get_calendar_service
    service = _get_calendar_service(user_id) if user_id else None
    if not service:
        return "Calendar not connected — skipping invite"

    # Parse start; fall back to tomorrow 10 AM UTC
    try:
        start = datetime.fromisoformat(start_datetime.replace("Z", "+00:00")) if start_datetime else None
    except (ValueError, AttributeError):
        start = None

    if start is None:
        start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

    # Parse end; fall back to start + 30 min
    try:
        end = datetime.fromisoformat(end_datetime.replace("Z", "+00:00")) if end_datetime else None
    except (ValueError, AttributeError):
        end = None

    if end is None:
        end = start + timedelta(minutes=30)

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


# ── Granular primitives ───────────────────────────────────────────────────────

def calendar_read(person: str, user_id: str = None) -> str:
    """Returns the next upcoming calendar event for a person."""
    return check_calendar(person, user_id=user_id)


def calendar_write(
    person: str,
    user_id: str = None,
    topic: str = None,
    start_datetime: str = None,
    end_datetime: str = None,
    email: str = None,
) -> str:
    """Creates a Google Calendar event and sends an invite."""
    return send_calendar_invite(person, user_id, topic, start_datetime, end_datetime, email)


def notion_read(query: str) -> str:
    """Returns open Notion tasks mentioning the given person or keyword."""
    return check_notion(query)


def notion_write(title: str, notes: str = None) -> str:
    """Creates a new Notion page with an optional body."""
    return write_notion_page(title, notes)


def slack_read(channel: str = None, limit: int = 5) -> str:
    """Returns recent messages from a Slack channel."""
    target = channel or SLACK_CHANNEL
    try:
        resp = _slack_client.conversations_history(channel=target, limit=limit)
        if not resp["ok"]:
            return f"Slack error: {resp.get('error')}"
        messages = resp.get("messages", [])
        if not messages:
            return "No recent messages"
        lines = [f"- {m.get('text', '(no text)')}" for m in messages]
        return "Recent messages:\n" + "\n".join(lines)
    except Exception as e:
        return f"Slack read error: {e}"


def slack_write(text: str, channel: str = None) -> str:
    """Posts a message to a Slack channel."""
    return send_slack_message(text, channel)


def slack_create_channel(name: str) -> str:
    """
    Creates a new public Slack channel.
    Returns a string containing the channel ID (e.g. "Created #name (ID: C123)").
    """
    clean = name.lower().replace(" ", "-")
    try:
        resp = _slack_client.conversations_create(name=clean)
        if resp["ok"]:
            channel_id = resp["channel"]["id"]
            return f"Created #{clean} (ID: {channel_id})"
        return f"Slack error: {resp.get('error')}"
    except Exception as e:
        return f"Slack channel creation error: {e}"


_SLACK_ID_RE = re.compile(r"^[UW][A-Z0-9]{8,}$")


def slack_lookup_user_id(name: str) -> str | None:
    """
    Resolves a display name (e.g. "Priya") to a Slack user ID by searching
    the workspace member list. Detection only ever sees names typed in
    message text, never IDs, so anything that invites people by name needs
    this to turn them into something the Slack API accepts.
    """
    try:
        resp = _slack_client.users_list()
        if not resp["ok"]:
            return None
        name_lower = (name or "").strip().lower()
        if not name_lower:
            return None
        for member in resp.get("members", []):
            profile = member.get("profile", {})
            candidates = {
                member.get("name", ""),
                member.get("real_name", ""),
                profile.get("real_name", ""),
                profile.get("display_name", ""),
            }
            if any(c.lower() == name_lower for c in candidates if c):
                return member["id"]
    except Exception:
        pass
    return None


def slack_invite(channel_id: str, user_ids: list) -> str:
    """
    Invites people to a channel. Each entry in user_ids may be a real Slack
    user ID OR a display name — names get resolved via slack_lookup_user_id
    first, since detection only ever captures names, not IDs.
    """
    resolved, unresolved = [], []
    for entry in user_ids or []:
        if _SLACK_ID_RE.match(entry or ""):
            resolved.append(entry)
            continue
        uid = slack_lookup_user_id(entry)
        (resolved if uid else unresolved).append(uid or entry)

    if not resolved:
        return f"Slack invite error: could not resolve any of {user_ids} to a workspace member"

    try:
        resp = _slack_client.conversations_invite(
            channel=channel_id, users=",".join(resolved)
        )
        if resp["ok"]:
            note = f" (couldn't find: {', '.join(unresolved)})" if unresolved else ""
            return f"Invited {len(resolved)} users to channel{note}"
        return f"Slack invite error: {resp.get('error')}"
    except Exception as e:
        return f"Slack invite error: {e}"


# ── Tool map ──────────────────────────────────────────────────────────────────

def _first_present(args: dict, *keys, default=None):
    """
    Returns the first non-empty value found under any of `keys` in args.
    detect.py's LLM freely names args/fixed_args (e.g. "invitees" instead of
    "user_ids", "project_name" instead of "name") — this lets the tool map
    accept whatever name it picked instead of requiring an exact match.
    """
    for k in keys:
        v = args.get(k)
        if v:
            return v
    return default


def get_tool_map(user_id: str) -> dict:
    """
    Returns the three tools with user_id bound for calendar auth.
    Keys match the sequence identifiers produced by the LLM in detect.py.
    """
    return {
        # Legacy keys (kept for backward compat with stored executor_code)
        "calendar": lambda args: send_calendar_invite(
            person=args.get("person", ""),
            user_id=user_id,
            topic=args.get("topic"),
            start_datetime=args.get("start_datetime"),
            end_datetime=args.get("end_datetime"),
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
        # Granular primitives
        "calendar_read": lambda args: calendar_read(
            person=args.get("person", ""),
            user_id=user_id,
        ),
        "calendar_write": lambda args: calendar_write(
            person=args.get("person", ""),
            user_id=user_id,
            topic=args.get("topic"),
            start_datetime=args.get("start_datetime"),
            end_datetime=args.get("end_datetime"),
            email=args.get("email"),
        ),
        "notion_read": lambda args: notion_read(
            query=args.get("query") or args.get("person", ""),
        ),
        "notion_write": lambda args: notion_write(
            title=_first_present(args, "title", "topic", "project_name", "person", default="Note"),
            notes=args.get("notes"),
        ),
        "slack_read": lambda args: slack_read(
            channel=args.get("channel"),
            limit=int(args.get("limit", 5)),
        ),
        "slack_write": lambda args: slack_write(
            text=args.get("text", ""),
            channel=args.get("channel"),
        ),
        "slack_create_channel": lambda args: slack_create_channel(
            name=_first_present(args, "name", "project_name", default="new-project"),
        ),
        "slack_invite": lambda args: slack_invite(
            channel_id=args.get("channel_id", ""),
            user_ids=_first_present(args, "user_ids", "invitees", "people", "team", "members", default=[]),
        ),
    }


# Fallback map — used when user_id is unavailable (e.g. tests)
TOOL_MAP = {
    # Legacy keys
    "calendar": lambda args: send_calendar_invite(
        person=args.get("person", ""),
        topic=args.get("topic"),
        start_datetime=args.get("start_datetime"),
        end_datetime=args.get("end_datetime"),
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
    # Granular primitives
    "calendar_read":        lambda args: calendar_read(person=args.get("person", "")),
    "calendar_write":       lambda args: calendar_write(
        person=args.get("person", ""),
        topic=args.get("topic"),
        start_datetime=args.get("start_datetime"),
        end_datetime=args.get("end_datetime"),
        email=args.get("email"),
    ),
    "notion_read":          lambda args: notion_read(query=args.get("query") or args.get("person", "")),
    "notion_write":         lambda args: notion_write(
        title=_first_present(args, "title", "topic", "project_name", "person", default="Note"),
        notes=args.get("notes"),
    ),
    "slack_read":           lambda args: slack_read(channel=args.get("channel"), limit=int(args.get("limit", 5))),
    "slack_write":          lambda args: slack_write(text=args.get("text", ""), channel=args.get("channel")),
    "slack_create_channel": lambda args: slack_create_channel(name=_first_present(args, "name", "project_name", default="new-project")),
    "slack_invite":         lambda args: slack_invite(channel_id=args.get("channel_id", ""), user_ids=_first_present(args, "user_ids", "invitees", "people", "team", "members", default=[])),
}


# ── Action-tag bridge ───────────────────────────────────────────────────────────
# detect.py/poller.py tag events with specific actions ("slack.channel_created",
# "notion.template_added", ...); this tool map is keyed by verb_noun primitive
# names ("slack_create_channel", "notion_write", ...). The two vocabularies
# evolved independently — this is the one place that translates between them.

_ACTION_TO_TOOL = {
    "notion.page_created":    "notion_write",
    "notion.page_updated":    "notion_write",
    "notion.template_added":  "notion_write",
    "slack.channel_created":  "slack_create_channel",
    "slack.member_invited":   "slack_invite",
    "slack.message_posted":   "slack_write",
    "slack.topic_changed":    "slack_write",
    "slack.purpose_changed":  "slack_write",
    "calendar.event_created": "calendar_write",
    "calendar.event_updated": "calendar_write",
}


def resolve_executor(executor_map: dict, step: str):
    """
    Maps a detected sequence step to the right entry in executor_map/TOOL_MAP:
    exact key match first, then the action-tag translation above, then a bare
    source-name fallback ("slack.channel_created" -> "slack") for anything
    unmapped. Returns None if nothing matches.
    """
    if step in executor_map:
        return executor_map[step]
    mapped = _ACTION_TO_TOOL.get(step)
    if mapped and mapped in executor_map:
        return executor_map[mapped]
    return executor_map.get(step.split(".")[0])
