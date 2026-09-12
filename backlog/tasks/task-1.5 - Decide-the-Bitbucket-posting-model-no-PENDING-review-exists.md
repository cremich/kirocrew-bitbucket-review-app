---
id: TASK-1.5
title: Decide the Bitbucket posting model (no PENDING review exists)
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 11:04'
updated_date: '2026-09-12 12:00'
labels:
  - 'wayfinder:grilling'
dependencies: []
parent_task_id: TASK-1
ordinal: 6000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

GitHub's PENDING review lets Sage stage many inline comments + a body as one author-owned draft that a HUMAN submits later, with delete-and-recreate dedup by the `[code-review-sage]` body marker (see TASK-1.2). Bitbucket Cloud has NO equivalent: every comment posts immediately, one MCP call each, no atomic multi-comment submit, no staging object (see TASK-1.4). This breaks the core UX contract — a reviewer no longer gets a single draft to review-then-submit.

Decide the replacement posting model. Candidate directions to grill against desired reviewer UX:
1. Post every finding immediately as an inline comment (fire-and-forget; loses the human-submit gate).
2. Accumulate all findings into ONE summary comment (keeps a single artifact, loses inline anchoring).
3. Use Bitbucket PR tasks (createBitbucketRepoPullRequestTask) as the checklist, comments for detail.
4. Gate posting behind a human "publish" action in Sage's own UI — Sage holds the draft internally (in its store), the human reviews in-app, then one click fires the sequential post loop. This relocates the "staging" from GitHub's server to Sage itself.

Resolve via grilling + domain-modeling on what reviewer experience we actually want, then name the chosen model, its dedup/idempotency story (re-running a review must not duplicate comments — Bitbucket has no marker-delete-review equivalent, so how?), and how it maps onto the Rovo MCP ops. This is the keystone: fetch-adapter shape, identity model, and UI port all take their cues from it.
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: sage-port-charting
created: 2026-09-12 12:00
---
RESOLUTION — Chosen posting model: **Sage-side staged review** (the ticket's option 4, "relocate the gate into Sage").

**Why not the alternatives:** A research subagent refined TASK-1.4's blanket claim. Bitbucket Cloud DOES have a native pending-comment concept — a documented `pending` boolean on the PR comment object, backing the UI's Start-review → Finish-review batch flow. BUT there is no documented API to publish a staged batch, and our decided transport (Rovo MCP) exposes only immediate add-comment. So platform-native staging (candidate C) can't be driven through our transport and is ruled OUT of V1. Posting immediately and dropping the human gate (candidates 1/2) throws away Sage's core value — a staged, human-approved review. So the staging moves into Sage.

**The model:**
- Sage computes the review and holds it as a DRAFT in its own store. Nothing touches Bitbucket until a human clicks Publish in Sage's UI. This relocates GitHub's server-side PENDING staging into Sage.
- Artifact shape (hybrid): ONE marker-bearing **summary comment** is the canonical review artifact — always posted, lists every finding with `path:line` refs. **Inline comments** only for Critical/High findings, anchored by Bitbucket's {path, from/to} keys. Caps MCP call count, keeps the review readable.
- Publish = a sequential Rovo `executeWrite` add-comment loop (summary first, then inline). No atomic batch needed.
- **Advisory only** in V1 — no merge-blocking PR tasks. Kept as a post-V1 option.
- Identity: post under the user's Atlassian/Rovo identity; ownership tracked by the `[code-review-sage]` body marker, no dedicated account (ports unchanged from GitHub).

**Dedup / idempotency** (Bitbucket has no delete-and-recreate-review):
- Finding identity = `path + rule-id + short content-hash`, deliberately EXCLUDING the exact line so a finding that only drifted down a few lines after a fix isn't resurrected. Embed it as a hidden marker per comment, e.g. `[code-review-sage:<path>#<rule>#<hash8>]`.
- On (re-)publish: Sage first `executeRead`-lists PR comments, filters to marker-bearing ones, parses their identities. Per draft finding — identity present → skip (or PUT-update if body changed); absent → post.
- The summary comment is a single marker-bearing comment: find-by-marker and PUT-update on re-run, never repost.
- Stale findings (previously posted, now fixed): V1 leaves them; auto-resolve/delete is a post-V1 nice-to-have.

**Rovo MCP op mapping:** list existing = `executeRead`; post inline = `executeWrite` add-comment with inline anchor; post/update summary = `executeWrite` add/update (PUT) comment. No start-review/finish-review dependency — staging is entirely Sage-internal.

**Downstream effects on the map:**
- TASK-1.7 (poster + idempotency) scope now explicitly includes the Sage-side draft store + publish endpoint + the marker-based dedup loop above.
- The external `website/` UI definitively needs a draft-review panel + Publish control → graduated as TASK-1.9 (research).
- Native pending staging (C) kept as a post-V1 fog/probe item, not the V1 path.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Chose Sage-side staged review: Sage holds the review as an internal draft, a human clicks Publish in Sage's UI, and Sage fires a sequential Rovo add-comment loop. Artifact = one marker-bearing summary comment (canonical) + inline comments for Critical/High only. Advisory in V1 (no merge-block). Dedup identity = path+rule+content-hash (line-drift tolerant), embedded as a hidden per-comment marker; re-publish reads existing marker comments and skips/PUT-updates. Native Bitbucket pending staging ruled out of V1 (Rovo drives immediate add-comment only). Poster/store/publish-endpoint fold into TASK-1.7; UI publish surface graduated to TASK-1.9.
<!-- SECTION:FINAL_SUMMARY:END -->
