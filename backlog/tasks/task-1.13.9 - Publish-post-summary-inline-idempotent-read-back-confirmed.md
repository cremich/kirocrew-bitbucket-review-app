---
id: TASK-1.13.9
title: 'Publish: post summary + inline, idempotent, read-back confirmed'
status: To Do
assignee: []
created_date: '2026-09-12 15:24'
labels:
  - ready-for-agent
dependencies:
  - TASK-1.13.8
parent_task_id: TASK-1.13
ordinal: 23000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

The keystone. A reviewer clicks Publish and the review lands on the PR: one always-present marker-bearing summary comment plus inline comments for the serious findings, posted one Rovo call at a time. Re-publishing is safe — Sage recognises what it already posted and updates instead of duplicating — and Sage never claims it posted something that did not land.

- `build_review_payload(record, 'bitbucket')` emits `{summary, inline}`: an always-present marker-bearing summary (canonical) plus inline comments for Critical/High findings only.
- Inline `{path, from, to}` anchors derived in Python by walking the diff-split hunk headers (added line → `to`, else `from`); findings that cannot be anchored fold into the summary so none is silently dropped.
- Marker `[code-review-sage:<path>#<rule>#<hash8>]` on every posted comment.
- `POST /runs/{id}/post` posts one Rovo call at a time.
- Idempotency keeps BOTH mechanisms (Bitbucket loses GitHub's atomicity and delete-reset): the inline `posted` map `{marker_id → {comment_id, posted_at, revision}}` on the record is the PRIMARY ledger; the hidden per-comment marker is the RECOVERY path, line-drift tolerant and store-loss tolerant.
- `_posts_confirmed` read-back is authoritative and reconciles the ledger; delivery evidence is ALWAYS the live `comment_id` from the read-back, NEVER the poster's self-count (fail-closed preserved).
- Re-publish reads existing marker comments and skips or updates rather than duplicating.
- Stale refusal: re-check the PR head fail-closed AT publish; if it moved since the draft, refuse and signal `target_stale` (never post against a moved head).
- Rate limits: small inter-call delay (~200–300 ms) and exponential backoff on 429, then stop.
- Keep the GitHub posting path; branch on platform.
- Regression proof: `test_learning.py` must stay green with NO edits — that green run is the evidence the port did not disturb the platform-clean learning subsystem. `test_no_auto_post.py` guards the never-auto-post invariant.

## Acceptance criteria

- [ ] Publish posts one marker-bearing summary + inline comments for Critical/High only
- [ ] Unanchored findings fold into the summary; none dropped
- [ ] Re-publish updates/skips existing Sage comments instead of duplicating
- [ ] Dedup survives line drift and loss of Sage's own store (marker recovery path)
- [ ] Delivery confirmed by read-back of the live comment id; never self-count
- [ ] Publish refused (`target_stale`) when the PR head moved since the draft
- [ ] 429s handled with backoff; a large review completes rather than failing halfway
- [ ] GitHub post path still works; `test_learning.py` green untouched
- [ ] Rovo transport faked at the boundary; tests assert request shape + derived behaviour, not tool names

## Blocked by

- TASK-1.13.8 (draft preview GET: the draft + staleness this publishes from).
<!-- SECTION:DESCRIPTION:END -->
