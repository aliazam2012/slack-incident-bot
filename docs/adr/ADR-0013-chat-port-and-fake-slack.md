# ADR-0013: A chat port, a fake Slack, and a contract test between them

Status: accepted 2026-11-24.

## Table of Contents

- [Context](#context)
- [Decision](#decision)
- [Consequences](#consequences)

---

## Context

incident-bot lives in Slack, but CI has no workspace, no tokens and no network, and a test that needs a real workspace is a test nobody runs. A fake that drifts from Slack's behavior is worse: the tests pass and the bot breaks.

## Decision

The bot talks to chat through a `ChatPort` with three operations it needs: post in a channel or thread, read a channel's history, create a channel. `FakeChat` implements it in memory and records every post with its channel, which is how the tests and the evaluation count posts outside the incident channel. `SlackChat` implements it with slack_sdk's Web API client. One contract test runs the same checks against both, with `SlackChat` pointed at a local stub of the Slack Web API served over HTTP, so the fake's ordering, threading and channel creation match the adapter's. The Bolt app maps `/incident` and channel messages onto the desk and is tested by dispatching Bolt requests to it with the same stub; a real workspace runs it under Socket Mode with tokens from the environment.

## Consequences

Every incident behavior, from cadence reminders to approvals, is tested and evaluated without Slack. What the contract test cannot cover is Slack itself changing; a smoke run in a real workspace before a release covers that. The port stays small on purpose: a new Slack feature the bot needs means a new port operation, a fake behavior and a contract check.

The fake runs everything on one thread; a real workspace does not. Bolt handles each event on a worker thread and `serve` fires reminders on another, so the stores share their connection under a lock, the desk runs its entry points one at a time, and tests drive the stores and the desk from worker threads at once. The self-review before this pull request merged found that gap: the one-thread tests had passed while `serve` could not have run.
