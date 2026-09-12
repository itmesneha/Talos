"""
LangGraph graph wiring for Tool-Synthesis.

Imports all nodes from nodes.py, wires them into a StateGraph,
compiles with MemorySaver, and exposes the public API used by
main.py (agent_loop) and slack_bot.py (resume_for_user).

Graph topology:
    poll → cluster → llm → pattern
                              ↓ no pattern → END
                              ↓ pattern found
                           propose → human [INTERRUPT]
                                        ↓ resume with Slack reply
                                     confirm
                                        ↓ discarded → END
                                        ↓ confirmed
                                     executor → END

Public API:
    run_for_user(user_id)              — start one detect-and-propose cycle
    resume_for_user(slack_id, text, say) — resume after user replies in Slack
    has_pending_proposal(slack_id)     — True if user has unanswered proposal
"""

import threading
import time

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from storage import get_users
from nodes import (
    AgentState,
    poll_node,
    cluster_node,
    llm_node,
    pattern_node,
    propose_node,
    human_node,
    confirm_node,
    re_propose_node,
    executor_node,
    execute_node,
)


# ─────────────────────────────────────────────────────────────────────────────
# Routing
# ─────────────────────────────────────────────────────────────────────────────

def _route_after_pattern(state: AgentState) -> str:
    """Go to propose_node if a new pattern was found, otherwise end the run."""
    return "propose" if state.get("pattern") else END


def _route_after_confirm(state: AgentState) -> str:
    """
    Three outcomes:
      confirmed=True          → executor (generate custom executor code)
      needs_reconfirmation    → re_propose (show updated proposal, loop back)
      otherwise               → END (discarded)
    """
    p = state.get("pattern") or {}
    if p.get("confirmed"):
        return "executor"
    if state.get("needs_reconfirmation"):
        return "re_propose"
    return END


# ─────────────────────────────────────────────────────────────────────────────
# Graph construction
# ─────────────────────────────────────────────────────────────────────────────

def _build_graph():
    g = StateGraph(AgentState)

    # Register nodes
    g.add_node("poll",       poll_node)
    g.add_node("cluster",    cluster_node)
    g.add_node("llm",        llm_node)
    g.add_node("pattern",    pattern_node)
    g.add_node("propose",    propose_node)
    g.add_node("human",      human_node)
    g.add_node("confirm",    confirm_node)
    g.add_node("re_propose", re_propose_node)
    g.add_node("executor",   executor_node)

    # Wire edges
    g.set_entry_point("poll")
    g.add_edge("poll",       "cluster")
    g.add_edge("cluster",    "llm")
    g.add_edge("llm",        "pattern")
    g.add_conditional_edges(
        "pattern",
        _route_after_pattern,
        {"propose": "propose", END: END},
    )
    g.add_edge("propose",    "human")
    g.add_edge("human",      "confirm")
    g.add_conditional_edges(
        "confirm",
        _route_after_confirm,
        {"executor": "executor", "re_propose": "re_propose", END: END},
    )
    g.add_edge("re_propose", "human")   # loop: re_propose → human → confirm → …
    g.add_edge("executor",   END)

    return g.compile(checkpointer=MemorySaver())


# Compiled singleton — shared across all invocations
agent_graph = _build_graph()


def _build_execute_graph():
    """Single-node graph for running a saved tool with a live Slack checklist."""
    g = StateGraph(AgentState)
    g.add_node("execute", execute_node)
    g.set_entry_point("execute")
    g.add_edge("execute", END)
    return g.compile()   # no checkpointer needed — no interrupts


execute_graph = _build_execute_graph()

# Maps slack_id → (LangGraph thread_id, created_at timestamp)
_pending_threads: dict[str, tuple[str, float]] = {}
_PROPOSAL_TTL_SECONDS = 120   # 2 minutes


def _expire_old_proposals() -> None:
    """Background thread: discard proposals that have been pending > 2 minutes."""
    while True:
        time.sleep(10)  # check every 10 seconds
        now = time.time()
        expired = [
            slack_id
            for slack_id, (_, created_at) in list(_pending_threads.items())
            if now - created_at > _PROPOSAL_TTL_SECONDS
        ]
        for slack_id in expired:
            entry = _pending_threads.pop(slack_id, None)
            if entry:
                thread_id, _ = entry
                print(f"[graph] proposal for {slack_id} expired after {_PROPOSAL_TTL_SECONDS}s (thread={thread_id})")


_expire_thread = threading.Thread(target=_expire_old_proposals, daemon=True, name="proposal-expiry")
_expire_thread.start()


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def run_for_user(user_id: str) -> None:
    """
    Start one detect-and-propose cycle for the given user.
    The graph runs poll → cluster → llm → pattern, then either ends (no pattern)
    or continues to propose → human (interrupt) awaiting a Slack reply.
    """
    users = get_users()
    if user_id not in users:
        print(f"[graph] unknown user: {user_id}")
        return

    slack_id = users[user_id]["slack_id"]

    # Don't start a new run while a proposal is already waiting
    if slack_id in _pending_threads:
        print(f"[graph] {user_id} already has a pending proposal — skipping")
        return

    thread_id = f"agent-{user_id}-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    print(f"[graph] starting run for {user_id} (thread={thread_id})")
    agent_graph.invoke(
        {
            "user_id": user_id,
            "events": [],
            "clusters": [],
            "interpreted_clusters": [],
            "pattern": None,
            "tool_definition": None,
            "dm_channel": None,
            "user_response": None,
        },
        config=config,
    )

    # If the graph paused at human_node, register the thread so we can resume it
    snapshot = agent_graph.get_state(config)
    if snapshot.next:
        _pending_threads[slack_id] = (thread_id, time.time())
        print(f"[graph] proposal pending for {user_id} ({slack_id}), thread={thread_id}")


def resume_for_user(slack_id: str, user_text: str, say) -> bool:
    """
    Resume a paused graph with the user's Slack reply.

    Returns True  — pending thread existed (reply handled or re-prompted).
    Returns False — no pending proposal for this user.

    Behaviour:
        - "yes"       → confirm_node saves the tool
        - "no"        → confirm_node discards
        - anything else → treated as a change request; re_propose_node applies
                          the change and re-sends the proposal (loops until yes/no)
    """
    entry = _pending_threads.get(slack_id)
    if not entry:
        return False
    thread_id, _ = entry

    config = {"configurable": {"thread_id": thread_id}}
    agent_graph.invoke(Command(resume=user_text), config=config)

    # Check if the graph paused again (change request → re_propose → human loop)
    snapshot = agent_graph.get_state(config)
    if snapshot.next:
        # Still interrupted — updated proposal DM already sent by re_propose_node
        # Refresh the TTL so the user gets another 2 minutes to respond
        _pending_threads[slack_id] = (thread_id, time.time())
        return True  # keep thread registered

    # Graph completed
    _pending_threads.pop(slack_id, None)
    final_state = agent_graph.get_state(config).values
    pattern = final_state.get("pattern") if isinstance(final_state, dict) else None

    if "yes" in lower:
        if pattern and pattern.get("confirmed"):
            tool_name = pattern["tool_name"]
            example = pattern.get("example") or f"/tool {tool_name} for [name]"
            say(
                f"✅ *Saved `{tool_name}`!*\n"
                f"_{pattern.get('description', '')}_\n\n"
                f"Run it anytime with:\n`{example}`"
            )
        else:
            say("Something went wrong while saving. Try again with `!poll now`.")
    else:
        say("Discarded. I'll let you know if I spot another pattern.")

    return True


def has_pending_proposal(slack_id: str) -> bool:
    """True if the user has an unanswered tool proposal."""
    return slack_id in _pending_threads


def run_tool_graph(user_id: str, tool: dict, args: dict, channel: str) -> None:
    """
    Invoke the execute_graph for a saved tool.
    Called from the /tool slash-command handler in slack_bot.py.
    """
    thread_id = f"exec-{user_id}-{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}
    print(f"[graph] executing '{tool['tool_name']}' args={args} (thread={thread_id})")
    execute_graph.invoke(
        {
            "user_id": user_id,
            "pattern": tool,
            "args":    args,
            "channel": channel,
        },
        config=config,
    )
