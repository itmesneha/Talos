# test_checklist.py — run once manually to verify the live checklist works
# Usage: python test_checklist.py
from slack_sdk import WebClient
import os
from dotenv import load_dotenv
load_dotenv()

from agent_graph import run_tool_graph

channel = os.getenv("SLACK_CHANNEL")

fake_tool = {
    "tool_name": "test_checkin",
    "sequence": ["calendar", "notion", "slack"],
    "args": [
        {"name": "person", "description": "who to meet with", "example": "Alice"},
        {"name": "topic",  "description": "meeting topic",    "example": "Q3 review"},
    ],
    "confirmed": True,
}

print(f"Posting test checklist to channel {channel} ...")
run_tool_graph("u11", fake_tool, {"person": "Alice", "topic": "Q3 review"}, channel)
print("Done — check your Slack channel.")
