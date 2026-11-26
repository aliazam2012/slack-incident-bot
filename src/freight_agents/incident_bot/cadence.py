"""Update reminders on the cadence the severity sets, rescheduled by every update.

A reminder is a statecore timer carrying a generation number. Rescheduling bumps the
case's generation, so an older reminder that comes due is skipped by the machine's guard:
a status update just before a reminder means no reminder, and a severity change replaces
the old cadence at once.
"""

from __future__ import annotations

import datetime as dt

from freight_agents.statecore.engine import CaseEngine

from freight_agents.incident_bot.domain import Incident
from freight_agents.incident_bot.policy import POLICY, cadence


def reschedule(
    engine: CaseEngine, case_id: str, now: dt.datetime, internal: bool, customer: bool
) -> None:
    case = engine.view(case_id)
    incident = Incident.model_validate(case.data["incident"])
    level = POLICY[incident.severity]
    elapsed = now - incident.declared_at
    if internal:
        generation = int(case.data.get("update_generation", 0)) + 1
        engine.patch(case_id, {"update_generation": generation}, actor="cadence")
        engine.schedule(
            case_id,
            "update_overdue",
            now + cadence(level.internal_every, elapsed),
            {"generation": generation},
        )
    if customer and incident.customer_impact and level.customer_every is not None:
        case = engine.view(case_id)
        generation = int(case.data.get("customer_generation", 0)) + 1
        engine.patch(case_id, {"customer_generation": generation}, actor="cadence")
        engine.schedule(
            case_id,
            "customer_update_overdue",
            now + cadence(level.customer_every, elapsed),
            {"generation": generation},
        )
