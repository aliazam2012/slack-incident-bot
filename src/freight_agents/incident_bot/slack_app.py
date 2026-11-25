"""The Slack app: `/incident` commands and channel messages, mapped onto the desk.

Runs under Bolt's Socket Mode handler in a real workspace (no public URL needed) and
under `App.dispatch` in tests. Slack redelivers an event it thinks was not acknowledged
in time, so each event id is handled once. The bot's own messages are ignored.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Protocol

from slack_bolt import Ack, App
from slack_sdk import WebClient


class Commands(Protocol):
    def slash(self, channel: str, user: str, text: str) -> str: ...

    def message(
        self, channel: str, user: str, text: str, ts: str, thread_ts: str | None
    ) -> None: ...


class SeenEvents:
    """Slack event ids already handled, in SQLite so a restart does not handle them again."""

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock, self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS seen (event_id TEXT PRIMARY KEY)")

    def first_time(self, event_id: str) -> bool:
        with self.lock, self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO seen VALUES (?)", (event_id,))
            return cursor.rowcount == 1


def build_slack_app(
    commands: Commands,
    client: WebClient,
    seen: SeenEvents,
    signing_secret: str = "",
) -> App:
    app = App(
        client=client,
        signing_secret=signing_secret or "unused-in-socket-mode",
        token_verification_enabled=False,
        request_verification_enabled=bool(signing_secret),
        process_before_response=True,
    )

    @app.command("/incident")
    def incident(ack: Ack, command: dict[str, Any]) -> None:
        reply = commands.slash(command["channel_id"], command["user_id"], command.get("text", ""))
        ack(reply)

    @app.event("message")
    def message(body: dict[str, Any], event: dict[str, Any]) -> None:
        if event.get("bot_id") or event.get("subtype"):
            return
        if not str(event.get("text", "")).strip():
            return
        if not seen.first_time(str(body.get("event_id", ""))):
            return
        commands.message(
            event["channel"],
            event.get("user", ""),
            event["text"],
            event["ts"],
            event.get("thread_ts"),
        )

    return app


def run_socket_mode(app: App, app_token: str) -> None:
    """A real workspace: Socket Mode with an app-level token from the environment."""
    from slack_bolt.adapter.socket_mode import SocketModeHandler

    # slack_bolt ships this method without annotations.
    SocketModeHandler(app, app_token).start()  # type: ignore[no-untyped-call]
