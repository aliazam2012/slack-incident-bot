---
title: Runbook, TMS order API outage (made-up)
source_url: https://github.com/aliazam2012/slack-incident-bot/blob/main/src/freight_agents/incident_bot/docs/runbook-tms-outage.md
publisher: freight-agents, a made-up runbook for the demo network
read_on: 2026-11-24
---

A made-up runbook for the made-up network; it exists so the bot has something to cite.

## Symptoms

TMS order API calls time out or answer 5xx; tms-orders cases wait in validated; the TMS client's circuit breaker trips and its retry warnings climb.

## First checks

Confirm the TMS health endpoint, recent deploys, and the database connection pool. Check whether the breaker opened on a 429 (rate limit) or on 5xx answers.

## Mitigation

Hold new tenders; they wait safely in validated and resume on the next run without duplicates. Roll back a recent deploy if one lines up with the start. Do not replay tenders by hand: the idempotency keys already make the rerun safe.

## Customer impact

Shippers see delayed confirmations, not lost tenders. A customer update says confirmations are delayed and gives the next update time.
