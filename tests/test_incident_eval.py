from pathlib import Path
from typing import Any

import pytest
from freight_agents.incident_bot import desk as desk_module
from freight_agents.incident_bot.evaluation import FLOORS, run, scenarios, write_report


def test_twenty_scenarios_with_six_attacks() -> None:
    all_ = scenarios()
    assert len(all_) >= 20
    assert sum(1 for s in all_ if s.attack) == 6


def test_the_reference_run_passes_the_gate(tmp_path: Path) -> None:
    result = run()
    assert result.failures(FLOORS) == [], [r for r in result.per_case if not r["internal_ok"]]
    assert "Gate: passed" in write_report(result, tmp_path, "2026-11-24").read_text()


def test_the_grader_catches_reminders_that_ignore_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    real = desk_module.reschedule

    def only_first(engine: Any, case_id: str, now: Any, internal: bool, customer: bool) -> None:
        if "update_generation" not in engine.view(case_id).data:
            real(engine, case_id, now, internal, customer)

    monkeypatch.setattr(desk_module, "reschedule", only_first)
    assert run().metrics["reminders_on_cadence"] < 1.0
