"""The chat port: what the bot needs from Slack, and an in-memory fake that behaves the same.

The fake runs every test and the evaluation, so CI needs no workspace. The Slack adapter
uses slack_sdk's Web API client; a contract test runs the same checks against both, the
adapter pointed at a local stub of the Slack Web API.
"""

from __future__ import annotations

import itertools
from typing import Protocol

from pydantic import BaseModel
from slack_sdk import WebClient


class ChatMessage(BaseModel):
    channel: str
    ts: str
    user: str
    text: str
    thread_ts: str | None


class ChatPort(Protocol):
    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str: ...

    def history(self, channel: str) -> list[ChatMessage]: ...

    def create_channel(self, name: str) -> str: ...


class FakeChat:
    """Slack in memory: channels, messages in order, and a log of every post the bot made."""

    def __init__(self, bot_user: str = "UBOT") -> None:
        self.bot_user = bot_user
        self.channels: dict[str, str] = {}
        self.messages: dict[str, list[ChatMessage]] = {}
        self.posts: list[ChatMessage] = []
        self._clock = itertools.count(1)

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        message = ChatMessage(
            channel=channel,
            ts=f"{next(self._clock)}.000100",
            user=self.bot_user,
            text=text,
            thread_ts=thread_ts,
        )
        self.messages.setdefault(channel, []).append(message)
        self.posts.append(message)
        return message.ts

    def say(self, channel: str, user: str, text: str, thread_ts: str | None = None) -> ChatMessage:
        """A person writes in a channel (tests and the evaluation)."""
        message = ChatMessage(
            channel=channel,
            ts=f"{next(self._clock)}.000100",
            user=user,
            text=text,
            thread_ts=thread_ts,
        )
        self.messages.setdefault(channel, []).append(message)
        return message

    def history(self, channel: str) -> list[ChatMessage]:
        return list(self.messages.get(channel, []))

    def create_channel(self, name: str) -> str:
        channel_id = f"C{len(self.channels) + 100}"
        self.channels[channel_id] = name
        return channel_id


class SlackChat:
    def __init__(self, client: WebClient) -> None:
        self.client = client

    def post(self, channel: str, text: str, thread_ts: str | None = None) -> str:
        response = self.client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        return str(response["ts"])

    def history(self, channel: str) -> list[ChatMessage]:
        response = self.client.conversations_history(channel=channel)
        rows = list(reversed(response["messages"]))  # Slack returns newest first
        return [
            ChatMessage(
                channel=channel,
                ts=r["ts"],
                user=r.get("user", ""),
                text=r["text"],
                thread_ts=r.get("thread_ts"),
            )
            for r in rows
        ]

    def create_channel(self, name: str) -> str:
        response = self.client.conversations_create(name=name)
        return str(response["channel"]["id"])
