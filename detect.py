import json
import re
import sys
import requests
import llm
from storage import get_users, get_events, tool_already_saved
from matching import group_similar_sequences
from dotenv import load_dotenv
load_dotenv()

# Words too generic to identify a specific project/person/topic when spotted
# in event text — excluded from entity-overlap clustering below.
_ENTITY_STOPWORDS = {
    "the", "this", "that", "with", "for", "and", "new", "meeting", "project",
    "check", "checked", "notes", "prep", "update", "updated", "added",
    "created", "invite", "invited", "slack", "notion", "calendar", "hi",
    "kickoff", "team", "message", "messaged", "page", "channel", "template",
}


def _extract_entities(text: str) -> set[str]:
    """
    Pull likely project/person-name tokens out of an event's text — a cheap,
    deterministic stand-in for "these events are about the same thing".
    Normalizes to lowercase since the same name shows up capitalized in
    Notion/Calendar text ("Falcon") but lowercase-slugged in Slack channel
    names ("#project-falcon").
    """
    text = text or ""
    entities = set()

    # Proper-noun-looking words (as they appear in Notion/Calendar titles)
    for w in re.findall(r"\b[A-Za-z][a-zA-Z0-9'-]+\b", text):
        if w[0].isupper() and w.lower() not in _ENTITY_STOPWORDS:
            entities.add(w.lower())

    # Slack channel-name slugs, e.g. "#project-falcon" -> {"project", "falcon"}
    for slug in re.findall(r"#([\w-]+)", text):
        for part in re.split(r"[-_]", slug):
            if len(part) >= 3 and part.lower() not in _ENTITY_STOPWORDS:
                entities.add(part.lower())

    return entities


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
    Groups events into a cluster when EITHER:
      - the gap to the previous event is within window_minutes, OR
      - the event shares an entity (e.g. a project/person name) with
        something already in the current cluster, however far apart in time.

    The entity check is what lets a ritual that unfolds over an hour+
    (Notion project page now, Slack channel 45 min later) still cluster as
    one candidate routine instead of being time-sliced apart.
    """
    if not events:
        return []

    from datetime import datetime, timezone

    def _parse_time(t: str) -> datetime:
        # Slack event timestamps are Unix epoch seconds (e.g. "1789191318.221739"),
        # calendar/notion timestamps are ISO8601 — try both.
        try:
            return datetime.fromtimestamp(float(t), tz=timezone.utc)
        except (ValueError, OSError):
            pass
        return datetime.fromisoformat(t.replace("Z", "+00:00"))

    # Sort by actual instant, not raw ISO string — sources mix timezone
    # offsets (Google Calendar returns "+08:00", others "Z"), and comparing
    # those as strings puts events in the wrong order, producing negative
    # "gaps" that trivially pass any window check.
    sorted_events = sorted(events, key=lambda e: _parse_time(e["time"]))
    clusters = []
    current = [sorted_events[0]]
    current_entities = set(_extract_entities(sorted_events[0].get("text", "")))

    for event in sorted_events[1:]:
        prev_time = _parse_time(current[-1]["time"])
        curr_time = _parse_time(event["time"])
        gap_minutes = (curr_time - prev_time).total_seconds() / 60

        event_entities = _extract_entities(event.get("text", ""))
        shares_entity = bool(event_entities & current_entities)

        if gap_minutes <= window_minutes or shares_entity:
            current.append(event)
            current_entities |= event_entities
        else:
            if len(current) >= 2:
                clusters.append(current)
            current = [event]
            current_entities = set(event_entities)

    if len(current) >= 2:
        clusters.append(current)

    return clusters


def interpret_cluster(cluster: list[dict]) -> dict | None:
    """
    Calls Claude. Returns structured JSON or None on failure.
    Retries once on malformed output.
    """
    events_text = "\n".join(
        f"- [{e['source']}{':' + e['action'] if e.get('action') else ''}] {e['time']}: {e['text']}"
        for e in cluster
    )

    prompt = f"""You are analysing a sequence of user actions across different tools.

Events (in time order):
{events_text}

Determine if these events represent one coherent multi-step routine a user repeats.
Use the content of each event (names, project titles, recurring phrases) — not just
timing — to judge whether these belong together. For example, if a Notion event and
a later Slack event both mention the same project name, treat that as strong evidence
they're part of the same routine even if they weren't close in time.

Reply with ONLY a valid JSON object, no explanation, no markdown:
{{
  "is_routine": true,
  "args": [
    {{"name": "arg_name", "description": "what this argument represents (varies each time the routine runs)", "example": "example value"}}
  ],
  "fixed_args": [
    {{"name": "fixed_name", "value": "the constant value or list of values that stays the same every time"}}
  ],
  "description": "one sentence describing the routine"
}}

"args" is for things that differ between occurrences (e.g. the project name) —
the user will supply these each time the tool runs.
"fixed_args" is for things that stay identical across occurrences (e.g. the same
4-5 people invited every time, the same Notion template, a fixed channel-naming
convention) — these get baked into the tool instead of asked for each run.
If nothing varies, use an empty list for args. If nothing is constant, use an
empty list for fixed_args.
If these events are NOT a routine, reply with exactly: {{"is_routine": false}}"""

    for attempt in range(3):
        try:
            print(f"[llm] calling {llm.OPENROUTER_MODEL} (attempt {attempt + 1}/3) with {len(cluster)} events ...")
            raw = llm.chat(prompt).strip()
            print(f"[llm] raw response: {raw[:200]}{'...' if len(raw) > 200 else ''}")
            result = _extract_json(raw)
            print(f"[llm] parsed: is_routine={result.get('is_routine')}")
            if result.get("is_routine"):
                # Compute deterministically from the events rather than trusting
                # the LLM's wording — asked twice for the same underlying routine,
                # it doesn't reliably reproduce identical phrasing ("calendar" one
                # call, "calendar.meeting_created" the next), which silently broke
                # matching against already-saved tools and against other clusters
                # of the same routine.
                result["sequence"] = [e.get("action") or e["source"] for e in cluster]
                result.setdefault("fixed_args", [])
                print(f"[llm] sequence (derived): {result['sequence']}")
                return result
            return None
        except (json.JSONDecodeError, KeyError) as e:
            print(f"[llm] parse error (attempt {attempt + 1}/3): {e}")
        except (requests.RequestException, RuntimeError) as e:
            print(f"[llm] request error (attempt {attempt + 1}/3): {e}")
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

    # Find if 2+ clusters describe substantially the same routine.
    # Fuzzy (set-overlap) instead of exact-tuple equality — a ritual missing
    # one step on a given occurrence still counts as a repeat.
    for group in group_similar_sequences(interpreted):
        if len(group) >= 2:
            return group[-1]  # most recent matching interpretation

    return None



if __name__ == "__main__":
    user_id = sys.argv[2] if len(sys.argv) > 2 else "u1"
    if "--user" in sys.argv:
        pattern = find_repeated_pattern(user_id)
        print(json.dumps(pattern, indent=2) if pattern else "No pattern found")
    else:
        print("Usage: python detect.py --user u1")
