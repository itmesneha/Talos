# Deprecated — use tools.py instead.
from tools import (
    check_calendar,
    check_notion,
    send_calendar_invite as create_calendar_event,
    write_notion_page   as add_notion_page,
    send_slack_message  as post_slack,
    get_tool_map        as get_executor_map,
    TOOL_MAP            as EXECUTOR_MAP,
)

def draft_message(person: str, calendar_result: str, notion_result: str) -> str:
    return (
        f"Hi {person}, just checking in ahead of our meeting. "
        f"{calendar_result}. "
        f"On the task front: {notion_result}. Let me know if anything needs attention!"
    )
