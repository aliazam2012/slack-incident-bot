"""incident-bot over MCP: read an incident, its timeline and the approvals waiting; search
runbooks. Nothing here posts, approves or changes an incident."""

from __future__ import annotations

import uuid
from typing import Any

from freight_agents.agentcore.guards import cap_size
from freight_agents.agentcore.hooks import HookContext
from freight_agents.agentcore.tools import Risk, Tool, ToolRegistry, strict_schema, tool
from freight_agents.knowledge.index import SearchIn, search_tool
from freight_agents.llm import ToolSpec
from freight_agents.mcp.bridge import Route, RouteError, build_server
from pydantic import BaseModel

from freight_agents.incident_bot.desk import IncidentDesk
from freight_agents.incident_bot.domain import TimelineEntry

INSTRUCTIONS = (
    "Incidents run in Slack. Read an incident, its timeline and the approvals waiting, and "
    "search the runbooks. Posting, approving and changing incidents are not available over MCP."
)


class CaseIn(BaseModel):
    case_id: str


class IncidentOut(BaseModel):
    case_id: str
    state: str
    title: str
    severity: str
    status: str
    channel: str
    roles: dict[str, str]
    customer_impact: bool


class TimelineOut(BaseModel):
    case_id: str
    entries: list[TimelineEntry]


class PendingIn(BaseModel):
    pass


class Pending(BaseModel):
    approval_id: str
    case_id: str | None
    tool: str
    text: str


class PendingOut(BaseModel):
    approvals: list[Pending]


def _specs() -> list[ToolSpec]:
    described: list[tuple[str, str, type[BaseModel]]] = [
        ("get_incident", "An incident: state, severity, status, channel, roles.", CaseIn),
        ("incident_timeline", "The incident's timeline, as code recorded it.", CaseIn),
        ("list_pending_approvals", "Customer updates and posts waiting for a person.", PendingIn),
        ("search_runbooks", "Search runbooks and incident practice; returns citations.", SearchIn),
    ]
    return [ToolSpec(name=n, description=d, input_schema=strict_schema(m)) for n, d, m in described]


def build_incident_server(desk: IncidentDesk) -> Any:
    engine = desk.stores.engine

    @tool(name="get_incident", description="Read an incident.", risk=Risk.READ)
    def get_incident(args: CaseIn) -> IncidentOut:
        case = engine.view(args.case_id)
        i = desk.incident(case)
        return IncidentOut(
            case_id=case.case_id,
            state=case.state,
            title=i.title,
            severity=i.severity.value,
            status=i.status.value,
            channel=i.channel,
            roles={r.value: p for r, p in i.roles.items()},
            customer_impact=i.customer_impact,
        )

    @tool(name="incident_timeline", description="Read a timeline.", risk=Risk.READ)
    def incident_timeline(args: CaseIn) -> TimelineOut:
        incident = desk.incident(engine.view(args.case_id))
        return TimelineOut(case_id=args.case_id, entries=incident.timeline)

    @tool(name="list_pending_approvals", description="Approvals waiting.", risk=Risk.READ)
    def list_pending_approvals(args: PendingIn) -> PendingOut:
        return PendingOut(
            approvals=[
                Pending(
                    approval_id=a.id,
                    case_id=a.case_id,
                    tool=a.tool,
                    text=str(a.args.get("text", "")),
                )
                for a in desk.stores.approvals.pending()
            ]
        )

    runbooks = search_tool(desk.runbooks)
    reads: dict[str, Tool] = {
        "get_incident": get_incident,
        "incident_timeline": incident_timeline,
        "list_pending_approvals": list_pending_approvals,
        "search_runbooks": runbooks,
    }

    def route(name: str, args: dict[str, Any]) -> Route:
        if name not in reads:
            raise RouteError("unknown_tool", f"no tool {name}")
        if "case_id" in args and not engine.exists(str(args["case_id"])):
            raise RouteError("not_found", f"no incident {args['case_id']}")
        ctx = HookContext(run_id=f"mcp-{uuid.uuid4().hex[:10]}", agent="mcp", now=desk.clock())
        call_name = runbooks.name if name == "search_runbooks" else name
        return Route(ToolRegistry([reads[name]]), [], [cap_size()], ctx, call_name, args)

    return build_server("freight-incidents", _specs(), route, desk.stores.audit, INSTRUCTIONS)
