import json
from pathlib import Path
from typing import Any

import anyio
import pytest
from freight_agents.incident_bot.cli import main
from freight_agents.incident_bot.mcp_server import build_incident_server
from mcp import Client

from .conftest import World


def _call(server: Any, name: str, args: dict[str, Any]) -> tuple[bool, Any]:
    async def go() -> tuple[bool, Any]:
        async with Client(server) as client:
            result = await client.call_tool(name, args)
            return bool(result.is_error), json.loads(result.content[0].text)

    return anyio.run(go)


def _names(server: Any) -> set[str]:
    async def go() -> set[str]:
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    return anyio.run(go)


def test_mcp_reads_incidents_timelines_approvals_and_runbooks(tmp_path: Path) -> None:
    world = World(tmp_path)
    channel = world.declare()
    world.desk.slash(channel, "U-dana", "status identified bad deploy")
    server = build_incident_server(world.desk)
    assert _names(server) == {
        "get_incident",
        "incident_timeline",
        "list_pending_approvals",
        "search_runbooks",
    }
    error, got = _call(server, "get_incident", {"case_id": "inc-1"})
    assert not error
    assert got["severity"] == "SEV-2"
    error, timeline = _call(server, "incident_timeline", {"case_id": "inc-1"})
    assert [e["kind"] for e in timeline["entries"]] == ["declared", "status"]
    error, hits = _call(server, "search_runbooks", {"query": "EDI feed down"})
    assert hits["passages"]
    error, _ = _call(server, "get_incident", {"case_id": "inc-9"})
    assert error


def test_the_demo_plays_an_incident_without_slack(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["--state", str(tmp_path), "demo"]) == 0
    out = capsys.readouterr().out
    assert "SEV-2 declared" in out
    assert "reminder" in out
    assert "C-customer-status" in out
    assert "Postmortem drafted" in out


def test_serve_needs_tokens_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    assert main(["--state", str(tmp_path), "serve"]) == 2


def test_the_reminder_loop_outlives_a_failed_tick(caplog: pytest.LogCaptureFixture) -> None:
    import threading

    from freight_agents.incident_bot.cli import tick_forever

    stop = threading.Event()
    calls: list[int] = []

    class Desk:
        def tick(self) -> list[str]:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("database is locked")
            stop.set()
            return []

    tick_forever(Desk(), stop, every=0)
    assert len(calls) == 2
    assert "reminder tick failed" in caplog.text
