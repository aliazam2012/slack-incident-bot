---
title: Managing Incidents (Google SRE book, chapter 14)
source_url: https://sre.google/sre-book/managing-incidents/
publisher: Google, Site Reliability Engineering (O'Reilly, 2017), CC BY-NC-ND 4.0; written by Andrew Stribblehill, edited by Kavita Guliani
read_on: 2026-11-24
---

Plain-language summary of the chapter.

## Roles

Incident command holds the high-level state of the incident and assigns responsibilities; the commander holds every role not delegated. The operations lead applies changes to the system, and the operations team should be the only group modifying it during the incident. The communication lead is the public face of the response and issues periodic updates to the team and stakeholders. Planning handles longer-term work: bugs, handoffs, and tracking how the system diverged from normal.

## Living incident document

The commander's most important job is to keep a living incident document that several people can edit at once, with the most important information at the top. It may be messy but must work, and it is kept for the postmortem.

## Clear handoff

The commander role is handed off explicitly, with a firm acknowledgement from the new commander, and the handoff is announced so everyone knows who leads at all times.

## When to declare

Declare early. The chapter's guidelines: declare an incident if a second team is needed to fix the problem, if the outage is visible to customers, or if the issue is unsolved after an hour of concentrated analysis.
