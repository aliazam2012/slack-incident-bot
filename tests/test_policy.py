import datetime as dt

import pytest

from freight_agents.incident_bot.domain import Incident, Role, Severity
from freight_agents.incident_bot.policy import POLICY, cadence, runbook_index


def test_the_bot_handles_sev1_to_sev3_and_every_value_names_its_source() -> None:
    assert set(POLICY) == {Severity.SEV1, Severity.SEV2, Severity.SEV3}
    for level in POLICY.values():
        assert level.definition
        assert level.source.startswith("https://")
        assert level.internal_every.source.startswith(("https://", "Team choice"))
        if level.customer_every is not None:
            assert level.customer_every.source.startswith(("https://", "Team choice"))


def test_major_incidents_update_customers_every_20_minutes_for_two_hours() -> None:
    sev1 = POLICY[Severity.SEV1]
    assert POLICY[Severity.SEV2].major
    assert not POLICY[Severity.SEV3].major
    assert cadence(sev1.customer_every, elapsed=dt.timedelta(minutes=30)) == dt.timedelta(
        minutes=20
    )
    later = cadence(sev1.customer_every, elapsed=dt.timedelta(hours=3))
    assert later > dt.timedelta(minutes=20)
    assert POLICY[Severity.SEV1].internal_every.minutes == 30
    assert POLICY[Severity.SEV3].customer_every is None


def test_an_incident_starts_with_a_commander_and_is_unsure_means_higher() -> None:
    incident = Incident.declare(
        "inc-1",
        "TMS orders failing",
        "#inc-1-tms",
        Severity.SEV2,
        commander="U-dana",
        customer_impact=True,
        at=dt.datetime(2026, 11, 24, 9, 0, tzinfo=dt.UTC),
    )
    assert incident.roles[Role.COMMANDER] == "U-dana"
    assert incident.timeline[0].kind == "declared"
    assert Severity.higher(Severity.SEV2, Severity.SEV3) == Severity.SEV2


@pytest.mark.parametrize(
    ("query", "doc"),
    [
        ("TMS order API failing timeouts", "runbook-tms-outage"),
        ("EDI 204 tenders stopped arriving", "runbook-edi-feed-down"),
        ("carrier portal login broken claims", "runbook-carrier-portal-down"),
        ("keep a living incident document and hand off clearly", "google-sre-managing-incidents"),
        ("how often to update customers during a major incident", "pagerduty-incident-response"),
    ],
)
def test_runbook_search_returns_cited_passages(query: str, doc: str) -> None:
    hits = runbook_index().search(query, k=2)
    assert hits
    passage = hits[0][0]
    assert passage.doc_id == doc
    assert passage.source_url.startswith("https://")
