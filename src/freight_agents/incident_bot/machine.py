r"""The incident state machine. Every open state takes updates, roles, severity changes and
reminders; reminders carry a generation, so one replaced by a newer reminder does nothing.

    investigating --identified--> identified --monitoring--> monitoring
          ^  \______________ monitoring ______________/   |     |
          |                identified (the fix did not hold) <-/     |
          |___ regressed (from identified or monitoring) ____________|
    any open state --resolved--> resolved
    resolved --postmortem_drafted--> postmortem_drafted --closed--> closed

A resolved incident still takes the customer update that says it is over.
"""

from __future__ import annotations

from typing import Any

from freight_agents.statecore.machine import CaseView, Guard, StateMachine, Transition

OPEN = ("investigating", "identified", "monitoring")
STAYS = ("status_update", "severity_changed", "role_assigned", "note", "customer_update")


def current(key: str) -> Guard:
    def guard(case: CaseView, payload: dict[str, Any]) -> str | None:
        if payload.get("generation") == case.data.get(key):
            return None
        return "a newer reminder replaced this one"

    return guard


def _transitions() -> list[Transition]:
    moves = [
        Transition("investigating", "identified", "identified"),
        Transition("investigating", "monitoring", "monitoring"),
        Transition("identified", "monitoring", "monitoring"),
        Transition("monitoring", "identified", "identified"),
        Transition("identified", "regressed", "investigating"),
        Transition("monitoring", "regressed", "investigating"),
        Transition("resolved", "customer_update", "resolved"),
        Transition("resolved", "postmortem_drafted", "postmortem_drafted"),
        Transition("postmortem_drafted", "closed", "closed"),
    ]
    for state in OPEN:
        moves += [Transition(state, event, state) for event in STAYS]
        moves.append(Transition(state, "update_overdue", state, guard=current("update_generation")))
        moves.append(
            Transition(
                state, "customer_update_overdue", state, guard=current("customer_generation")
            )
        )
        moves.append(Transition(state, "resolved", "resolved"))
    return moves


INCIDENT_MACHINE = StateMachine(
    name="incident", initial="investigating", transitions=_transitions(), terminal={"closed"}
)
