---
title: Runbook, carrier claims portal failing (made-up)
source_url: https://github.com/aliazam2012/slack-incident-bot/blob/main/src/freight_agents/incident_bot/docs/runbook-carrier-portal-down.md
publisher: freight-agents, a made-up runbook for the demo network
read_on: 2026-11-24
---

A made-up runbook for the made-up network.

## Symptoms

Portal filings fail: the claim form is missing a field, sign-in is refused, or no confirmation page appears. Claims wait in failed with the portal's reason.

## First checks

Open the carrier portal by hand and compare the form with the adapter's field list. A redesign shows up as a missing field before anything is submitted.

## Mitigation

Fix the adapter's field mapping, then retry the failed claims; each retry searches the portal's claim list first, so nothing is filed twice. File urgent claims by the carrier's claims email meanwhile.

## Customer impact

Claim filings are delayed; the 370.5 clock starts when the carrier receives the claim, so delays here do not cost the claimant time already running.
