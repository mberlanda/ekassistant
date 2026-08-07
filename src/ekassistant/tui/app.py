"""Terminal client (REPL).

See docs/design/client-tui.md. This first version exercises the API
Gateway's mock identity resolution end to end (`:user <id>` to switch,
try `alice`, `bob`, `carol`, or `guest` - see config/identities.yaml).
Question answering lands once retrieval + the model layer are wired, per
docs/design/orchestration.md.
"""

import httpx
from rich.console import Console

from ekassistant.config.settings import get_settings

console = Console()


def run() -> None:
    settings = get_settings()
    base_url = "http://127.0.0.1:8000"
    user_id = settings.default_user

    console.print("[bold]Enterprise Knowledge Assistant[/bold] (TUI)")
    console.print(f"Talking to API Gateway at {base_url}. Type :quit to exit.")
    console.print(f"Current mock user: [cyan]{user_id}[/cyan] (:user <id> to switch)\n")

    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        while True:
            try:
                line = console.input("[green]> [/green]").strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not line:
                continue
            if line in (":quit", ":q"):
                break
            if line.startswith(":user "):
                user_id = line.removeprefix(":user ").strip()
                resp = client.get("/whoami", headers={"X-User-Id": user_id})
                groups = resp.json()["groups"]
                console.print(f"Now [cyan]{user_id}[/cyan], groups: {groups or '(none)'}")
                continue

            console.print(
                "[yellow]Question answering isn't wired up yet[/yellow] - "
                "retrieval and the model layer land in a later commit. "
                "See docs/design/orchestration.md for the plan."
            )


if __name__ == "__main__":
    run()
