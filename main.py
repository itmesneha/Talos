import threading
import time
from agent_graph import run_for_user
from storage import get_users
from slack_bot import start_bot, setup_loop


def _run_user_safe(user_id: str) -> None:
    """Wrapper so per-user errors don't cancel other users' threads."""
    try:
        run_for_user(user_id)
    except Exception as e:
        print(f"[agent_loop] {user_id} error: {e}")


def agent_loop(interval_seconds: int = 15) -> None:
    """
    Each cycle spawns one thread per user so users run fully in parallel.
    A pending proposal (or slow Ollama call) for one user never blocks others.
    Waits for all per-user threads to finish before sleeping until the next cycle.
    """
    while True:
        users = get_users()
        user_threads = [
            threading.Thread(
                target=_run_user_safe,
                args=(user_id,),
                daemon=True,
                name=f"agent-{user_id}",
            )
            for user_id in users
        ]
        for t in user_threads:
            t.start()
        for t in user_threads:
            t.join()
        time.sleep(interval_seconds)


def main():
    threads = [
        threading.Thread(
            target=agent_loop,
            kwargs={"interval_seconds": 15},
            daemon=True,
            name="agent",
        ),
        threading.Thread(
            target=setup_loop,
            kwargs={"interval_seconds": 3600},
            daemon=True,
            name="setup-checker",
        ),
    ]

    for t in threads:
        t.start()
        print(f"[main] started {t.name}")

    print("[main] starting Slack bot...")
    start_bot()  # blocking — keeps process alive


if __name__ == "__main__":
    main()
