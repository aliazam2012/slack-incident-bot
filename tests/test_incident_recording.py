import tempfile
from pathlib import Path

import pytest
from freight_agents.evals.recording import StaleCassette
from freight_agents.incident_bot.domain import Incident
from freight_agents.incident_bot.drafts import Kind, reference_model
from freight_agents.incident_bot.evaluation import (
    FLOORS,
    model_read_scenarios,
    play,
    record,
    run_recorded,
    write_report,
)
from freight_agents.llm import Message, Model, ModelTurn, ScriptedModel, ToolSpec, Usage


class Capturing:
    def __init__(self, inner: Model, sink: list[ModelTurn]) -> None:
        self._inner, self._sink = inner, sink

    @property
    def name(self) -> str:
        return self._inner.name

    def complete(self, system: str, messages: list[Message], tools: list[ToolSpec]) -> ModelTurn:
        turn = self._inner.complete(system, messages, tools)
        self._sink.append(turn)
        return turn


def live_reference(case_id: str) -> Model:
    """What the reference drafts answer in this scenario, as one priced model."""
    (s,) = [x for x in model_read_scenarios() if x.id == case_id]
    turns: list[ModelTurn] = []

    def drafts(kind: Kind, incident: Incident) -> Model:
        return Capturing(reference_model(kind, incident), turns)

    with tempfile.TemporaryDirectory() as tmp:
        play(s, Path(tmp), live=drafts)
    usage = Usage(input_tokens=1_500, output_tokens=250)
    priced = [t.model_copy(update={"model": "claude-opus-5-5", "usage": usage}) for t in turns]
    return ScriptedModel(priced, name="claude-opus-5-5")


@pytest.fixture(scope="module")
def cassettes(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("cassettes")
    assert record(out, live_reference, cap=20.0).code == 0
    return out


def test_every_scenario_but_the_red_team_is_recorded(cassettes: Path) -> None:
    ids = {s.id for s in model_read_scenarios()}
    assert len(ids) == 14
    assert {p.stem for p in cassettes.glob("*.jsonl")} == ids
    assert any(p.read_text() for p in cassettes.glob("*.jsonl"))


def test_recorded_mode_replays_through_the_same_gate(cassettes: Path) -> None:
    result = run_recorded(cassettes, "claude-opus-5-5")
    assert result.failures(FLOORS) == []


def test_a_missing_cassette_fails_recorded_mode_naming_the_scenario(
    cassettes: Path, tmp_path: Path
) -> None:
    drafted = [p for p in sorted(cassettes.glob("*.jsonl")) if p.read_text()]
    victim = drafted[0]
    for path in cassettes.glob("*.jsonl"):
        if path != victim:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with pytest.raises(StaleCassette, match=victim.stem):
        run_recorded(tmp_path, "claude-opus-5-5")


def test_a_recorded_report_names_the_model(cassettes: Path, tmp_path: Path) -> None:
    result = run_recorded(cassettes, "claude-opus-5-5")
    path = write_report(result, tmp_path, "2026-11-25", mode="recorded", model="claude-opus-5-5")
    assert path.name == "2026-11-25-recorded.md"
    assert "claude-opus-5-5" in path.read_text()


def test_the_eval_command_refuses_to_record_without_a_cap(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    from freight_agents.incident_bot.cli import main

    assert main(["eval", "--mode", "record", "--cassettes", str(tmp_path)]) == 2
    assert "--max-usd" in capsys.readouterr().out
