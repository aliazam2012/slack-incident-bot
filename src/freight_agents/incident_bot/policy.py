"""Severity levels and update cadence, each value with its source. Read 2026-11-24.

PagerDuty's published incident response guide (docs.pagerduty.com, Ops Guides):
severity levels; anything above SEV-3 is a major incident; external updates at least
every 20 minutes from the scoping update during the first two hours, then at reduced
frequency; internal stakeholder updates roughly every 30 minutes. Where the guide
gives no number (the reduced frequency, a SEV-3 cadence), the value is this team's
stated choice. SEV-4 and SEV-5 are tickets in that guide, not incidents, so the bot
runs SEV-1 to SEV-3.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from freight_agents.incident_bot.domain import Severity
from freight_agents.knowledge.index import Bm25Index, package_corpus

SEVERITY_URL = "https://docs.pagerduty.com/ops-guides/incident-response-guide/severity-levels"
EXTERNAL_URL = "https://docs.pagerduty.com/ops-guides/incident-response-guide/external-comms"
INTERNAL_URL = "https://docs.pagerduty.com/ops-guides/incident-response-guide/during-an-incident"
TEAM = "Team choice: the guide gives no number for this"


@dataclass(frozen=True)
class Cadence:
    minutes: int
    source: str
    after_hours: float | None = None  # from then on, `then_minutes`
    then_minutes: int | None = None
    then_source: str | None = None


@dataclass(frozen=True)
class Level:
    severity: Severity
    definition: str
    major: bool
    source: str
    internal_every: Cadence
    customer_every: Cadence | None


MAJOR_CUSTOMER = Cadence(
    minutes=20, source=EXTERNAL_URL, after_hours=2.0, then_minutes=60, then_source=TEAM
)
INTERNAL = Cadence(minutes=30, source=INTERNAL_URL)

POLICY: dict[Severity, Level] = {
    Severity.SEV1: Level(
        Severity.SEV1,
        "Critical issue that warrants public notification and liaison with executive teams.",
        True,
        SEVERITY_URL,
        INTERNAL,
        MAJOR_CUSTOMER,
    ),
    Severity.SEV2: Level(
        Severity.SEV2,
        "Critical system issue actively impacting many customers' ability to use the product.",
        True,
        SEVERITY_URL,
        INTERNAL,
        MAJOR_CUSTOMER,
    ),
    Severity.SEV3: Level(
        Severity.SEV3,
        "Stability or minor customer-impacting issues that "
        "require immediate attention from service owners.",
        False,
        SEVERITY_URL,
        Cadence(minutes=60, source=TEAM),
        None,
    ),
}


def cadence(c: Cadence, elapsed: dt.timedelta) -> dt.timedelta:
    """The interval to the next update, `elapsed` after the incident was declared."""
    if (
        c.after_hours is not None
        and c.then_minutes is not None
        and elapsed >= dt.timedelta(hours=c.after_hours)
    ):
        return dt.timedelta(minutes=c.then_minutes)
    return dt.timedelta(minutes=c.minutes)


def runbook_index() -> Bm25Index:
    """Runbooks and incident practice, searchable with citations."""
    return Bm25Index(package_corpus("freight_agents.incident_bot"))
