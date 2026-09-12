"""
Shared OpenRouter chat helper — replaces the local Ollama call used across
detect.py and nodes.py. All four call sites share the same shape (one
user-role prompt in, raw text content out), so this is the single place
that knows about the HTTP call, auth, and default model.
"""

import os
import requests
from dotenv import load_dotenv
load_dotenv()

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")


def chat(prompt: str, model: str = None, timeout: int = 30) -> str:
    """
    Sends a single user-role message to OpenRouter and returns the reply text.
    Raises RuntimeError / requests.HTTPError on missing key / API failure —
    callers already wrap this in their own try/except + retry loops.
    """
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    resp = requests.post(
        OPENROUTER_API_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model or OPENROUTER_MODEL,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
