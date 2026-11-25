import datetime as dt
from pathlib import Path

from freight_agents.incident_bot.cadence import reschedule
from freight_agents.incident_bot.domain import Incident, Severity
from freight_agents.incident_bot.machine import INCIDENT_MACHINE
from freight_agents.statecore.engine import CaseEngine
from freight_agents.statecore.store import SqliteEventStore

T0 = dt.datetime(2026, 11, 24, 9, 0, tzinfo=dt.UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> dt.datetime:
        return self.now


def _open(tmp_path: Path, severity: Severity, customer_impact: bool, case: str = "inc-1"):  # type: ignore[no-untyped-def]
    clock = Clock()
    engine = CaseEngine(INCIDENT_MACHINE, SqliteEventStore(tmp_path / f"{case}.sqlite3"), clock)
    incident = Incident.declare(
        case, "TMS orders failing", f"C-{case}", severity, "U-dana", customer_impact, T0
    )
    engine.open(case, {"incident": incident.model_dump(mode="json")}, actor="U-dana")
    reschedule(engine, case, clock(), internal=True, customer=True)
    return engine, clock


def fire(engine: CaseEngine, at: dt.datetime) -> list[str]:
    return [t.event for t in engine.fire_due(at)]


def test_a_sev3_reminds_every_hour_and_a_status_update_resets_it(tmp_path: Path) -> None:
    engine, clock = _open(tmp_path, Severity.SEV3, customer_impact=False)
    clock.now = T0 + dt.timedelta(minutes=50)
    engine.fire("inc-1", "status_update", {"text": "rolling back"}, actor="U-dana")
    reschedule(engine, "inc-1", clock(), internal=True, customer=False)
    assert fire(engine, T0 + dt.timedelta(minutes=61)) == []  # the 10:00 reminder is stale
    assert fire(engine, T0 + dt.timedelta(minutes=111)) == ["update_overdue"]


def test_escalating_to_sev1_shortens_the_cadence(tmp_path: Path) -> None:
    engine, clock = _open(tmp_path, Severity.SEV3, customer_impact=True)
    clock.now = T0 + dt.timedelta(minutes=10)
    incident = Incident.model_validate(engine.view("inc-1").data["incident"])
    escalated = incident.model_copy(update={"severity": Severity.SEV1})
    engine.fire(
        "inc-1", "severity_changed", {"incident": escalated.model_dump(mode="json")}, actor="U-dana"
    )
    reschedule(engine, "inc-1", clock(), internal=True, customer=True)
    # SEV-1: customer updates every 20 minutes, internal every 30, from the change.
    assert fire(engine, T0 + dt.timedelta(minutes=31)) == ["customer_update_overdue"]
    assert fire(engine, T0 + dt.timedelta(minutes=41)) == ["update_overdue"]
    assert fire(engine, T0 + dt.timedelta(minutes=61)) == []  # the SEV-3 hour is gone


def test_customer_updates_slow_down_after_two_hours(tmp_path: Path) -> None:
    engine, clock = _open(tmp_path, Severity.SEV2, customer_impact=True)
    clock.now = T0 + dt.timedelta(hours=2, minutes=5)
    engine.fire("inc-1", "customer_update", {"text": "still degraded"}, actor="U-comms")
    reschedule(engine, "inc-1", clock(), internal=False, customer=True)
    due = [
        t for t in engine.due(T0 + dt.timedelta(hours=4)) if t.event == "customer_update_overdue"
    ]
    assert due[-1].due_at == T0 + dt.timedelta(hours=3, minutes=5)


def test_no_customer_reminders_without_customer_impact(tmp_path: Path) -> None:
    engine, _ = _open(tmp_path, Severity.SEV1, customer_impact=False)
    assert "customer_update_overdue" not in fire(engine, T0 + dt.timedelta(hours=1))


def test_a_resolved_incident_gets_no_reminders(tmp_path: Path) -> None:
    engine, _ = _open(tmp_path, Severity.SEV1, customer_impact=True)
    engine.fire("inc-1", "resolved", {"text": "fixed"}, actor="U-dana")
    assert fire(engine, T0 + dt.timedelta(hours=3)) == []
    assert engine.view("inc-1").state == "resolved"


def test_two_incidents_keep_their_own_reminders(tmp_path: Path) -> None:
    a, _ = _open(tmp_path, Severity.SEV1, customer_impact=False, case="inc-a")
    b, _ = _open(tmp_path, Severity.SEV3, customer_impact=False, case="inc-b")
    at = T0 + dt.timedelta(minutes=31)
    assert [(t.case_id, t.event) for t in a.fire_due(at)] == [("inc-a", "update_overdue")]
    assert b.fire_due(at) == []
