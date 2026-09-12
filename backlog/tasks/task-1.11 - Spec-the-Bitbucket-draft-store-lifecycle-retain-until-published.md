---
id: TASK-1.11
title: Spec the Bitbucket draft-store lifecycle (retain-until-published)
status: Done
assignee:
  - christian.bonzelet
created_date: '2026-09-12 13:07'
updated_date: '2026-09-12 13:39'
labels:
  - 'wayfinder:grilling'
dependencies:
  - TASK-1.10
parent_task_id: TASK-1
ordinal: 12000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

TASK-1.10 fixed that the Bitbucket Publish bar reads the Sage-side draft via the app layer `POST /api/apps/code-review-sage/runs/{id}/post` (and a GET to read the pending draft). TASK-1.7 named the tension: the on-disk result record (`data/results/<id>.json` with `review_payload` + findings) already IS the "Sage-side draft" TASK-1.5 named — but `results.py` CLEARS records after a run, and a hold-for-publish draft must NOT be cleared until it is published.

DECIDE the draft lifecycle:
- WHEN the draft is computed and persisted (at run completion vs on-demand), and in what record shape (does `review_payload` from TASK-1.7's `build_review_payload` live in the result record as the canonical draft?).
- HOW LONG it is retained: what stops `results.py` from clearing an unpublished hold-for-publish record, and when a published/abandoned draft IS cleared (TTL? explicit discard? cleared on confirmed publish via the TASK-1.7 `published.json` reconciliation?).
- HOW the UI reads it pre-publish: the exact GET contract the reworked `DraftReviewActions` calls to render the pending draft before the user hits Publish, and how it reflects the TASK-1.7 idempotency state (already-posted markers → which findings show as new vs already-published on re-open).
- Interaction with the TASK-1.10 staleness guard: the draft record must carry the `revision` (source.commit.hash) it was computed against so the UI can detect PR-head drift.

Grill + domain-modeling. Blocked on TASK-1.10 (surface decision, done) and already-agree with TASK-1.7's poster/ledger contract.
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
created: 2026-09-12 13:39
---
RESOLVED — Bitbucket draft-store lifecycle (retain-until-published).

Ground truth that corrected the ticket's premise (read from sage_lib/results.py + review_driver.py, not assumed): the result record data/runs/<id>/results/<change_id>.json ALREADY is the Sage-side draft, `clear_results()` fires at the NEXT run's START (review_driver.py:1135, beside report.reset + clear_staged) — NOT at run end, so records already persist run-to-run — and posted state (posted_keys, pending_comments, posted_comments) lives INLINE on the record today, there is no published.json. So the real risk is not 'results.py clears after a run'; it is the next run's reset wiping an unpublished draft, plus head drift.

DECISIONS (5):

1. POSTED STATE STAYS INLINE ON THE RECORD — drop TASK-1.7's separate published.json. The record already survives run-to-run, is already what the GET reads and the poster writes back, and a sidecar adds a second thing to keep consistent (lost-update risk across Bitbucket's N sequential posts). Store `posted` map {marker_id -> {comment_id, posted_at, revision}} on the record. The hidden per-comment marker [code-review-sage:<path>#<rule>#<hash8>] in the live PR comment stays as the RECOVERY path if the record is lost (TASK-1.7's intent preserved), but the RECORD is the primary ledger, not a sidecar file.

2. RETAIN VIA A hold_for_publish FLAG + SKIP IN THE CLEAR SWEEP. clear_results skips records where hold_for_publish=true AND fully_posted=false. Flag set when a draft is computed for a publishable target. It clears (becomes sweep-eligible) only when every wanted finding's marker is in `posted` confirmed via _posts_confirmed read-back, OR on explicit user discard. No TTL — retention is bounded by publish/discard, not time.

3. PERSIST FINDINGS + revision AT RUN COMPLETION; COMPUTE THE {summary, inline} WORK LIST LAZILY (on GET/publish), not at run end. Findings + revision are the durable truth; the payload is a derived view TASK-1.7 already rebuilds each publish from findings + the diff, so freezing it at run end would only go stale against the idempotency logic. Record carries findings + revision + posted-map; payload is recomputed on demand.

4. PRE-PUBLISH GET CONTRACT: GET /api/apps/code-review-sage/runs/{id}/draft?change_id=<cid> -> {change_id, revision, hold_for_publish, findings[], summary, inline[], posted[], stale}. Each finding/inline comment carries its marker key and a status: 'new' | 'published' derived from the `posted` map. Pure READ, no side effects. The reworked DraftReviewActions renders new-vs-published from `status` and disables/warns on `stale`.

5. STALENESS: store revision (source.commit.hash, mapped by TASK-1.6) on the record at run completion; compute `stale` at GET time via one cheap Rovo executeRead on the live PR head, and RE-CHECK fail-closed at publish time (a draft can go stale between opening the panel and hitting Publish). On stale-at-publish: REFUSE and tell the user to re-run the review — never silently post against a moved head. This is the Sage-owned guard TASK-1.10 kept (off ReviewTarget revision, not the source layer's contentDigest).

Graduates TASK-1.12 (UI port spec): the draft-read GET contract it names is now fixed (decision 4).
---
<!-- COMMENTS:END -->
