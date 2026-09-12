# Tool-Synthesis Agent — Agentic Workflow

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          python main.py                                      │
│                                                                              │
│   Thread 1: Poller          Thread 2: Detector        Thread 3: Slack Bot   │
│   (every 15s)               (every 15s)               (always on)           │
└────────┬────────────────────────────┬──────────────────────────┬────────────┘
         │                            │                          │
         ▼                            │                          ▼
┌─────────────────┐                   │              ┌───────────────────────┐
│   poller.py     │                   │              │     slack_bot.py      │
│                 │                   │              │                       │
│ poll_calendar() │──┐                │              │  handle_message()     │
│ poll_notion()   │  │  writes        │              │  ┌──────────────────┐ │
│ poll_slack()    │  │  events        │              │  │ !poll now        │ │
└─────────────────┘  │                │              │  │ → poll_now()     │ │
                     ▼                │              │  │ → find_pattern() │ │
         ┌───────────────────┐        │              │  └──────────────────┘ │
         │  event_log.json   │        │              │  ┌──────────────────┐ │
         │                   │◄───────┘ reads        │  │ "yes"            │ │
         │ {user_id, source, │        │              │  │ → confirm_tool() │ │
         │  time, text}      │        │              │  └──────────────────┘ │
         └───────────────────┘        │              │  ┌──────────────────┐ │
                     ▲                │              │  │ "run it for X"   │ │
                     │                │              │  │ → run_tool()     │ │
         ┌───────────────────┐        │              │  └──────────────────┘ │
         │   detect.py       │◄───────┘              └───────────┬───────────┘
         │                   │                                   │
         │ cluster_by_time() │   groups events within 10 min     │
         │        ↓          │                                   │
         │ interpret_        │   calls Ollama per cluster        ▼
         │  cluster()        │◄──────────────────────  ┌─────────────────┐
         │        ↓          │   returns JSON:          │   executors.py  │
         │ find_repeated_    │   {is_routine, sequence, │                 │
         │  pattern()        │    slot, slot_value,     │ check_calendar()│
         │        ↓          │    description}          │ check_notion()  │
         │ 2+ clusters with  │                          │ post_slack()    │
         │ same sequence?    │                          │ draft_message() │
         └────────┬──────────┘                          └────────┬────────┘
                  │ yes                                          │
                  ▼                                             │ results
         ┌─────────────────┐    DM to user                     │
         │ propose_tool()  │──────────────────────────────┐    │
         └─────────────────┘                              │    │
                                                          ▼    ▼
         ┌───────────────────┐            ┌──────────────────────────────┐
         │  tools_store.json │            │        Slack Channel         │
         │                   │            │                              │
         │ u1: [{            │            │  "I noticed you've done      │
         │  tool_name,       │◄─ append   │   this 3-step sequence..."  │
         │  sequence,        │   on "yes" │                              │
         │  slot,            │            │  Running weekly_checkin...   │
         │  confirmed        │            │  ☐ calendar                  │
         │ }]                │            │  ☐ notion      ← live update │
         └───────────────────┘            │  ☐ slack                     │
                                          │                              │
         ┌───────────────────┐            │  ✅ calendar — Thu 4pm       │
         │   storage.py      │            │  ☐ notion      ← live update │
         │  (shared helpers) │            │  ☐ slack                     │
         │                   │            │                              │
         │ read/write JSON   │            │  ✅ calendar — Thu 4pm       │
         │ get_users()       │            │  ✅ notion — No open items   │
         │ get_events()      │            │  ✅ slack — Message sent     │
         │ append_tool()     │            │                              │
         │ find_matching_    │            │  ✅ weekly_checkin complete. │
         │  tool()           │            └──────────────────────────────┘
         │ slack_id_to_      │
         │  user_id()        │
         └───────────────────┘
```

---

## End-to-End Flow

### Phase 1 — Observation (Poller)

Every 15 seconds, `poller.py` asks three sources for each user's recent activity:

- **Calendar** → reads from `mock_events/calendar_uN.json` (stub for Google Calendar)
- **Notion** → real API call, filters by the user's `notion_filter` field
- **Slack** → real API call, filters messages authored by the user's `slack_id`

Each event gets normalized to `{user_id, source, time, text}` and appended to
`event_log.json`. The last-polled timestamp per source is saved in `state.json`
so it never re-reads the same event twice.

---

### Phase 2 — Pattern Detection (Detector + Ollama)

Every 15 seconds, `detect.py` runs for every user independently:

1. **`cluster_by_time()`** — pure Python, no LLM. Sorts a user's events by time
   and groups any that happen within 10 minutes of each other into a cluster.
   Noise events (isolated, far from others) fall out naturally.

2. **`interpret_cluster()`** — calls Ollama (`llama3.1:8b`) once per cluster with
   the raw events. The model decides if it looks like a deliberate multi-step
   routine and returns structured JSON: the sequence of tools used, what the
   variable part is (the *slot*), and a description.

3. **`find_repeated_pattern()`** — compares all interpreted clusters for that user.
   If 2+ clusters share the same sequence (e.g. `calendar → notion → slack`), a
   repeating pattern has been detected. User isolation is strict — u1's events
   never touch u2's analysis.

---

### Phase 3 — Proposal (Slack Bot)

When a new pattern is found and isn't already saved, `propose_tool()` DMs the
correct user directly in Slack:

> *"I noticed you've done this 3-step sequence at least twice:*
> *calendar → notion → slack.*
> *Each time for a different person.*
> *Want me to save this as `the_user_messages_someone`? Reply yes to confirm."*

The pattern is held in `pending_proposals` in memory, keyed by Slack user ID.

---

### Phase 4 — Confirmation + Memory

When the user replies **"yes"**, `confirm_tool()` writes the tool to
`tools_store.json` under that user's key. The tool now has a name, sequence,
slot type, and `confirmed: true`. It is permanent across restarts.

---

### Phase 5 — Execution (Live Checklist)

When the user types `/tool` in Slack, the bot lists their saved tools. Typing
`/tool create_meeting for Carol` (or any matching message) runs the tool:

1. Matches the message against the user's saved tools via `find_matching_tool()`
2. Extracts the slot value (`Carol`) from the text
3. Posts an initial Slack message with all steps unchecked: `☐ calendar ☐ notion ☐ slack`
4. For each step: first updates the message to show `\ step` (backslash = running),
   then calls the executor, then updates to `✅ step — result`
5. All edits happen on the **same message** via `chat.update` — no new messages
6. Final state shows all ✅ with results inline

---

## Data Files

| File | Written by | Read by | Purpose |
|---|---|---|---|
| `users.json` | manual | everyone | user → slack_id + notion_filter mapping |
| `state.json` | poller | poller | last-polled timestamps per user per source |
| `event_log.json` | poller | detect | all normalized events across all users |
| `tools_store.json` | slack_bot | slack_bot, detect | confirmed tools per user |

---

## Key Design Properties

| Property | How it's enforced |
|---|---|
| **User isolation** | Every function takes `user_id` as first arg; event reads always filter by `user_id` |
| **No hardcoded patterns** | Ollama infers routines from raw timestamped events |
| **Demo safety net** | `!poll now` in Slack triggers an immediate poll + detection pass without waiting |
| **Noise tolerance** | `cluster_by_time()` naturally ignores isolated one-off events |
| **No duplicate proposals** | `tool_already_saved()` checks before proposing |
| **No state crossing** | `pending_proposals` is keyed by `slack_user_id`; tools are keyed by `user_id` |

---

## File Ownership

| File | Owner | Notes |
|---|---|---|
| `storage.py` | Person A | shared — B imports, never rewrites |
| `users.json` | Person A | shared data contract |
| `state.json` | Person A | shared data contract |
| `event_log.json` | Person A | shared data contract |
| `tools_store.json` | Person A | shared data contract |
| `mock_events/` | Person A | calendar fallback for demo |
| `poller.py` | Person A | |
| `detect.py` | Person A | |
| `seed.py` | Person A | run once before demo |
| `executors.py` | Person B | |
| `slack_bot.py` | Person B | |
| `main.py` | Person B | |
| `test_checklist.py` | Person B | manual verification only |
| `requirements.txt` | Person B | |
| `.env.example` | Person B | |
