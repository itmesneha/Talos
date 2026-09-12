"""
calendar_auth.py

Handles Google Calendar OAuth for each user.

Flow:
  1. generate_auth_url(user_id, slack_id)  →  returns a URL to send to the user
  2. User clicks URL → browser opens → they approve
  3. Google redirects to http://localhost:8080/callback?code=...&state=user_id
  4. callback_server (running in a background thread) catches it
  5. Exchanges code → saves token_{user_id}.json
  6. Calls on_token_saved(user_id, slack_id) so the bot can DM the user
"""

import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from google_auth_oauthlib.flow import Flow
from dotenv import load_dotenv
load_dotenv()

SCOPES           = ["https://www.googleapis.com/auth/calendar.readonly"]
CREDENTIALS_FILE = "credentials.json"
REDIRECT_URI     = "http://localhost:8080/callback"

# Populated by start_callback_server — called by slack_bot after bot starts
_on_token_saved = None   # callback(user_id, slack_id)
_pending: dict[str, dict] = {}   # state token → {"slack_id": str, "flow": Flow}


def token_path(user_id: str) -> str:
    return f"token_{user_id}.json"


def is_calendar_connected(user_id: str) -> bool:
    return os.path.exists(token_path(user_id))


def generate_auth_url(user_id: str, slack_id: str) -> str:
    """
    Creates an OAuth URL for this user.
    Stores slack_id in _pending so the callback can DM them when done.
    """
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError(
            f"{CREDENTIALS_FILE} not found. "
            "Download it from Google Cloud Console → APIs & Services → Credentials."
        )

    flow = Flow.from_client_secrets_file(
        CREDENTIALS_FILE,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    # Use user_id as the state param so the callback knows who this is
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=user_id,
        prompt="consent",
    )
    _pending[user_id] = {"slack_id": slack_id, "flow": flow}
    return auth_url


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed  = urlparse(self.path)
        params  = parse_qs(parsed.query)

        if parsed.path != "/callback":
            self._respond(404, "Not found")
            return

        code    = params.get("code", [None])[0]
        user_id = params.get("state", [None])[0]

        if not code or not user_id:
            self._respond(400, "Missing code or state")
            return

        try:
            pending = _pending.pop(user_id, None)
            if not pending:
                self._respond(400, "Unknown or expired state")
                return

            flow = pending["flow"]
            flow.fetch_token(code=code)
            creds = flow.credentials

            with open(token_path(user_id), "w") as f:
                f.write(creds.to_json())

            print(f"[calendar_auth] saved {token_path(user_id)}")
            self._respond(200, "All done! You can close this tab and go back to Slack.")

            # Notify the bot so it can DM the user
            slack_id = pending["slack_id"]
            if _on_token_saved and slack_id:
                threading.Thread(
                    target=_on_token_saved,
                    args=(user_id, slack_id),
                    daemon=True,
                ).start()

        except Exception as e:
            print(f"[calendar_auth] token exchange error: {e}")
            self._respond(500, f"Error: {e}")

    def _respond(self, status: int, body: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *args):
        pass  # suppress default access logs


def start_callback_server(on_token_saved=None, port: int = 8080):
    """
    Start the OAuth callback server in a background daemon thread.
    on_token_saved(user_id, slack_id) is called after each successful auth.
    """
    global _on_token_saved
    _on_token_saved = on_token_saved

    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="calendar-oauth")
    thread.start()
    print(f"[calendar_auth] OAuth callback server listening on http://127.0.0.1:{port}/callback")
