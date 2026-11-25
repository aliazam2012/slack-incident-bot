---
title: Runbook, EDI 204 tender feed down (made-up)
source_url: https://github.com/aliazam2012/slack-incident-bot/blob/main/src/freight_agents/incident_bot/docs/runbook-edi-feed-down.md
publisher: freight-agents, a made-up runbook for the demo network
read_on: 2026-11-24
---

A made-up runbook for the made-up network.

## Symptoms

No EDI 204 tenders arrive from a trading partner for longer than its usual gap, or files arrive and fail envelope checks.

## First checks

Look at the last interchange control number received and the parser's errors: a control-number mismatch or a segment count names the broken segment. Ask the partner whether their side changed.

## Mitigation

Ask the partner to resend from the last good control number; resends map to existing orders, so nothing is created twice. Accept tenders by email or PDF meanwhile.

## Customer impact

The partner's tenders are delayed; a customer update to that partner names the alternative channel.
