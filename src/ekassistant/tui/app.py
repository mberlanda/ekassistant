"""Terminal client (REPL).

See docs/design/client-tui.md. Mock identity resolution (`:user <id>` to
switch, try `alice`, `bob`, `carol`, or `guest` - see
config/identities.yaml) and question answering via POST /query, per
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

            _ask(client, user_id, line)


def _ask(client: httpx.Client, user_id: str, question: str) -> None:
    try:
        # Longer timeout than the client default: local CPU model
        # inference (see ADR-0004) is far slower than a health/whoami
        # round trip, especially on a cold-started Ollama model.
        response = client.post(
            "/query",
            json={"question": question},
            headers={"X-User-Id": user_id},
            timeout=120.0,
        )
        response.raise_for_status()
    except httpx.HTTPError as error:
        console.print(f"[red]Request to the API Gateway failed:[/red] {error}")
        return

    result = response.json()
    if result["abstained"]:
        console.print("[yellow]No grounded answer found in the accessible documents.[/yellow]")
        return

    console.print(result["answer"])
    for citation in result["citations"]:
        console.print(f"  [dim]- {citation['source']} ({citation['chunk_id']})[/dim]")


if __name__ == "__main__":
    run()
