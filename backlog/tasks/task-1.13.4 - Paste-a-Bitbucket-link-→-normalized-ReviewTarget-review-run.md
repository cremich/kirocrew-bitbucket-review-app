---
id: TASK-1.13.4
title: Paste a Bitbucket link → normalized ReviewTarget + review run
status: To Do
assignee: []
created_date: '2026-09-12 15:24'
labels:
  - ready-for-agent
dependencies:
  - TASK-1.13.2
parent_task_id: TASK-1.13
ordinal: 18000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

A reviewer pastes a `bitbucket.org/dflds/content.hub/pull-requests/N` link and Sage fetches the PR, normalizes it into the same review target the brain already uses, and runs a review — held as an internal draft, nothing posted. Review quality is identical to GitHub because the brain only ever sees a normalized `ReviewTarget`.

- `detect_platform` branches to Bitbucket on host `bitbucket.org` + `/pull-requests/` (hyphenated, plural); GitHub branch untouched. `bitbucket_pr_ref` → `(workspace, repo, id)`.
- Bitbucket single-PR fetch stays an LLM-instruction spec: discover the Rovo tools at runtime and call get-PR / get-diff / get-comments (mirrors GitHub's fetch=instruction / parse=deterministic split).
- `parse_bitbucket_payload(raw, *, link)` maps into the SAME 14-field `ReviewTarget`: `repo_identity=bitbucket.org/<ws>/<repo>`, `change_id` via `bitbucket_change_id`, author from nickname, target branch from destination branch, `revision`=source commit hash, description from summary. `linked_issue` via Jira-key regex `[A-Z][A-Z0-9]+-\d+`, else empty (soft field).
- `split_unified_diff(diff_text)` splits Bitbucket's ONE PR-wide diff into `files[]{path, diff}` on `diff --git` headers (Bitbucket has no per-file patch array).
- Helpers: `bitbucket_change_id(ws,repo,id)` → `BB-<ws>-<repo>-<id>` (sanitized); `bitbucket_review_key` → `bitbucket.org/<ws>/<repo>#<id>` (lossless). Route `normalize()` to the Bitbucket parser.
- Guard: a link outside the configured allowlist is rejected before any fetch.

## Acceptance criteria

- [ ] Pasting a configured Bitbucket PR link runs a full review, held as a draft, nothing posted
- [ ] `detect_platform` routes Bitbucket links; GitHub links still route to GitHub
- [ ] `parse_bitbucket_payload` produces the same 14-field `ReviewTarget` shape as GitHub
- [ ] `split_unified_diff` splits a PR-wide diff into per-file diffs on `diff --git`
- [ ] A link outside the allowlist is rejected before fetch
- [ ] Rovo transport faked at the transport boundary in tests (no Python REST client, no asserting discovered tool names)

## Blocked by

- TASK-1.13.2 (fail-closed config + `allowed_targets`).
<!-- SECTION:DESCRIPTION:END -->
