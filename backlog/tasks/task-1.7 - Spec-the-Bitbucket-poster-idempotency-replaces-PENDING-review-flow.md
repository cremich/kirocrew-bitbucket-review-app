---
id: TASK-1.7
title: Spec the Bitbucket poster + idempotency (replaces PENDING-review flow)
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 11:05'
updated_date: '2026-09-12 12:14'
labels:
  - 'wayfinder:grilling'
dependencies:
  - TASK-1.5
  - TASK-1.6
parent_task_id: TASK-1
ordinal: 8000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Specify the Bitbucket poster mechanism and its idempotency story — the concrete equivalent of review_driver.py's PENDING-review poster block (:540-566) once TASK-1.5 fixes the posting model.

Cover: (a) the poster prompt/flow rewrite — replace the single `gh api .../reviews` (no event key) call with the chosen Bitbucket MCP sequence (addBitbucketRepoPullRequestComment per finding, and/or createBitbucketRepoPullRequestTask); (b) IDEMPOTENCY without GitHub's one-draft-per-author + marker-delete: re-running a review must not duplicate comments. Options to decide — list existing comments (listBitbucketRepoPullRequestComments) and match the `[code-review-sage]` marker to skip/update, keep a local posted-set in Sage's store keyed by (change_id, anchor, finding-hash), or both; (c) rate-limit handling for the sequential post loop (authed 1000-10000/hr, Rovo site cap — see TASK-1.4) — batching cadence, backoff; (d) the review_driver.py leak cleanup: cur["github_review_payload"] (:783,:859) and build_github_review_payload -> platform-neutral name.

Blocked on TASK-1.5 (defines what actually gets posted) and TASK-1.6 (the anchor data the poster needs).
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: sage-port-charting
created: 2026-09-12 12:13
---
RESOLUTION (1/2) — Bitbucket poster + idempotency spec. Ground truth read from source this session (review_driver.py, pipeline.py, results.py, adapters.py, store.py).

CORRECTION to a prior assumption on the map: GitHub's idempotency is NOT 'one-draft-per-author + marker-delete' alone. That marker-delete (build_post_task step 2) only clears a STALE PENDING draft so the re-POST does not 422. The REAL dedup is a durable per-finding ledger `posted_keys` on the result record, gated by a CONTENT read-back `_draft_confirmed` (review_driver.py:946) that re-reads the PR's PENDING reviews through the app's own `gh` chokepoint and matches body + commit_id + every inline {path,line,body}. Delivery evidence never comes from the poster's self-reported count. The Bitbucket poster MUST preserve that ledger+readback discipline; it is the load-bearing part, not the marker.

Two GitHub properties Bitbucket LOSES, which drive the redesign:
1. ATOMICITY: GitHub posts the whole review (body + all inline comments) in ONE POST .../reviews. `_draft_confirmed`'s all-or-nothing comment is literally true there. Bitbucket has NO batch: publish is N sequential executeWrite add-comment calls (TASK-1.5). A publish can now partially fail (comment 3 of 7 errors). The ledger MUST therefore be updated PER COMMIT, comment-by-comment, not once at the end — each landed comment's identity is recorded the moment its post is confirmed, so a resumed publish re-sends only the missing ones.
2. DELETE-AND-RESET: GitHub wipes its stale draft and recreates. Bitbucket comments are permanent-on-post, no author-draft to delete. So re-publish CANNOT reset; it must READ existing marker comments and skip/PUT-update. This is exactly the model TASK-1.5 chose.

=== (a) Poster prompt/flow rewrite (replaces build_post_task + build_github_review_payload) ===
GitHub today: Python assembles `github_review_payload` (build_github_review_payload), the LLM poster posts it VERBATIM via one `gh api` call, writes back posted_comments. Keep that division of labour: Python owns bodies+anchors+redaction+ledger, the LLM worker only calls MCP tools.

Rename build_github_review_payload -> build_review_payload (platform-neutral), and have it emit a Bitbucket-shaped WORK LIST instead of a single review envelope. Per TASK-1.5 the artifact is: ONE marker-bearing SUMMARY comment (canonical, always) + INLINE comments for 🔴/High only. So the payload becomes:
  {
    summary: {marker_id, body},                      # the ship-readiness comment (build_ship_comment), always present
    inline: [{marker_id, path, from, to, body}, ...]  # 🔴/High findings only
  }
Inline anchor derivation (handed off from TASK-1.6): Bitbucket inline keys are {path, from(old side), to(new side)} (+ start_from/start_to for ranges — TASK-1.4). Derive them in Python from finding.line + the matching files[].diff chunk (which the 1.6 parser already split per file): walk the unified-diff hunk headers (@@ -a,b +c,d @@) to decide whether finding.line lands on an added line (set `to`) or an unchanged/removed line (set `from`), matching Bitbucket's rule that a comment anchors to the new side for added lines and the old side otherwise. A finding whose line is NOT in the diff folds into the summary body (mirrors GitHub's unanchored-finding fold in build_review_payload), never dropped.

The new post prompt (build_post_task for platform=='bitbucket') instructs the worker: `discover` the Bitbucket tools, then for each work-list entry in order call executeWrite add-comment (summary first, then inline), passing body VERBATIM and the anchor for inline entries. It writes back per-comment delivery evidence (see (b)). It MUST NOT compose/edit bodies, MUST NOT submit/approve/merge, MUST NOT create PR tasks (advisory-only, V1).
---

author: sage-port-charting
created: 2026-09-12 12:14
---
RESOLUTION (2/3) — idempotency + rate limits.

=== (b) IDEMPOTENCY — marker + per-comment ledger + read-back (BOTH, they answer different failure modes) ===
1. Per-comment hidden MARKER (identity on the artifact). Per TASK-1.5: `[code-review-sage:<path>#<rule>#<hash8>]`, hash EXCLUDES exact line so line-drift after an edit does not resurrect a finding. Summary carries fixed `[code-review-sage:summary]`. _comment_body / build_ship_comment already append `_{DRAFT_MARKER}_`; for Bitbucket append the per-finding marker_id there. This identity survives even if Sage's local store is lost.
2. Durable LEDGER in Sage's store, mirroring posted_keys but Bitbucket-shaped. Key = bitbucket_review_key ('bitbucket.org/<ws>/<repo>#<id>', TASK-1.6) -> {posted:{marker_id: bitbucket_comment_id}, head_sha, published_at}. SEPARATE index from reviewed.json (call it published.json): reviewed.json = 'we looked', published.json = 'we posted these comment ids'. The comment_id is what enables PUT-update instead of duplicate-post.
3. READ-BACK gate (the _draft_confirmed analog, renamed _posts_confirmed). Before trusting the ledger — and to survive a lost store — the publish flow FIRST executeRead-lists PR comments, filters to marker-bearing bodies, parses marker_id + captures each bitbucket comment_id. This live set is AUTHORITATIVE and reconciles the ledger. Per draft entry:
   - marker present live AND body matches -> SKIP.
   - marker present live, body differs -> PUT-update that comment_id (executeWrite update-comment).
   - marker absent live -> POST add-comment; on confirm record {marker_id: new_comment_id} into the ledger IMMEDIATELY (per-commit, since Bitbucket publish is non-atomic).
Delivery evidence = the live read-back comment_id, NEVER the poster's self-reported count (preserves review_driver.py fail-closed: an unverifiable post leaves the ledger untouched so the next publish re-sends; a visible duplicate is recoverable, a dropped finding is not).
Stale findings (posted before, now fixed): V1 LEAVES them (TASK-1.5; auto-resolve is post-V1).

=== (c) Rate limits for the sequential publish loop ===
Context (TASK-1.4): authed /2.0 scaled 1000-10000/hr; Rovo site cap 500-10000/hr; whether Rovo STACKS its cap on REST is an open fog probe (not resolved here). A normal review = 1 summary + a few 🔴/High inline, well under any cap; risk is only a pathological large PR or 'review all' fan-out.
DECISION: (i) Keep the artifact small BY DESIGN — inline for 🔴/High only (TASK-1.5) already bounds N; everything else folds into the summary. This is the PRIMARY control. (ii) Sequential loop with a small fixed inter-call delay (~200-300ms) suffices for single-review publish; do NOT build elaborate batching. (iii) On 429/rate-limit from executeWrite: exponential backoff (1s,2s,4s, cap ~30s, few retries), then STOP leaving the ledger reflecting exactly what landed — read-back-reconciled resume makes re-publish safe. (iv) Publish is a human click (TASK-1.5), so publishes are naturally spaced; no autonomous high-frequency poster. Cross-review global throttling OUT for V1 — revisit only IF the stacking probe shows the cap bites.
---

author: sage-port-charting
created: 2026-09-12 12:14
---
RESOLUTION (3/3) — leak cleanup (d) + downstream.

=== (d) review_driver.py / pipeline.py leak cleanup (platform-neutral renames) ===
Concrete renames so no GitHub identifier reaches the shared poster path:
- build_github_review_payload (pipeline.py:444) -> build_review_payload(record, platform); github branch = today's {body,comments,commit_id} envelope, bitbucket branch = the {summary, inline} work list from (a). One neutral entry point, platform-branched inside.
- cur['github_review_payload'] (review_driver.py:783, :859) -> cur['review_payload']. The poster prompt reads 'review_payload' from data/results/<id>.json for EVERY platform; the SHAPE differs (github {body,comments,commit_id} / bitbucket {summary,inline}) but the KEY is neutral. review_payload_units branches: github = len(comments)+(1 if body); bitbucket = len(inline)+1 (summary always present).
- _draft_confirmed (:946) -> _posts_confirmed(link, payload, platform): github branch = today's PENDING read-back; bitbucket branch = the marker+comment_id live read-back from (b). Returns the confirmed identity set, not a boolean, same as today.
- POSTING_SPECS/posting_spec + FETCH_SPECS/fetch_spec are already platform-keyed dicts — add a 'bitbucket' entry, no rename. build_comment_payload's `raise ValueError(unsupported posting platform)` gains a bitbucket branch OR is bypassed (Bitbucket uses the work-list path, not the single-finding {path,line,side} shape).
- store.py comment 'PENDING (draft) review on the PR' (:110-112) config prose -> platform-neutral 'draft review, published on the PR'.

=== Downstream / graduated ===
- TASK-1.9 (website/ UI research, In Progress in a parallel session) OWNS the Publish button + draft-review panel. This ticket defines the BACKEND publish contract it binds to: a publish endpoint that (1) loads the record's draft review_payload, (2) runs the read-back reconcile from (b), (3) dispatches the poster, (4) writes published.json. The endpoint's ROUTE wiring is TASK-1.8's scope; named here so 1.9 has a contract.
- NEW FOG (added to map Not-yet-specified): the DRAFT STORE LIFECYCLE. TASK-1.5 said 'Sage holds the review as a draft in its store'; the on-disk record (data/results/<id>.json with review_payload + findings) already IS that draft, BUT results.py CLEARS records after a run — a hold-for-publish draft must NOT be cleared until published. When it is computed, how long retained, how the UI reads it pre-publish is unspecified. Sharp enough to ticket once TASK-1.9 maps what the UI reads; flag as fog, blocked on 1.9, do not ticket yet.
- Nothing newly ruled out of scope. PR-tasks poster path stays OUT (advisory-only, TASK-1.5; already on map Out-of-scope).

Acceptance: TASK-1.7 answers all four sub-questions (a poster flow, b idempotency, c rate limits, d leak cleanup) with named functions/files. No ticket invalidated; one fog patch graduated-adjacent (draft-store lifecycle) pending 1.9.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Bitbucket poster = platform-neutral build_review_payload emitting a {summary, inline} WORK LIST (one always-present marker-bearing summary comment + inline comments for 🔴/High only, TASK-1.5), replacing GitHub's single PENDING-review envelope. Inline {path, from, to} anchors derived in Python from finding.line + the 1.6 diff-split files[].diff (walk @@ hunk headers: added line -> `to`, else `from`); unanchored findings fold into the summary. Poster prompt: discover Bitbucket tools, then sequential executeWrite add-comment (summary first), bodies VERBATIM, no submit/merge/PR-tasks.

IDEMPOTENCY corrects a map assumption: GitHub's real dedup is NOT marker-delete alone but a durable per-comment ledger (posted_keys) gated by a CONTENT read-back (_draft_confirmed), and GitHub's one-POST atomicity makes it all-or-nothing. Bitbucket loses BOTH atomicity (N sequential posts, so ledger updates PER COMMIT) and delete-reset (comments permanent, so re-publish reads + skip/PUT-updates). Spec keeps BOTH: hidden per-comment marker `[code-review-sage:<path>#<rule>#<hash8>]` (line-drift tolerant, survives store loss) + a durable published.json ledger keyed by bitbucket_review_key mapping marker_id->bitbucket_comment_id (enables PUT-update). A live executeRead read-back (_posts_confirmed) is authoritative and reconciles the ledger; delivery evidence is the live comment_id, never the poster's self-count (fail-closed preserved). Rate limits: keep the artifact small by design (inline = 🔴/High only), ~200-300ms inter-call delay, exponential backoff on 429 then stop with the ledger reflecting exactly what landed. Renames: github_review_payload -> review_payload, build_github_review_payload -> build_review_payload(record, platform), _draft_confirmed -> _posts_confirmed. New fog: draft-store LIFECYCLE (results.py clears records post-run; a hold-for-publish draft must not be cleared) — blocked on TASK-1.9, not ticketed yet. Publish endpoint contract named for TASK-1.9's UI; route wiring is TASK-1.8.
<!-- SECTION:FINAL_SUMMARY:END -->
