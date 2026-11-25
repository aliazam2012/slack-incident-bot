"""The incident desk: commands, the timeline, reminders, drafts and approvals.

`/incident declare` opens a channel for the incident; everything after happens there.
Code posts inside that channel without asking (reminders, summaries, runbooks, the
postmortem). Anything addressed outside it, a customer update above all, is a send that
waits for a person's approval. Channel messages are untrusted: they enter the timeline as
notes and reach the model only as escaped data; nothing in them can pick a channel.

Slack's worker threads and the reminder thread share one desk, so its entry points
(slash commands, messages, reminders, approvals) run one at a time under a lock: each is
a read, a decision and a write on the incident's case, and two at once would collide.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from freight_agents.agentcore.approvals import ApprovalQueue, AuditLog, IdempotencyStore
from freight_agents.agentcore.guards import (
    approval_gate,
    cap_size,
    idempotency,
    state_allowlist,
    taint_guard,
)
from freight_agents.agentcore.hooks import ALLOW, Decision, Deny, HookContext, ToolOutcome, run_tool
from freight_agents.agentcore.taint import TaintSet
from freight_agents.agentcore.tools import Risk, Tool, ToolRegistry, tool
from freight_agents.incident_bot.cadence import reschedule
from freight_agents.incident_bot.chat import ChatPort
from freight_agents.incident_bot.domain import Incident, Role, Severity, Status, TimelineEntry
from freight_agents.incident_bot.drafts import (
    Draft,
    ModelFor,
    PostmortemDraft,
    draft,
    reference_model,
)
from freight_agents.incident_bot.machine import INCIDENT_MACHINE, OPEN
from freight_agents.incident_bot.policy import POLICY, runbook_index
from freight_agents.incident_bot.slack_app import SeenEvents
from freight_agents.llm import ToolCall
from freight_agents.statecore.engine import CaseEngine, Timer
from freight_agents.statecore.machine import CaseView, IllegalTransition
from freight_agents.statecore.store import SqliteEventStore
from pydantic import BaseModel

STATUS_CHANNEL = "C-customer-status"  # the made-up customer status channel
TEMPLATE_HEADINGS = [
    "Summary",
    "Impact",
    "Timeline",
    "Root Cause",
    "What Went Well",
    "What Was Hard",
    "Actions",
]  # docs/TPL-postmortem.md
SEVERITY = re.compile(r"SEV-[123]")
Clock = Callable[[], dt.datetime]
POSTING = {*OPEN, "resolved", "postmortem_drafted"}
POLICY_BY_TOOL: dict[str, set[str]] = {
    "post_in_channel": POSTING,
    "post_customer_update": {*OPEN, "resolved"},
    "post_outside": {*OPEN, "resolved"},
    "search_runbooks": POSTING,
}


log = logging.getLogger(__name__)


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@dataclass
class Stores:
    engine: CaseEngine
    approvals: ApprovalQueue
    idempotency: IdempotencyStore
    audit: AuditLog
    seen: SeenEvents
    postmortems: Path

    @classmethod
    def at(cls, directory: Path, clock: Clock = _utc_now) -> Stores:
        directory.mkdir(parents=True, exist_ok=True)
        return cls(
            engine=CaseEngine(
                INCIDENT_MACHINE, SqliteEventStore(directory / "events.sqlite3"), clock
            ),
            approvals=ApprovalQueue(directory / "approvals.sqlite3"),
            idempotency=IdempotencyStore(directory / "idempotency.sqlite3"),
            audit=AuditLog(directory / "audit.sqlite3"),
            seen=SeenEvents(directory / "seen.sqlite3"),
            postmortems=directory / "postmortems",
        )


class ChannelPost(BaseModel):
    channel: str
    text: str
    key: str  # what this post is for; a rerun of the same post is replayed, not repeated


class CustomerUpdate(BaseModel):
    text: str


class OutsidePost(BaseModel):
    channel: str
    text: str


class Posted(BaseModel):
    ts: str


class RunbookQuery(BaseModel):
    query: str


def slugify(title: str) -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return words[:40].strip("-") or "incident"


class IncidentDesk:
    def __init__(
        self,
        stores: Stores,
        chat: ChatPort,
        models: ModelFor = reference_model,
        clock: Clock = _utc_now,
        status_channel: str = STATUS_CHANNEL,
    ) -> None:
        self.stores, self.chat, self.models, self.clock = stores, chat, models, clock
        self.status_channel = status_channel
        self.runbooks = runbook_index()
        self._lock = threading.RLock()

    # Lookups.
    def case_for(self, channel: str) -> CaseView | None:
        engine = self.stores.engine
        for case_id in engine.store.case_ids():
            case = engine.view(case_id)
            if case.data["incident"]["channel"] == channel:
                return case
        return None

    def incident(self, case: CaseView) -> Incident:
        return Incident.model_validate(case.data["incident"])

    # Commands.
    def slash(self, channel: str, user: str, text: str) -> str:
        with self._lock:
            return self._slash(channel, user, text)

    def _slash(self, channel: str, user: str, text: str) -> str:
        words = text.split()
        verb = words[0].lower() if words else "help"
        rest = " ".join(words[1:])
        if verb == "declare":
            return self.declare(user, rest)
        case = self.case_for(channel)
        if case is None:
            return (
                "This is not an incident channel. Start one with `/incident declare SEV-2 title`."
            )
        handlers: dict[str, Callable[[CaseView, str, str], str]] = {
            "sev": self._severity,
            "role": self._role,
            "status": self._status,
            "resolve": self._resolve,
            "runbook": self._runbook,
            "summary": self._summary,
            "customer": self._customer,
            "postmortem": self._postmortem,
        }
        handler = handlers.get(verb)
        if handler is None:
            return "Commands: sev, role, status, resolve, runbook, summary, customer, postmortem."
        try:
            return handler(case, user, rest)
        except IllegalTransition:
            # The machine refused before anything was written: say so plainly.
            return self._not_now(case, verb)

    def _not_now(self, case: CaseView, verb: str) -> str:
        state = case.state.replace("_", " ")
        return f"{case.case_id} is {state}, so `/incident {verb}` does not apply now."

    def declare(self, user: str, rest: str) -> str:
        with self._lock:
            return self._declare(user, rest)

    def _declare(self, user: str, rest: str) -> str:
        found = SEVERITY.search(rest)
        if found is None:
            return "Give a severity: `/incident declare SEV-2 TMS orders failing`."
        customer = "+customer" in rest
        title = " ".join(w for w in rest.replace(found.group(0), "").split() if w != "+customer")
        engine = self.stores.engine
        number = len(engine.store.case_ids()) + 1
        incident_id = f"inc-{number}"
        channel = self.chat.create_channel(f"{incident_id}-{slugify(title)}")
        now = self.clock()
        incident = Incident.declare(
            incident_id, title or "untitled", channel, Severity(found.group(0)), user, customer, now
        )
        engine.open(
            incident_id, {"incident": incident.model_dump(mode="json"), "messages": []}, actor=user
        )
        reschedule(engine, incident_id, now, internal=True, customer=True)
        case = engine.view(incident_id)
        level = POLICY[incident.severity]
        hits = self.runbooks.search(title, k=1)
        if hits and hits[0][0].doc_id.startswith("runbook-"):
            runbook = f"\nRunbook: {hits[0][0].title} ({hits[0][0].source_url})"
        else:
            # No runbook for this one: point at the incident practice the team follows.
            practice = self.runbooks.search(
                "incident commander roles living incident document", k=1
            )[0][0]
            runbook = (
                f"\nNo runbook matched; incident practice: {practice.title} ({practice.source_url})"
            )
        customers = (
            (
                "\nCustomer updates every 20 minutes for the first two hours, each approved "
                "before it goes out."
            )
            if customer and level.customer_every
            else ""
        )
        self._post(
            case,
            f"declare-{incident_id}",
            (
                f"{incident.severity} declared: {incident.title}. Incident commander: <@{user}>. "
                f"{level.definition} Status updates every {level.internal_every.minutes} minutes."
                f"{customers}{runbook}"
            ),
        )
        return f"Declared {incident_id} in <#{channel}>."

    def _record(
        self, case: CaseView, event: str, incident: Incident, kind: Any, by: str, text: str
    ) -> CaseView:
        entry = TimelineEntry(at=self.clock(), kind=kind, by=by, text=text)
        updated = incident.model_copy(update={"timeline": [*incident.timeline, entry]})
        return self.stores.engine.fire(
            case.case_id, event, {"incident": updated.model_dump(mode="json")}, actor=by
        )

    def _severity(self, case: CaseView, user: str, rest: str) -> str:
        found = SEVERITY.search(rest)
        if found is None:
            return "Give a severity: SEV-1, SEV-2 or SEV-3."
        incident = self.incident(case)
        new = Severity(found.group(0))
        changed = incident.model_copy(update={"severity": new})
        case = self._record(
            case,
            "severity_changed",
            changed,
            "severity",
            user,
            f"severity {incident.severity} to {new}",
        )
        reschedule(self.stores.engine, case.case_id, self.clock(), internal=True, customer=True)
        level = POLICY[new]
        self._post(
            self.stores.engine.view(case.case_id),
            f"sev-{case.seq}",
            f"Severity is now {new}: {level.definition} Status updates every "
            f"{level.internal_every.minutes} minutes.",
        )
        return f"{case.case_id} is {new}."

    def _role(self, case: CaseView, user: str, rest: str) -> str:
        parts = rest.split()
        if len(parts) != 2 or parts[0] not in {r.value for r in Role}:
            return "Use `/incident role commander|communications|operations @person`."
        role, person = Role(parts[0]), parts[1].strip("<>@")
        incident = self.incident(case)
        roles = {**incident.roles, role: person}
        case = self._record(
            case,
            "role_assigned",
            incident.model_copy(update={"roles": roles}),
            "role",
            user,
            f"{role.value}: {person}",
        )
        handoff = (
            f" <@{person}>, you are now the incident commander." if role == Role.COMMANDER else ""
        )
        self._post(case, f"role-{case.seq}", f"{role.value.capitalize()}: <@{person}>.{handoff}")
        return f"{role.value} is {person}."

    def _status(self, case: CaseView, user: str, rest: str) -> str:
        words = rest.split()
        incident = self.incident(case)
        moves = {
            "identified": Status.IDENTIFIED,
            "monitoring": Status.MONITORING,
            "investigating": Status.INVESTIGATING,
        }
        status = moves.get(words[0].lower()) if words else None
        text = " ".join(words[1:] if status else words) or "no detail"
        if status is not None and status != incident.status:
            # Move first: a move the machine refuses leaves the record untouched.
            event = "regressed" if status == Status.INVESTIGATING else status.value
            case = self.stores.engine.fire(case.case_id, event, actor=user)
        updated = incident.model_copy(update={"status": status or incident.status})
        case = self._record(case, "status_update", updated, "status", user, text)
        reschedule(self.stores.engine, case.case_id, self.clock(), internal=True, customer=False)
        return f"Status recorded: {updated.status}."

    def _resolve(self, case: CaseView, user: str, rest: str) -> str:
        incident = self.incident(case)
        resolved = incident.model_copy(update={"status": Status.RESOLVED})
        case = self._record(case, "resolved", resolved, "resolved", user, rest or "resolved")
        self._post(
            case,
            "resolved",
            f"Resolved: {rest or incident.title}. Next: "
            "`/incident postmortem` drafts the blameless postmortem.",
        )
        return f"{case.case_id} resolved."

    def _runbook(self, case: CaseView, user: str, rest: str) -> str:
        outcome = self._run(
            case,
            ToolCall(
                id=f"rb-{case.seq}",
                name="search_runbooks",
                args={"query": rest or self.incident(case).title},
            ),
        )
        passages = (outcome.output or {}).get("passages", [])
        if not passages:
            return "No runbook matched."
        lines = [
            f"*{p['title']}*, {p['section']}: {p['text']} ({p['source_url']})" for p in passages[:2]
        ]
        self._post(case, f"runbook-{case.seq}", "\n".join(lines))
        return "Runbook posted."

    def _messages(self, case: CaseView) -> list[str]:
        return [f"{m['user']}: {m['text']}" for m in case.data.get("messages", [])]

    def _summary(self, case: CaseView, user: str, rest: str) -> str:
        incident = self.incident(case)
        result = draft(
            "summary",
            incident,
            self._messages(case),
            self.models("summary", incident),
            self.clock(),
        )
        if not isinstance(result, Draft):
            return "No summary was drafted."
        self._post(case, f"summary-{case.seq}", f"Summary: {result.text}")
        return "Summary posted."

    def _customer(self, case: CaseView, user: str, rest: str) -> str:
        approval = self._draft_customer_update(case)
        return (
            f"Customer update drafted; waiting for approval {approval}."
            if approval
            else ("No customer update was drafted.")
        )

    def _draft_customer_update(self, case: CaseView) -> str | None:
        incident = self.incident(case)
        result = draft(
            "customer_update",
            incident,
            self._messages(case),
            self.models("customer_update", incident),
            self.clock(),
        )
        if not isinstance(result, Draft):
            return None
        outcome = self._run(
            case,
            ToolCall(id=f"cu-{case.seq}", name="post_customer_update", args={"text": result.text}),
        )
        approval = (outcome.output or {}).get("approval_id")
        if approval:
            self._post(
                case,
                f"cu-draft-{approval}",
                f"Customer update drafted, waiting for approval ({approval}): {result.text}",
            )
        return str(approval) if approval else None

    def _postmortem(self, case: CaseView, user: str, rest: str) -> str:
        if case.state in OPEN:
            return "Resolve the incident first."
        if case.state != "resolved":
            return self._not_now(case, "postmortem")
        incident = self.incident(case)
        result = draft(
            "postmortem",
            incident,
            self._messages(case),
            self.models("postmortem", incident),
            self.clock(),
        )
        if not isinstance(result, PostmortemDraft):
            return "No postmortem was drafted."
        text = render_postmortem(incident, result)
        self.stores.postmortems.mkdir(parents=True, exist_ok=True)
        path = self.stores.postmortems / f"{incident.id}.md"
        path.write_text(text)
        case = self._record(case, "postmortem_drafted", incident, "postmortem", user, str(path))
        self._post(case, "postmortem", f"Postmortem drafted: {path.name}\n\n{text[:2500]}")
        return "Postmortem drafted."

    # Channel messages and reminders.
    def message(self, channel: str, user: str, text: str, ts: str, thread_ts: str | None) -> None:
        with self._lock:
            self._message(channel, user, text, ts)

    def _message(self, channel: str, user: str, text: str, ts: str) -> None:
        case = self.case_for(channel)
        if case is None or case.state not in POSTING:
            return
        if any(m["ts"] == ts for m in case.data.get("messages", [])):
            return  # Slack redelivered it; the first copy is already in the timeline
        messages = [*case.data.get("messages", []), {"user": user, "text": text, "ts": ts}]
        incident = self.incident(case)
        entry = TimelineEntry(at=self.clock(), kind="note", by=user, text=text[:500])
        updated = incident.model_copy(update={"timeline": [*incident.timeline, entry]})
        self.stores.engine.patch(
            case.case_id,
            {"messages": messages, "incident": updated.model_dump(mode="json")},
            actor=user,
        )

    def tick(self) -> list[str]:
        """Fire due reminders: post them in their incident's channel and set the next one."""
        with self._lock:
            return self._tick()

    def _tick(self) -> list[str]:
        done = []
        for timer in self.stores.engine.fire_due(self.clock()):
            try:
                self._remind(timer)
            except Exception:
                # The next reminder is already scheduled; one failed post must not stop
                # this incident's reminders or any other incident's.
                log.exception("could not post a reminder for %s (%s)", timer.case_id, timer.event)
            done.append(f"{timer.case_id}:{timer.event}")
        return done

    def _remind(self, timer: Timer) -> None:
        """Schedule the next reminder first, then post this one."""
        internal = timer.event == "update_overdue"
        reschedule(
            self.stores.engine,
            timer.case_id,
            self.clock(),
            internal=internal,
            customer=not internal,
        )
        case = self.stores.engine.view(timer.case_id)
        incident = self.incident(case)
        if internal:
            self._post(
                case,
                f"remind-{timer.timer_id}",
                (
                    f"{incident.severity} reminder: time for a status update "
                    f"(`/incident status ...`). Updates every "
                    f"{POLICY[incident.severity].internal_every.minutes} minutes."
                ),
            )
            return
        waiting = [
            a
            for a in self.stores.approvals.pending()
            if a.case_id == case.case_id and a.tool == "post_customer_update"
        ]
        if waiting:
            self._post(
                case,
                f"cu-wait-{timer.timer_id}",
                f"Customer update overdue: approval {waiting[0].id} is still waiting for a person.",
            )
        else:
            self._draft_customer_update(case)

    def approve(self, approval_id: str, approver: str) -> CaseView:
        with self._lock:
            return self._approve(approval_id, approver)

    def _approve(self, approval_id: str, approver: str) -> CaseView:
        approvals = self.stores.approvals
        approval = approvals.get(approval_id)
        if approval.case_id is None:
            raise LookupError(f"approval {approval_id} is not tied to an incident")
        if approvals.status(approval_id) == "pending":
            approvals.approve(approval_id, approver)
        case = self.stores.engine.view(approval.case_id)
        outcome = self._run(
            case,
            ToolCall(id=f"approved-{approval_id}", name=approval.tool, args=approval.args),
            actor=f"person:{approver}",
        )
        if outcome.status != "ok":
            approvals.release(approval_id)
            raise RuntimeError(f"approved post did not run: {outcome.code} {outcome.reason}")
        if approval.tool == "post_customer_update":
            incident = self.incident(case)
            case = self._record(
                case,
                "customer_update",
                incident,
                "customer_update",
                f"person:{approver}",
                str(approval.args["text"]),
            )
            if case.state in OPEN:  # no customer reminders once it is resolved
                reschedule(
                    self.stores.engine, case.case_id, self.clock(), internal=False, customer=True
                )
        return self.stores.engine.view(case.case_id)

    # Tools and hooks.
    def _post(self, case: CaseView, key: str, text: str) -> None:
        channel = self.incident(case).channel
        outcome = self._run(
            case,
            ToolCall(
                id=f"post-{key}",
                name="post_in_channel",
                args={"channel": channel, "text": text, "key": key},
            ),
        )
        if outcome.status not in ("ok", "replayed"):
            raise RuntimeError(f"could not post in {channel}: {outcome.code} {outcome.reason}")

    def toolkit(self, case: CaseView) -> tuple[ToolRegistry, list[Any], list[Any]]:
        channel = self.incident(case).channel

        def channel_policy(t: Tool, args: dict[str, Any], ctx: HookContext) -> Decision:
            if t.name == "post_in_channel" and args.get("channel") != channel:
                return Deny(
                    "channel", "the bot posts without approval only in the incident's own channel"
                )
            return ALLOW

        idem_pre, idem_post = idempotency(self.stores.idempotency)
        pre = [
            state_allowlist(POLICY_BY_TOOL),
            taint_guard(
                {
                    "post_outside": {"channel": None},
                    "post_customer_update": {"text": lambda v: True},
                    "post_in_channel": {"channel": lambda v: v == channel},
                }
            ),
            channel_policy,
            idem_pre,
            approval_gate(self.stores.approvals),
        ]
        return ToolRegistry(self._tools()), pre, [idem_post, cap_size()]

    def _tools(self) -> list[Tool]:
        chat, status_channel = self.chat, self.status_channel

        @tool(
            name="post_in_channel",
            description="Post in the incident's own channel.",
            risk=Risk.WRITE,
        )
        def post_in_channel(args: ChannelPost) -> Posted:
            return Posted(ts=chat.post(args.channel, args.text))

        @tool(
            name="post_customer_update",
            description="Post an approved customer update to the customer status channel.",
            risk=Risk.SEND,
        )
        def post_customer_update(args: CustomerUpdate) -> Posted:
            return Posted(ts=chat.post(status_channel, args.text))

        @tool(
            name="post_outside",
            description="Post outside the incident channel; a person approves it first.",
            risk=Risk.SEND,
        )
        def post_outside(args: OutsidePost) -> Posted:
            return Posted(ts=chat.post(args.channel, args.text))

        from freight_agents.knowledge.index import search_tool

        search = dataclasses.replace(
            search_tool(self.runbooks),
            name="search_runbooks",
            description="Search runbooks and incident practice; returns citations.",
        )
        return [post_in_channel, post_customer_update, post_outside, search]

    def context(self, case: CaseView, actor: str = "workflow") -> HookContext:
        taint = TaintSet()
        for message in case.data.get("messages", []):
            taint.add(str(message["text"]), "incident.thread")
        return HookContext(
            run_id=f"{actor}-{case.case_id}",
            agent=actor,
            now=self.clock(),
            case_id=case.case_id,
            state=case.state,
            taint=taint,
        )

    def _run(self, case: CaseView, call: ToolCall, actor: str = "workflow") -> ToolOutcome:
        registry, pre, post = self.toolkit(case)
        return run_tool(call, registry, pre, post, self.context(case, actor), self.stores.audit)


# A Slack mention (<@U012AB3CD>), a Slack user id (U plus 8 or more capitals or digits) or
# this repository's made-up ids (U-dana). Linear, no nested quantifiers; ordinary words
# that start with U ("Users", "Update") do not match.
PERSON = re.compile(r"<@[A-Za-z0-9-]+>|\bU[A-Z0-9]{8,}\b|\bU-[a-z]+\b")


def blameless(text: str) -> str:
    """Postmortem prose talks about systems and decisions: no person is named in it."""
    return PERSON.sub("a responder", text)


def render_postmortem(incident: Incident, d: PostmortemDraft) -> str:
    """The repository's postmortem template, prose from the draft and the timeline from code."""
    timeline = "\n".join(
        f"- {e.at:%Y-%m-%d %H:%M} UTC, {e.kind}: {e.text}" for e in incident.timeline
    )
    sections = {
        "Summary": blameless(d.summary),
        "Impact": blameless(d.impact),
        "Timeline": timeline,
        "Root Cause": blameless(d.root_cause),
        "What Went Well": blameless(d.went_well),
        "What Was Hard": blameless(d.was_hard),
        "Actions": "\n".join(f"- {blameless(a)}" for a in d.actions) or "- none yet",
    }
    toc = "\n".join(f"- [{h}](#{h.lower().replace(' ', '-')})" for h in TEMPLATE_HEADINGS)
    body = "\n\n".join(f"## {h}\n\n{sections[h]}" for h in TEMPLATE_HEADINGS)
    return f"# Postmortem: {incident.title}\n\n## Table of Contents\n\n{toc}\n\n---\n\n{body}\n"
