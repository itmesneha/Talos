# test_tools.py — integration tests for all tools using real credentials
#
# Usage:
#   python test_tools.py                  # runs all tests for user u11
#   python test_tools.py --user u1        # specify a different user
#   python test_tools.py --only calendar  # run only calendar tests
#
# Prerequisites:
#   - .env with NOTION_TOKEN, NOTION_DB_ID, SLACK_BOT_TOKEN, SLACK_CHANNEL
#   - token_<user_id>.json for calendar tests

import sys
import os
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv()

# ── Parse CLI args ─────────────────────────────────────────────────────────────

user_id  = "u11"
only     = None

args = sys.argv[1:]
for i, a in enumerate(args):
    if a == "--user" and i + 1 < len(args):
        user_id = args[i + 1]
    if a == "--only" and i + 1 < len(args):
        only = args[i + 1].lower()

# ── Helpers ────────────────────────────────────────────────────────────────────

PASS = "✅"
FAIL = "❌"
SKIP = "⏭ "

def run(label: str, fn):
    try:
        result = fn()
        status = FAIL if (result and "error" in result.lower()) else PASS
        print(f"  {status} {label}")
        print(f"       → {result}")
    except Exception as e:
        print(f"  {FAIL} {label}")
        print(f"       → Exception: {e}")

def section(name: str) -> bool:
    """Print section header. Returns False if this section should be skipped."""
    if only and only not in name.lower():
        return False
    print(f"\n── {name} ──────────────────────────────────────────────")
    return True

# ── Import tools ───────────────────────────────────────────────────────────────

from tools import (
    calendar_read,
    calendar_write,
    notion_read,
    notion_write,
    slack_read,
    slack_write,
    slack_create_channel,
    slack_invite,
)

print(f"\nRunning tool tests for user_id='{user_id}'")
print(f"Slack channel: {os.getenv('SLACK_CHANNEL')}")

# ── Calendar read ──────────────────────────────────────────────────────────────

if section("calendar_read"):
    run(
        "read upcoming events (generic)",
        lambda: calendar_read(person="", user_id=user_id),
    )
    run(
        "read events for a specific person",
        lambda: calendar_read(person="Alice", user_id=user_id),
    )
    run(
        "read with no user_id (should say not connected)",
        lambda: calendar_read(person="Bob", user_id=None),
    )

# ── Calendar write ─────────────────────────────────────────────────────────────

if section("calendar_write"):
    # Use a far-future date so the test event is easy to find and delete
    start = (datetime.now(timezone.utc) + timedelta(days=30)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(hours=1)

    run(
        "create event with explicit start + end",
        lambda: calendar_write(
            person="Test Person",
            user_id=user_id,
            topic="[TEST] Talos tool test event",
            start_datetime=start.isoformat(),
            end_datetime=end.isoformat(),
        ),
    )
    run(
        "create event with no dates (should default to tomorrow 10 AM, 30 min)",
        lambda: calendar_write(
            person="Test Person",
            user_id=user_id,
            topic="[TEST] Talos default-time event",
        ),
    )
    run(
        "create event with no user_id (should say not connected)",
        lambda: calendar_write(
            person="Ghost",
            user_id=None,
            topic="[TEST] should not appear",
            start_datetime=start.isoformat(),
            end_datetime=end.isoformat(),
        ),
    )

# ── Notion read ────────────────────────────────────────────────────────────────

if section("notion_read"):
    run(
        "read tasks for a common keyword",
        lambda: notion_read(query=""),
    )
    run(
        "read tasks mentioning 'Alice'",
        lambda: notion_read(query="Alice"),
    )
    run(
        "read tasks for unlikely keyword",
        lambda: notion_read(query="xyzzy_notareal_keyword"),
    )

# ── Notion write ───────────────────────────────────────────────────────────────

if section("notion_write"):
    run(
        "create a page with title only",
        lambda: notion_write(title="[TEST] Talos tool test page"),
    )
    run(
        "create a page with title and notes",
        lambda: notion_write(
            title="[TEST] Talos test page with notes",
            notes="This page was created by test_tools.py and can be deleted.",
        ),
    )

# ── Slack read ─────────────────────────────────────────────────────────────────

if section("slack_read"):
    run(
        "read last 3 messages from default channel",
        lambda: slack_read(limit=3),
    )
    run(
        "read last 1 message from default channel",
        lambda: slack_read(limit=1),
    )
    run(
        "read from an invalid channel (should return error)",
        lambda: slack_read(channel="C000000FAKE"),
    )

# ── Slack write ────────────────────────────────────────────────────────────────

if section("slack_write"):
    run(
        "post a message to the default channel",
        lambda: slack_write(text="[TEST] Talos test_tools.py — slack_write ✅"),
    )
    run(
        "post to an invalid channel (should return error)",
        lambda: slack_write(text="should fail", channel="C000000FAKE"),
    )

# ── Slack create channel ───────────────────────────────────────────────────────

if section("slack_create_channel"):
    # Use a timestamp suffix so re-runs don't collide
    ts = datetime.now().strftime("%m%d%H%M%S")
    run(
        "create a new channel",
        lambda: slack_create_channel(name=f"talos-test-{ts}"),
    )
    run(
        "create channel with spaces in name (should be hyphenated)",
        lambda: slack_create_channel(name=f"talos test spaces {ts}2"),
    )

# ── Slack invite ───────────────────────────────────────────────────────────────

if section("slack_invite"):
    run(
        "invite to invalid channel (should return error)",
        lambda: slack_invite(channel_id="C000000FAKE", user_ids=["U000000FAKE"]),
    )

print("\n── Done ──────────────────────────────────────────────────────────────────\n")
