import json
from typing import Any
from urllib.parse import urlencode

import pytest
from slack_bolt import BoltRequest
from slack_sdk import WebClient

from freight_agents.incident_bot.chat import FakeChat, SlackChat
from freight_agents.incident_bot.slack_app import SeenEvents, build_slack_app

from .conftest import SlackStub


def chats(slack: tuple[SlackStub, str]) -> list[Any]:
    return [FakeChat(), SlackChat(WebClient(token="xoxb-test", base_url=slack[1]))]


def test_the_fake_and_the_slack_adapter_behave_alike(slack: tuple[SlackStub, str]) -> None:
    for chat in chats(slack):
        channel = chat.create_channel("inc-1-tms-orders")
        first = chat.post(channel, "SEV-2 declared")
        reply = chat.post(channel, "commander: dana", thread_ts=first)
        assert float(reply) > float(first)
        texts = [m.text for m in chat.history(channel)]
        assert texts == ["SEV-2 declared", "commander: dana"], type(chat).__name__
        assert chat.history(channel)[1].thread_ts == first


def test_the_fake_records_every_post_with_its_channel() -> None:
    chat = FakeChat()
    chat.post("C1", "one")
    chat.post("C2", "two")
    assert [(p.channel, p.text) for p in chat.posts] == [("C1", "one"), ("C2", "two")]


class Recorder:
    def __init__(self) -> None:
        self.slashes: list[tuple[str, str, str]] = []
        self.messages: list[tuple[str, str, str]] = []

    def slash(self, channel: str, user: str, text: str) -> str:
        self.slashes.append((channel, user, text))
        return "declared"

    def message(self, channel: str, user: str, text: str, ts: str, thread_ts: str | None) -> None:
        self.messages.append((channel, user, text))


def _app(slack: tuple[SlackStub, str], recorder: Recorder) -> Any:
    client = WebClient(token="xoxb-test", base_url=slack[1])
    return build_slack_app(recorder, client=client, seen=SeenEvents())


def test_the_incident_command_reaches_the_desk(slack: tuple[SlackStub, str]) -> None:
    recorder = Recorder()
    app = _app(slack, recorder)
    body = urlencode(
        {
            "command": "/incident",
            "text": "declare SEV-2 TMS orders failing",
            "user_id": "U1",
            "channel_id": "C1",
            "team_id": "T1",
            "response_url": "https://hooks.example.com/x",
            "trigger_id": "t",
        }
    )
    response = app.dispatch(
        BoltRequest(body=body, headers={"content-type": ["application/x-www-form-urlencoded"]})
    )
    assert response.status == 200
    assert recorder.slashes == [("C1", "U1", "declare SEV-2 TMS orders failing")]
    assert "declared" in response.body


def _event(event_id: str, **event: Any) -> BoltRequest:
    body = {
        "type": "event_callback",
        "team_id": "T1",
        "api_app_id": "A1",
        "event_id": event_id,
        "event_time": 1,
        "event": {"type": "message", "channel": "C1", "ts": "5.000100", **event},
    }
    return BoltRequest(body=json.dumps(body), headers={"content-type": ["application/json"]})


def test_a_redelivered_event_is_handled_once(slack: tuple[SlackStub, str]) -> None:
    recorder = Recorder()
    app = _app(slack, recorder)
    for _ in range(2):
        app.dispatch(_event("Ev1", user="U2", text="TMS errors started at 09:02"))
    assert recorder.messages == [("C1", "U2", "TMS errors started at 09:02")]


def test_the_bots_own_messages_are_ignored(slack: tuple[SlackStub, str]) -> None:
    recorder = Recorder()
    app = _app(slack, recorder)
    app.dispatch(_event("Ev2", bot_id="BBOT", text="Reminder: post a status update"))
    assert recorder.messages == []


@pytest.mark.parametrize("text", ["", "   "])
def test_empty_messages_are_ignored(slack: tuple[SlackStub, str], text: str) -> None:
    recorder = Recorder()
    app = _app(slack, recorder)
    app.dispatch(_event("Ev3", user="U2", text=text))
    assert recorder.messages == []
