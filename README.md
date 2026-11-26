# slack-incident-bot

[![CI](https://github.com/aliazam2012/slack-incident-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/aliazam2012/slack-incident-bot/actions/workflows/ci.yml)
![Evaluation gate](https://img.shields.io/badge/evaluation%20gate-20%20incidents-brightgreen)
![Python 3.12](https://img.shields.io/badge/python-3.12-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

**A Slack bot that runs an incident inside its own channel: severity and roles, a timeline, update reminders on the cadence the severity sets, cited runbooks, customer updates a person approves, and a blameless postmortem.**

**Skills shown:** an agent in a team's workflow (Slack Bolt, Socket Mode) · a state machine with cadence timers · retrieval with citations · an approval gate for anything leaving the channel · prompt-injection defense against channel messages · a contract-tested fake Slack · safe under concurrent events · evaluation on 20 incidents played minute by minute

## Table of Contents

- [Results](#results)
- [How It Works](#how-it-works)
- [Commands](#commands)
- [Run It](#run-it)
- [Connect a Real Workspace](#connect-a-real-workspace)
- [Limits](#limits)
- [Where It Fits](#where-it-fits)

---

## Results

From the [2026-11-24 evaluation](evals/reports/2026-11-24-reference.md): 20 incidents played minute by minute against an in-memory Slack, six of them red-team cases.

| Metric | Value |
|---|---|
| Status reminders at exactly the expected minutes | 1.00 |
| Customer-update reminders at exactly the expected minutes | 1.00 |
| Posts outside the incident channels | 0 |
| Customer updates posted without approval | 0 |
| Timelines complete | 1.00 |
| Declarations citing a runbook or incident practice | 1.00 |
| Postmortems with every template section and no person named | 1.00 |
| Red-team cases resisted (the model obeys the injected text) | 6 of 6 |

The expected minutes are written by hand from the policy, so the grader checks behavior rather than the code's own arithmetic. Drafts run in reference mode until a live run is recorded.

## How It Works

| Part | Module | What it does |
|---|---|---|
| Policy | `policy.py` | SEV-1 to SEV-3 from PagerDuty's published guide: status every 30 minutes for SEV-1 and SEV-2 and customers every 20 minutes for two hours when customers are affected; SEV-3 hourly (a team choice, labeled) |
| Incidents | `domain.py`, `machine.py` | The living incident document the Google SRE book describes, kept by code; a state machine from investigating to closed |
| Reminders | `cadence.py` | Timers carrying a generation: every update or severity change replaces the pending reminder |
| Chat | `chat.py`, `slack_app.py` | A chat port with an in-memory fake and a slack_sdk adapter, contract-tested against a stub of the Slack Web API; a Bolt app that handles a redelivered event once |
| Desk | `desk.py`, `drafts.py` | Commands, runbook search with citations, model drafts through record-only tools, approvals for anything outside the channel, the postmortem |

[ADR-0013](docs/adr/ADR-0013-chat-port-and-fake-slack.md) explains the chat port and the fake.

## Commands

| Command | Where | Does |
|---|---|---|
| `/incident declare SEV-2 title [+customer]` | any channel | Opens `#inc-N-title`, posts the cadence and a cited runbook |
| `/incident sev SEV-1` | incident channel | Changes severity and the cadence |
| `/incident role commander @person` | incident channel | Assigns a role; a commander handoff is announced |
| `/incident status [identified\|monitoring\|investigating] text` | incident channel | Records status and resets the reminder |
| `/incident runbook query` | incident channel | Posts matching runbook passages with sources |
| `/incident summary` | incident channel | Posts a summary of the thread |
| `/incident customer` | incident channel | Drafts a customer update for approval |
| `/incident resolve text`, `/incident postmortem` | incident channel | Resolves; drafts the postmortem |

## Run It

```bash
uv run freight-incidents demo     # one incident against the in-memory Slack, printed as a transcript
uv run freight-incidents eval     # the 20 scenarios
uv run freight-incidents mcp      # read tools over MCP on stdio
```

## Connect a Real Workspace

Socket Mode needs no public URL. In the Slack app settings ([Slack's Socket Mode guide](https://docs.slack.dev/apis/events-api/using-socket-mode)):

1. Turn on Socket Mode and generate an app-level token (it starts with `xapp-`) under Basic Information.
2. Add bot token scopes `chat:write` (post), `channels:history` (read messages in public channels) and `channels:manage` (create the incident channel).
3. Subscribe to the bot event `message.channels`, and create the slash command `/incident`.
4. Install the app, then run it with tokens from the environment, never from a file in the repository:

```bash
SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... INCIDENT_STATUS_CHANNEL=C0123456789 \
  uv run freight-incidents serve
```

## Limits

- Company, people and runbooks are made up; runbooks cite the repository as their source.
- Anyone in an incident channel can run its commands; a real deployment would map commands to roles.
- Approvals happen through `approve` on the desk (the demo and tests call it); a Slack button for them is not built.
- Drafts are read in reference mode until a live run is recorded ([issue #10](https://github.com/aliazam2012/freight-agents/issues/10)).

## Where It Fits

Part of a family of freight agents on one [agent runtime](https://github.com/aliazam2012/agent-runtime), which holds the shared model layer, hooks, state engine and evaluation tools.
