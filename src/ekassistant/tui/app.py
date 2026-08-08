"""Terminal client (REPL).

See docs/design/client-tui.md. Mock identity resolution (`:user <id>` to
switch, try `alice`, `bob`, `carol`, or `guest` - see
config/identities.yaml), generation temperature (`:temp <value>` to
switch - see models/generation.py's DEFAULT_TEMPERATURE docstring for why
this is exposed as an experimentation knob rather than tuned to a
"better" default: repeated live testing found no fixed temperature
reliably reduces the small local model's cite-or-abstain flakiness, so
letting a caller try different values directly is more honest than
guessing one), and question answering via POST /query, per
docs/design/orchestration.md.
"""

import httpx
from rich.console import Console

from ekassistant.config.settings import get_settings

console = Console()

# Ollama accepts any non-negative temperature; this project bounds it to
# the same [0.0, 2.0] range the API Gateway validates server-side (see
# QueryRequest.temperature) so a typo like ":temp 20" fails loudly in the
# TUI itself, not as an unexplained 422 from the API a beat later.
_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 2.0


class InvalidTemperature(ValueError):
    pass


def _parse_temperature(raw: str) -> float:
    raw = raw.strip()
    try:
        value = float(raw)
    except ValueError:
        raise InvalidTemperature(f"{raw!r} is not a number") from None
    if not (_MIN_TEMPERATURE <= value <= _MAX_TEMPERATURE):
        raise InvalidTemperature(f"{value} is outside [{_MIN_TEMPERATURE}, {_MAX_TEMPERATURE}]")
    return value


def run() -> None:
    settings = get_settings()
    base_url = "http://127.0.0.1:8000"
    user_id = settings.default_user
    temperature = settings.ollama_temperature

    console.print("[bold]Enterprise Knowledge Assistant[/bold] (TUI)")
    console.print(f"Talking to API Gateway at {base_url}. Type :quit to exit.")
    console.print(f"Current mock user: [cyan]{user_id}[/cyan] (:user <id> to switch)")
    console.print(f"Current temperature: [cyan]{temperature}[/cyan] (:temp <0.0-2.0> to switch)\n")

    with httpx.Client(base_url=base_url, timeout=5.0) as client:
        while True:
            # One try/except around the whole turn, not just console.input():
            # _ask()'s /query call can run for up to 120s (see below), and
            # Ctrl+C during that wait must exit the REPL cleanly too, the
            # same as Ctrl+C/Ctrl+D at the prompt itself - not crash with a
            # traceback partway through answering.
            try:
                line = console.input("[green]> [/green]").strip()

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
                if line == ":temp" or line.startswith(":temp "):
                    raw = line.removeprefix(":temp").strip()
                    if not raw:
                        console.print(f"Current temperature: [cyan]{temperature}[/cyan]")
                        continue
                    try:
                        temperature = _parse_temperature(raw)
                    except InvalidTemperature as error:
                        console.print(f"[red]Invalid temperature:[/red] {error}")
                        continue
                    console.print(f"Now using temperature [cyan]{temperature}[/cyan]")
                    continue

                _ask(client, user_id, temperature, line)
            except (EOFError, KeyboardInterrupt):
                break


def _ask(client: httpx.Client, user_id: str, temperature: float, question: str) -> None:
    try:
        # Longer timeout than the client default: local CPU model
        # inference (see ADR-0004) is far slower than a health/whoami
        # round trip, especially on a cold-started Ollama model.
        response = client.post(
            "/query",
            json={"question": question, "temperature": temperature},
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
    # The model's own, unvalidated self-assessment - see
    # GroundedAnswer.confidence's docstring for why it's optional (the
    # model may omit it even on a real answer) and shown as-is, never
    # recalibrated.
    if result["confidence"] is not None:
        console.print(f"  [dim]confidence: {result['confidence']:.2f}[/dim]")


if __name__ == "__main__":
    run()
