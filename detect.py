import json
import re
import sys
import ollama
from storage import get_users, get_events, tool_already_saved
from dotenv import load_dotenv
load_dotenv()

OLLAMA_MODEL = "llama3.1:8b"


def _extract_json(text: str) -> dict:
    """
    Robustly extract the first JSON object from LLM output.

    Tries in order:
      1. Direct json.loads on the full text.
      2. Strip ``` / ```json fences, then json.loads.
      3. Regex-find the first {...} block (handles any preamble / postamble text).
    Raises json.JSONDecodeError if all three attempts fail.
    """
    text = text.strip()

    # 1. Plain JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Strip code fences wherever they appear
    stripped = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # 3. Pull out the first {...} block (greedy, dot matches newline)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())

    raise json.JSONDecodeError("no JSON object found in LLM output", text, 0)


def cluster_by_time(events: list[dict], window_minutes: int = 10) -> list[list[dict]]:
    """
    Pure function — no API calls.
    Groups consecutive events within window_minutes of each other.
    """
    if not events:
        return []

    sorted_events = sorted(events, key=lambda e: e["time"])
    clusters = []
    current = [sorted_events[0]]

    for event in sorted_events[1:]:
        from datetime import datetime
        prev_time = datetime.fromisoformat(current[-1]["time"].replace("Z", "+00:00"))
        curr_time = datetime.fromisoformat(event["time"].replace("Z", "+00:00"))
        gap_minutes = (curr_time - prev_time).total_seconds() / 60

        if gap_minutes <= window_minutes:
            current.append(event)
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [event]

    if len(current) >= 2:
        clusters.append(current)

    return clusters


def interpret_cluster(cluster: list[dict]) -> dict | None:
    """
    Calls Claude. Returns structured JSON or None on failure.
    Retries once on malformed output.
    """
    events_text = "\n".join(
        f"- [{e['source']}] {e['time']}: {e['text']}"
        for e in cluster
    )

    prompt = f"""You are analysing a sequence of user actions across different tools.

Events (in time order):
{events_text}

Determine if these events represent one coherent multi-step routine a user repeats.

Reply with ONLY a valid JSON object, no explanation, no markdown:
{{
  "is_routine": true,
  "sequence": ["source1", "source2"],
  "args": [
    {{"name": "arg_name", "description": "what this argument represents", "example": "example value"}}
  ],
  "description": "one sentence describing the routine"
}}

If there are multiple things that vary (e.g. a person AND a topic), include one entry per arg.
If nothing varies, use an empty list for args.
If these events are NOT a routine, reply with exactly: {{"is_routine": false}}"""

    for attempt in range(3):
        try:
            print(f"[ollama] calling {OLLAMA_MODEL} (attempt {attempt + 1}/3) with {len(cluster)} events ...")
            resp = ollama.chat(
                model=OLLAMA_MODEL,
                messages=[{"role": "user", "content": prompt}]
            )
            raw = resp["message"]["content"].strip()
            print(f"[ollama] raw response: {raw[:200]}{'...' if len(raw) > 200 else ''}")
            result = _extract_json(raw)
            print(f"[ollama] parsed: is_routine={result.get('is_routine')}, sequence={result.get('sequence')}")
            if result.get("is_routine"):
                return result
            return None
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[ollama] parse error (attempt {attempt + 1}/3): {e}")
    return None


def find_repeated_pattern(user_id: str) -> dict | None:
    """
    Scoped entirely to one user.
    Returns a pattern dict if 2+ clusters share the same sequence, else None.
    """
    events = get_events(user_id)
    if len(events) < 4:
        return None

    clusters = cluster_by_time(events)
    interpreted = []

    for cluster in clusters:
        result = interpret_cluster(cluster)
        if result:
            interpreted.append(result)

    # Find if 2+ clusters share the same sequence
    from collections import Counter
    sequence_counts = Counter(
        tuple(r["sequence"]) for r in interpreted
    )

    for sequence_tuple, count in sequence_counts.items():
        if count >= 2:
            # Return the most recent matching cluster's interpretation
            for r in reversed(interpreted):
                if tuple(r["sequence"]) == sequence_tuple:
                    return r

    return None



if __name__ == "__main__":
    user_id = sys.argv[2] if len(sys.argv) > 2 else "u1"
    if "--user" in sys.argv:
        pattern = find_repeated_pattern(user_id)
        print(json.dumps(pattern, indent=2) if pattern else "No pattern found")
    else:
        print("Usage: python detect.py --user u1")
