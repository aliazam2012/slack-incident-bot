"""freight-incidents: play a demo incident, serve the Slack bot, or serve read tools over MCP."""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Protocol

from freight_agents.incident_bot.chat import FakeChat
from freight_agents.incident_bot.desk import STATUS_CHANNEL, IncidentDesk, Stores
from freight_agents.llm import Model

DEFAULT_STATE = Path("var/incidents")
log = logging.getLogger(__name__)


class Ticks(Protocol):
    def tick(self) -> list[str]: ...


def tick_forever(desk: Ticks, stop: threading.Event, every: float = 30.0) -> None:
    """Fire reminders until stopped; a failed tick is logged and the next runs on time."""
    while not stop.is_set():
        try:
            desk.tick()
        except Exception:
            log.exception("reminder tick failed; trying again in %s seconds", every)
        stop.wait(every)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="freight-incidents")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="play one incident against an in-memory Slack")
    commands.add_parser("serve", help="run the bot in a Slack workspace (Socket Mode)")
    commands.add_parser("mcp", help="serve the read tools over MCP on stdio")
    evaluate = commands.add_parser("eval", help="play every scenario and grade it")
    evaluate.add_argument("--reports", type=Path, default=Path("apps/incident-bot/evals/reports"))
    evaluate.add_argument("--day", default=dt.date.today().isoformat())
    evaluate.add_argument(
        "--mode", choices=["reference", "recorded", "record"], default="reference"
    )
    evaluate.add_argument(
        "--cassettes", type=Path, default=Path("apps/incident-bot/evals/cassettes")
    )
    evaluate.add_argument("--model-name", default="claude-opus-5-5")
    evaluate.add_argument("--max-usd", type=float, default=0.0, help="record: the approved cap")
    evaluate.add_argument("--limit", type=int, help="record: only the first N scenarios")
    args = parser.parse_args(argv)
    if args.command == "eval":
        from freight_agents.evals.recording import StaleCassette
        from freight_agents.incident_bot.evaluation import (
            FLOORS,
            record,
            run,
            run_recorded,
            write_report,
        )

        if args.mode == "record":
            from freight_agents.llm.anthropic_adapter import AnthropicModel

            def live(case_id: str) -> Model:
                return AnthropicModel(model=args.model_name, effort="low")

            return record(args.cassettes, live, args.max_usd, args.limit).code
        try:
            result = (
                run() if args.mode == "reference" else run_recorded(args.cassettes, args.model_name)
            )
        except StaleCassette as exc:
            print(f"FAIL {exc}")
            return 2
        model = None if args.mode == "reference" else args.model_name
        path = write_report(result, args.reports, args.day, args.mode, model)
        for name, value in result.metrics.items():
            print(f"{name}: {value:.2f}")
        failures = result.failures(FLOORS)
        print(f"gate: {'passed' if not failures else 'failed: ' + '; '.join(failures)}")
        print(f"report: {path}")
        return 1 if failures else 0
    if args.command == "demo":
        return _demo(args.state)
    if args.command == "serve":
        return _serve(args.state)
    from freight_agents.incident_bot.mcp_server import build_incident_server

    desk = IncidentDesk(Stores.at(args.state), FakeChat())
    return _stdio(build_incident_server(desk))


class _Clock:
    def __init__(self) -> None:
        self.now = dt.datetime(2026, 11, 24, 9, 0, tzinfo=dt.UTC)

    def __call__(self) -> dt.datetime:
        return self.now


def _demo(state: Path) -> int:
    clock, chat = _Clock(), FakeChat()
    desk = IncidentDesk(Stores.at(state / "demo", clock), chat, clock=clock)
    say = chat.say

    def step(minutes: int, label: str) -> None:
        clock.now += dt.timedelta(minutes=minutes)
        print(f"\n-- {clock.now:%H:%M} {label}")

    step(0, "dana declares from #general")
    print(desk.slash("C-general", "U-dana", "declare SEV-2 TMS order API failing +customer"))
    channel = chat.posts[-1].channel
    step(3, "the team talks in the incident channel")
    for user, text in [
        ("U-lee", "timeouts on POST /orders since 08:55"),
        ("U-sam", "deploy 412 went out at 08:52"),
    ]:
        message = say(channel, user, text)
        desk.message(channel, user, text, message.ts, None)
    print(desk.slash(channel, "U-dana", "role operations <@U-lee>"))
    print(desk.slash(channel, "U-dana", "runbook TMS order API timeouts"))
    step(18, "the customer update falls due")
    desk.tick()
    (pending,) = desk.stores.approvals.pending()
    print(f"approval waiting: {pending.id}: {pending.args['text']}")
    desk.approve(pending.id, "U-comms")
    step(10, "a reminder falls due before anyone posts status")
    desk.tick()
    print(desk.slash(channel, "U-dana", "status identified deploy 412 rolled back"))
    step(25, "recovered")
    print(desk.slash(channel, "U-dana", "resolve error rate back to normal"))
    print(desk.slash(channel, "U-dana", "postmortem"))
    print("\n== channel transcript")
    for post in chat.posts:
        where = "C-customer-status" if post.channel == STATUS_CHANNEL else post.channel
        print(f"[{where}] {post.text.splitlines()[0]}")
    return 0


def _serve(state: Path) -> int:
    bot, app_token = os.environ.get("SLACK_BOT_TOKEN"), os.environ.get("SLACK_APP_TOKEN")
    if not bot or not app_token:
        print(
            "Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN; see apps/incident-bot/README.md.",
            file=sys.stderr,
        )
        return 2
    from freight_agents.incident_bot.chat import SlackChat
    from freight_agents.incident_bot.slack_app import build_slack_app, run_socket_mode
    from slack_sdk import WebClient

    client = WebClient(token=bot)
    stores = Stores.at(state)
    desk = IncidentDesk(
        stores,
        SlackChat(client),
        status_channel=os.environ.get("INCIDENT_STATUS_CHANNEL", STATUS_CHANNEL),
    )

    threading.Thread(target=tick_forever, args=(desk, threading.Event()), daemon=True).start()
    run_socket_mode(build_slack_app(desk, client=client, seen=stores.seen), app_token)
    return 0


def _stdio(server: object) -> int:
    import anyio
    from mcp.server.stdio import stdio_server

    async def serve() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())  # type: ignore[attr-defined]

    anyio.run(serve)
    return 0


if __name__ == "__main__":
    sys.exit(main())
