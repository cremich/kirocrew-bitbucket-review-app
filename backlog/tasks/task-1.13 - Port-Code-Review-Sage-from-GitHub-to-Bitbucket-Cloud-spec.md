---
id: TASK-1.13
title: Port Code Review Sage from GitHub to Bitbucket Cloud (spec)
status: In Progress
assignee:
  - kirocrew
created_date: '2026-09-12 14:19'
updated_date: '2026-09-12 14:25'
labels:
  - ready-for-agent
  - spec
dependencies: []
parent_task_id: TASK-1
ordinal: 14000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Synthesized from the fully-resolved wayfinder map (TASK-1, decisions 1.1–1.12). No interview — this folds every resolved decision into one executable spec. Full User Stories, Implementation Decisions, and Testing Decisions are in this task's Implementation Plan and Notes (the description field caps at 10k chars). Scope: Bitbucket Cloud only (workspace `dflds`, repo `content.hub`); Data Center/Server out of scope for V1.

## Problem Statement

Code Review Sage reviews pull requests and posts findings back as review comments, but it only speaks GitHub. Our PRs live on Bitbucket Cloud (`dflds/content.hub`). Today Sage cannot fetch a Bitbucket PR, cannot post to it, and its UI only accepts `github.com/.../pull/N` links. We want the same review-and-publish experience on Bitbucket without a second tool or a bot account.

## Solution

Sage gains a Bitbucket adapter and a Bitbucket-shaped posting model, using the Atlassian Rovo MCP as transport (no App Password, no separate REST client). A reviewer pastes a Bitbucket PR link, Sage fetches and reviews it exactly as it does GitHub PRs, then holds the result as an internal draft. The reviewer opens the draft in Sage's UI, previews the comments, and clicks Publish; Sage posts a marker-bearing summary comment plus inline comments for the serious findings, one Rovo call at a time. Re-publishing is safe — Sage recognises what it already posted and updates instead of duplicating. Approve / Request-changes fire from Sage directly as separate buttons, with a confirm dialog stating undo needs the Bitbucket web UI.

## Seam (test surface)

Highest single seam is the existing adapter boundary: the brain only ever sees a normalized `ReviewTarget`. All new behavior is testable at existing seams — no new seam introduced. Prior art already present: `tests/test_adapters.py`, `test_pipeline.py`, `test_review_driver.py`, `test_post_comments.py`, `test_backend_routes.py`, `test_run_endpoints.py`, `test_no_auto_post.py`, `test_learning.py`.

## Out of Scope

- The React UI port lives in `kirodotdev/KiroCrew`'s external `website/` tree — cross-repo dependency, documented for the UI owner, NOT executed or verified from this repo. A coding agent here builds only the Python backend + its tests.
- Bitbucket Data Center / Server (Cloud only for V1).
- Merge-block enforcement (verdicts do not gate merges).
- Un-approve / withdraw of a verdict — Rovo exposes no such op; undo is the Bitbucket web UI.
- Native Bitbucket pending/Start-review batch staging — not drivable through Rovo; Sage-side staging replaces it.
- A dedicated bot account or identity port — ownership stays marker-based; Sage posts as the Rovo token user.

## Further Notes

- Renames to kill the GitHub leak in shared code: `github_review_payload`→`review_payload`, `build_github_review_payload`→`build_review_payload`, `_draft_confirmed`→`_posts_confirmed`.
- Security invariant preserved: delivery evidence is ALWAYS the live comment id from a read-back, never the poster's self-count; idempotency keeps both the durable inline ledger and the hidden-marker recovery path, fail-closed.
- Map correction the code forced: the premise that results are cleared after a run was wrong — `clear_results()` runs at the NEXT run's start, which is why the record can serve as the persistent draft.
- Route wiring for new endpoints folds into TASK-1.8's config/discovery scope.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
## User Stories

1. As a reviewer, I want to paste a `bitbucket.org/dflds/content.hub/pull-requests/N` link into Sage, so that I can start a review the same way I do for GitHub.
2. As a reviewer, I want Sage to reject a link that is not a configured Bitbucket target, so that I never accidentally review or post to the wrong repo.
3. As a reviewer, I want Sage to fetch the PR's metadata and full diff from Bitbucket, so that the review runs against the real change.
4. As a reviewer, I want the Bitbucket PR mapped into the same normalized review target the brain already uses, so that review quality is identical to GitHub.
5. As a reviewer, I want Sage to split Bitbucket's single PR-wide diff into per-file diffs, so that inline findings can be anchored to the right file and line.
6. As a reviewer, I want Sage to hold my review as an internal draft after the run, so that nothing is posted to the PR until I decide.
7. As a reviewer, I want to open the draft in Sage's UI and see every finding with its proposed comment, so that I can preview before publishing.
8. As a reviewer, I want each draft item labelled new vs already-published, so that I can tell what a re-publish will actually do.
9. As a reviewer, I want to click Publish and have Sage post one summary comment plus inline comments for Critical/High findings, so that the author sees the review on the PR.
10. As a reviewer, I want Sage to post as whatever user the Rovo token authenticates as, so that no dedicated bot account or extra identity setup is needed.
11. As a reviewer, I want a re-publish to update the existing Sage comments rather than duplicate them, so that the PR thread stays clean across multiple runs.
12. As a reviewer, I want Sage's dedup to survive line drift and even loss of Sage's own store, so that idempotency holds even if the PR moved or Sage restarted.
13. As a reviewer, I want delivery confirmed by reading the comment back from Bitbucket, so that Sage never claims it posted something that did not land.
14. As a reviewer, I want Publish refused if the PR head moved since the draft was computed, so that I never post a review against code that changed under me.
15. As a reviewer, I want a stale badge on the draft when the PR head has moved, so that I know to re-run before publishing.
16. As a reviewer, I want to Approve the PR from Sage, so that I don't have to switch to the Bitbucket web UI to record my verdict.
17. As a reviewer, I want to Request changes on the PR from Sage, so that my verdict is recorded alongside my comments.
18. As a reviewer, I want verdict buttons separate from Publish, so that I never fire a one-way verdict by accident while just posting comments.
19. As a reviewer, I want a confirm dialog before a verdict fires, stating that undo requires the Bitbucket web UI, so that I understand the action is one-way.
20. As a reviewer, I want the add-PR input, run labels, and source links to recognise Bitbucket PR URLs, so that the whole UI reads correctly for Bitbucket work.
21. As an operator, I want to configure the allowed Bitbucket targets as workspace/repo pairs, so that Sage only ever touches repos I approved.
22. As an operator, I want an unconfigured Sage to fail closed, so that there is no unsafe default target.
23. As an operator, I want the app manifest to declare the Rovo/Atlassian dependency instead of the `gh` CLI, so that install requirements are accurate.
24. As a reviewer, I want the personal GitHub feed pickers (my repos, contributed repos, recent repos) dropped, so that the UI does not surface GitHub-only surfaces with no Bitbucket equivalent.
25. As a reviewer, I want Sage to list open PRs for a configured Bitbucket repo, so that I can pick a PR without pasting a link.
26. As a reviewer, I want the learning subsystem to keep working unchanged, so that Sage keeps improving across reviews regardless of platform.
27. As a reviewer, I want findings that cannot be anchored to a diff line to fold into the summary comment, so that no finding is silently dropped.
28. As a reviewer, I want Publish to be resilient to Bitbucket rate limits, so that a large review still completes instead of failing halfway.

## Execution Plan (recorded by kirocrew after reading the code)

Grounded in adapters.py, pipeline.py, store.py, review_driver.py, results.py, discovery.py, backend/routes.py, app.json, test_adapters.py + tests/fixtures.py conventions. Build the Python backend only (UI out of local scope). Work in dependency order; run the relevant test module after each module.

### Step 1 - Config (store.py)
Add `bitbucket_repos: []` to DEFAULT_CONFIG (NO safe default -> fail closed). Keep `github_hosts` (GitHub path stays working; port is additive). Add `DEFAULT_BITBUCKET_REPOS = []`.

### Step 2 - Adapter (adapters.py) + test_adapters.py
- detect_platform: branch to 'bitbucket' on host bitbucket.org + '/pull-requests/'; keep GitHub branch. Add bitbucket_pr_ref -> (ws, repo, id).
- allowed_targets(config) -> frozenset of (ws, repo) pairs from bitbucket_repos; fail-closed empty when unconfigured.
- parse_bitbucket_payload(raw, *, link) -> same 14-field ReviewTarget. repo_identity=bitbucket.org/<ws>/<repo>, change_id via bitbucket_change_id, author=nickname, target_branch=destination.branch.name, revision=source.commit.hash, description=summary. linked_issue via Jira-key regex [A-Z][A-Z0-9]+-\d+.
- split_unified_diff(diff_text) -> files[]{path,diff} split on 'diff --git' headers.
- bitbucket_change_id(ws,repo,id)->BB-<ws>-<repo>-<id> (sanitized); bitbucket_review_key->bitbucket.org/<ws>/<repo>#<id> (lossless). Route normalize() to bitbucket parser.

### Step 3 - Pipeline (pipeline.py) + test_pipeline.py
- Rename build_github_review_payload->build_review_payload(record, platform); GitHub branch keeps pending-review payload; bitbucket branch produces {summary, inline[]}.
- Add bitbucket entries to FETCH_SPECS + POSTING_SPECS (LLM-instruction: discover Rovo tools, get-PR/get-diff/get-comments; post summary + inline).
- Bitbucket inline anchoring from files[].diff hunk headers (added->to, else from); unanchored fold into summary. Inline for red/High only; summary always present, marker-bearing. Marker [code-review-sage:<path>#<rule>#<hash8>] helper.

### Step 4 - Poster/idempotency (review_driver.py) + test_review_driver.py, test_post_comments.py
- Rename _draft_confirmed->_posts_confirmed.
- Bitbucket posting: posted map {marker_id->{comment_id,posted_at,revision}} inline (primary ledger); hidden marker recovery; read-back confirmation = live comment_id (fail-closed, never self-count); re-publish skip/update; stale-at-publish refusal. Keep GitHub path; branch on platform.

### Step 5 - Draft store lifecycle (results.py/review_driver.py)
hold_for_publish flag; clear sweep skips hold_for_publish=true AND fully_posted=false; clears on full read-back or discard; no TTL. Persist findings+revision at completion; compute {summary, inline} lazily on GET/publish.

### Step 6 - Config/discovery/routes + test_backend_routes.py, test_run_endpoints.py, test_discovery.py, test_user_repos.py
allowed_targets() fail-closed guard in driver. Drop list_user_repos/list_contributed_repos + /my-repos /recent-repos routes. Keep list-open-PRs rebuilt on Rovo listBitbucketRepoPullRequests scoped to configured repo. /repos CRUD manages bitbucket_repos. New endpoints: draft GET /runs/{id}/draft, /runs/{id}/post (+target_stale), /approve, /request-changes.

### Step 7 - Manifest (app.json)
Drop gh from dependencies.commands; rewrite GitHub prose -> Bitbucket/Rovo; add config-schema note for bitbucket_repos.

### Step 8 - Verify
Run full test suite. test_learning.py must stay green untouched. Fix rename breakage (grep callers before/after). Clean scratch.

Note: GitHub posted_keys/posting_expected ledger stays; bitbucket adds posted map. Rovo transport = LLM-instruction fetch/post specs, faked at transport boundary in tests, never a Python REST client.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Implementation Decisions

**Architecture / seam.** The review brain only ever sees a normalized `ReviewTarget`. Adding Bitbucket is a new adapter plus config, not a brain change. All coupling lives in four places: the adapter module (parse + identity), the discovery module (`gh` plumbing), three dicts in the pipeline module (fetch specs, posting specs, list-open-PRs), and the poster/confirm blocks in the review driver. Learning and store data model are already platform-clean and port untouched.

**Fetch (Req 3–5).** Single-PR fetch stays an LLM-instruction string keyed by platform — the Bitbucket fetch spec tells the worker to `discover` the Rovo tools at runtime and call get-PR / get-diff / get-comments — mirroring GitHub's fetch=instruction / parse=deterministic split. A new deterministic `parse_bitbucket_payload` maps into the SAME 14-field `ReviewTarget`: `repo_identity='bitbucket.org/dflds/content.hub'`, `change_id=BB-<ws>-<repo>-<id>`, description from summary, author from nickname, target branch from destination branch, `revision`=source commit hash. Largest new code is a unified-diff splitter: Bitbucket returns ONE PR-wide diff (no per-file patch array), split on `diff --git` headers into `files[]{path, diff}`. `detect_platform` routes on host `bitbucket.org` plus `/pull-requests/` (hyphenated, plural). `linked_issue` repurposed to Jira keys (`[A-Z][A-Z0-9]+-\d+`), else empty — soft field. New `bitbucket_change_id` + `bitbucket_review_key` helpers mirror the GitHub ones. NO new `ReviewTarget` field — inline anchors derived by the poster from `files[].diff`.

**Transport.** Atlassian Rovo MCP v2. Tool names vary across Atlassian docs, so resolve via `discover` at runtime; the Rovo token's user is the posting identity. Verified live: `approveBitbucketRepoPullRequest` and `requestChangesOnBitbucketRepoPullRequest` (both executeWrite, args `{workspaceId, repoId, prId}`, no verdict/body field). Load-bearing constraint: Rovo exposes NO un-approve/withdraw op and NO reviewer-status read op — verdicts are one-way from Sage.

**Posting model — THE KEYSTONE (Req 9, 11–13).** Bitbucket Cloud has no usable draft/pending-review batch through our transport, so Sage-side staging replaces GitHub's PENDING review. A platform-neutral `build_review_payload(record, platform)` emits a Bitbucket work list `{summary, inline}`: one always-present marker-bearing summary comment (canonical) plus inline comments for Critical/High only. Inline `{path, from, to}` anchors derived in Python by walking the diff-split hunk headers (added line → `to`, else `from`); unanchored findings fold into the summary.

Idempotency keeps BOTH mechanisms because Bitbucket loses both of GitHub's guarantees — atomicity (N sequential posts, ledger updates per commit) and delete-reset (comments permanent):
- Hidden per-comment marker `[code-review-sage:<path>#<rule>#<hash8>]`, line-drift tolerant, survives store loss — the RECOVERY path.
- Posted state stored INLINE on the result record as a `posted` map `{marker_id → {comment_id, posted_at, revision}}` — the PRIMARY ledger (no separate `published.json`).

A live read-back (`_posts_confirmed`) is authoritative and reconciles the ledger; delivery evidence is the live `comment_id`, never the poster's self-count (fail-closed preserved). Re-publish reads existing marker comments and skips or updates. Rate limits handled by a small artifact by design, a ~200–300 ms inter-call delay, and exponential backoff on 429 then stop.

**Config / identity / discovery (Req 21–25).** Replace the `github_hosts` allowlist with `bitbucket_repos: [{workspace, repo}]` in the store's default config — NO safe default, so unconfigured Sage fails closed. `allowed_hosts()` becomes `allowed_targets()` returning `(workspace, repo)` pairs; the review-driver fail-closed guard revalidates target-in-scope. Drop the personal gh-feed pickers (list-user-repos, list-contributed-repos) and their `/recent-repos` + `/my-repos` routes; keep only list-open-PRs, rebuilt on Rovo `listBitbucketRepoPullRequests` scoped to a configured repo. `/repos` CRUD manages the config list. Manifest: drop the `gh` dependency, rewrite the GitHub prose, add a required config-schema block for `bitbucket_repos`.

**Draft-store lifecycle (Req 6–8, 14–15).** The result record IS the Sage draft; `clear_results()` fires at the NEXT run's START, not at run end, so records already persist run-to-run. Persist findings + `revision` at run completion; compute the `{summary, inline}` work list LAZILY on GET/publish (payload is derived, rebuilt each publish). Retain unpublished drafts via a `hold_for_publish` flag — the clear sweep skips records where `hold_for_publish=true AND fully_posted=false`; the flag clears only when every wanted marker is confirmed via read-back or on explicit discard; no TTL. Pre-publish read is a draft GET returning `{change_id, revision, hold_for_publish, findings[], summary, inline[], posted[], stale}`, each item carrying its marker key and `status: new|published`; pure read. Staleness: store `revision` at completion, compute `stale` at GET via one Rovo read on the live PR head, RE-CHECK fail-closed at publish (refuse and tell user to re-run — never post against a moved head).

**Backend routes / API contracts (Req 9, 16–17).** App-layer publish endpoint `/api/apps/code-review-sage/runs/{id}/post` (unchanged shape plus a `target_stale` signal). New draft GET (above). New `/approve` and `/request-changes` endpoints wrapping the Rovo executeWrite verdict ops. The Publish bar drives the APP layer, not a faked platform-source layer — no Bitbucket source-provider is written (source layer stays GitHub-only; Bitbucket bypasses it). 8 of ~20 routes currently assume GitHub; the GitHub-specific feed routes are removed rather than ported.

**UI port — lives in the external `website/` tree in `kirodotdev/KiroCrew`, NOT this repo (Req 7–8, 16–20).** A PORT, not net-new: the draft-review + Publish bar already exists. Coupling is narrow — four regex parsers (`repoOfRun`, `sageSourceLink`, `prLabelFromChange`, `prRefFromChange`) edited to accept `bitbucket.org/.../pull-requests/N`; no URL builder to touch (repo props take an `owner/repo` string; backend builds the URL). The app-layer post IS the publish on Bitbucket, so the two shipping publish paths collapse: re-point `DraftReviewActions` at the app layer (draft GET + `/post`), keep preview + irreversibility warning, drop the GitHub PENDING guards (`contentDigest`/`supersededDraft`/`expectedReviewId`/`autoMerge`), render new-vs-published off the GET's `status`, disable on `stale`, staleness now Sage-owned off `revision`. Verdict was a SELECTOR fused into the GitHub single-POST submit; on Bitbucket comments and verdict are DIFFERENT endpoints, so split it into a new sibling `VerdictActions` component — discrete Approve / Request-changes, confirm-before-fire, "undo needs Bitbucket web UI" — never fused with Publish. `sageSourceLink` returns null for Bitbucket intentionally (no source-provider impl).

## Testing Decisions

A good test asserts EXTERNAL behavior at the module's public seam, not internal wiring: given a Bitbucket PR link or payload in, assert the normalized `ReviewTarget` / work list / route response out. Do not assert private helper calls or Rovo tool names (discovered at runtime, will drift). Rovo calls are faked at the transport boundary; assert the request shape Sage builds and the behavior it derives from a canned response, not the network.

Modules tested and existing prior art in `tests/`:
- Adapter: `detect_platform`, `parse_bitbucket_payload`, the diff splitter, `bitbucket_change_id`/`bitbucket_review_key` — extend `test_adapters.py` (covers GitHub parse + identity + `detect_platform`).
- Pipeline: Bitbucket fetch/posting specs and `build_review_payload(record, 'bitbucket')` emitting `{summary, inline}` with correct inline anchoring and summary-fold of unanchored findings — extend `test_pipeline.py`.
- Poster / idempotency: marker + inline-ledger dedup, read-back confirmation, re-publish skip/update, fail-closed on unconfirmed delivery, stale-at-publish refusal — extend `test_review_driver.py` and `test_post_comments.py`; `test_no_auto_post.py` guards the never-auto-post invariant.
- Config / discovery: `allowed_targets()` fail-closed on unconfigured, target-in-scope guard, list-open-PRs on Rovo, dropped feed routes gone — extend `test_backend_routes.py`, `test_run_endpoints.py`, `test_discovery.py`; `test_user_repos.py` shrinks as personal pickers are removed.
- Draft store: `hold_for_publish` survives the clear sweep, draft GET shape + `status: new|published`, lazy payload compute, staleness badge — extend `test_review_driver.py` / `test_run_endpoints.py`.
- Learning: unchanged; `test_learning.py` should stay green with no edits — that green run is itself the regression proof the port did not disturb the platform-clean subsystem.

UI tests are out of local scope (see Out of Scope in the description).
<!-- SECTION:NOTES:END -->
