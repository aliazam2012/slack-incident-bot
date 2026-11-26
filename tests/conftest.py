"""A stub of the Slack Web API, served over HTTP so slack_sdk talks to it as to Slack."""

import datetime as dt
import itertools
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request

from freight_agents.incident_bot.chat import FakeChat
from freight_agents.incident_bot.desk import IncidentDesk, Stores


class SlackStub:
    def __init__(self) -> None:
        self.app = FastAPI()
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.channels: dict[str, str] = {}
        self.calls: list[str] = []
        clock = itertools.count(1)

        async def form(request: Request) -> dict[str, Any]:
            if request.headers.get("content-type", "").startswith("application/json"):
                return dict(json.loads(await request.body() or b"{}"))
            return dict((await request.form()).items())

        @self.app.post("/api/{method}")
        async def call(method: str, request: Request) -> dict[str, Any]:
            body = await form(request)
            self.calls.append(method)
            if method == "auth.test":
                return {"ok": True, "user_id": "UBOT", "bot_id": "BBOT", "team_id": "T1"}
            if method == "chat.postMessage":
                ts = f"{next(clock)}.000100"
                message = {
                    "ts": ts,
                    "user": "UBOT",
                    "text": body["text"],
                    "thread_ts": body.get("thread_ts"),
                }
                self.messages.setdefault(str(body["channel"]), []).append(message)
                return {"ok": True, "channel": body["channel"], "ts": ts}
            if method == "conversations.history":
                return {
                    "ok": True,
                    "messages": list(reversed(self.messages.get(str(body["channel"]), []))),
                }
            if method == "conversations.create":
                channel_id = f"C{len(self.channels) + 100}"
                self.channels[channel_id] = str(body["name"])
                return {"ok": True, "channel": {"id": channel_id, "name": body["name"]}}
            return {"ok": False, "error": "unknown_method"}


@pytest.fixture
def slack() -> Iterator[tuple[SlackStub, str]]:
    stub = SlackStub()
    server = uvicorn.Server(uvicorn.Config(stub.app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the Slack stub did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield stub, f"http://127.0.0.1:{port}/api/"
    server.should_exit = True
    thread.join(timeout=5)


T0 = dt.datetime(2026, 11, 24, 9, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> dt.datetime:
        return self.now

    def advance(self, minutes: int) -> None:
        self.now += dt.timedelta(minutes=minutes)


class World:
    def __init__(self, root: Path) -> None:
        self.clock = Clock()
        self.chat = FakeChat()
        self.stores = Stores.at(root, self.clock)
        self.desk = IncidentDesk(self.stores, self.chat, clock=self.clock)

    def declare(self, text: str = "declare SEV-2 TMS orders failing +customer") -> str:
        reply = self.desk.slash("C-general", "U-dana", text)
        assert reply.startswith("Declared")
        return self.chat.posts[-1].channel

    def posts_in(self, channel: str) -> list[str]:
        return [p.text for p in self.chat.posts if p.channel == channel]

    def outside(self, channel: str) -> list[Any]:
        return [p for p in self.chat.posts if p.channel != channel]


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)
