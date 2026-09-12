# test_calendar_auth.py
# Tests the Google Calendar OAuth login in isolation.
# Usage: python test_calendar_auth.py u1
#
# What it does:
#   1. Starts the OAuth callback server on localhost:8080
#   2. Generates an auth URL for the given user_id
#   3. Opens the URL in your browser
#   4. Waits for you to approve access
#   5. Saves token_{user_id}.json and prints confirmation

import sys
import time
import webbrowser
import threading

from calendar_auth import (
    generate_auth_url,
    start_callback_server,
    is_calendar_connected,
    token_path,
)

user_id  = sys.argv[1] if len(sys.argv) > 1 else "u1"
slack_id = f"TEST_SLACK_{user_id}"  # placeholder — not used in this test

done = threading.Event()

def on_connected(uid, sid):
    print(f"\n✅ Calendar connected for {uid}!")
    print(f"   Token saved to: {token_path(uid)}")
    done.set()

print(f"[test] Starting OAuth callback server on http://localhost:8080/callback ...")
start_callback_server(on_token_saved=on_connected)

print(f"[test] Generating auth URL for user_id='{user_id}' ...")
try:
    url = generate_auth_url(user_id, slack_id)
except FileNotFoundError as e:
    print(f"\n❌ {e}")
    print("   Download credentials.json from:")
    print("   Google Cloud Console → APIs & Services → Credentials → your OAuth client → Download JSON")
    sys.exit(1)

print(f"\n[test] Opening browser for Google Calendar authorization...")
print(f"       URL: {url}\n")
webbrowser.open(url)

print("[test] Waiting for you to approve in the browser (timeout: 120s)...")
if done.wait(timeout=120):
    print("[test] Done. You can now run the bot.")
else:
    print("[test] Timed out. Try again or check that port 8080 is free.")
