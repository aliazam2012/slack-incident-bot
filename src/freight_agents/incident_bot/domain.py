"""Incidents, roles and the timeline: the living incident document, kept by code."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class Severity(StrEnum):
    SEV1 = "SEV-1"
    SEV2 = "SEV-2"
    SEV3 = "SEV-3"

    @staticmethod
    def higher(a: Severity, b: Severity) -> Severity:
        """When unsure between two levels, take the more severe one (PagerDuty's guide)."""
        return min(a, b, key=lambda s: int(s.value[-1]))


class Status(StrEnum):
    INVESTIGATING = "investigating"
    IDENTIFIED = "identified"
    MONITORING = "monitoring"
    RESOLVED = "resolved"


class Role(StrEnum):
    COMMANDER = "commander"
    COMMUNICATIONS = "communications"
    OPERATIONS = "operations"


EntryKind = Literal[
    "declared",
    "severity",
    "role",
    "status",
    "note",
    "update_posted",
    "customer_update",
    "resolved",
    "postmortem",
]


class TimelineEntry(BaseModel):
    at: dt.datetime
    kind: EntryKind
    by: str
    text: str


class Incident(BaseModel):
    id: str
    title: str
    channel: str
    severity: Severity
    status: Status
    roles: dict[Role, str]
    customer_impact: bool
    declared_at: dt.datetime
    timeline: list[TimelineEntry]

    @classmethod
    def declare(
        cls,
        incident_id: str,
        title: str,
        channel: str,
        severity: Severity,
        commander: str,
        customer_impact: bool,
        at: dt.datetime,
    ) -> Incident:
        entry = TimelineEntry(
            at=at,
            kind="declared",
            by=commander,
            text=f"{severity} declared: {title}; commander {commander}",
        )
        return cls(
            id=incident_id,
            title=title,
            channel=channel,
            severity=severity,
            status=Status.INVESTIGATING,
            roles={Role.COMMANDER: commander},
            customer_impact=customer_impact,
            declared_at=at,
            timeline=[entry],
        )
