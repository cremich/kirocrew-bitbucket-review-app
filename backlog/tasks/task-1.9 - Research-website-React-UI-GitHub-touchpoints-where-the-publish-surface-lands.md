---
id: TASK-1.9
title: >-
  Research: website/ React UI GitHub touchpoints + where the publish surface
  lands
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 12:00'
updated_date: '2026-09-12 12:14'
labels:
  - 'wayfinder:research'
dependencies: []
parent_task_id: TASK-1
ordinal: 10000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

The posting-model decision (TASK-1.5) chose **Sage-side staged review**: Sage holds the review as a draft in its own store and a human clicks **Publish** in Sage's UI to fire the post loop. That means the UI must render a draft-review panel + Publish control — a surface GitHub Sage never had. But the UI is NOT in this repo; it lives in an external `website/` tree (`website/src/apps/code-review-sage/CodeReviewSagePage.tsx`, per TASK-1.3).

Map the external `website/` UI so its port can be specced:
1. Every github.com / GitHub-specific touchpoint in the Code Review Sage page(s): URL builders, "GitHub Enterprise can't be pinned" strings, `setup_required` for the `gh` CLI, any PR-link construction.
2. Where a **draft-review panel + Publish button** would land in the current component tree, and what backend contract it needs (list draft findings, publish, show post progress).
3. What the UI currently assumes about the posting model (does it show anything about PENDING reviews / submit state today?).

AFK research: read the external `website/` tree (different repo — locate it first). Capture findings as the resolution; do not edit the website repo. This unblocks a later UI-port spec decision, which will also depend on TASK-1.7's poster contract.
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: sage-port-charting
created: 2026-09-12 12:14
---
## Resolution — website/ UI is a PORT, not net-new; a source-abstraction publish contract already exists

**Source confidence: HIGH.** Read from the compiled bundle `CodeReviewSagePage-1dwUxzbo.js` (on disk), cross-checked against `kirodotdev/KiroCrew@HEAD` source (`CodeReviewSagePage.tsx`, `api.ts`, `api/client.ts`, `components/DraftReviewActions.tsx`, `utils/pullRequestErrors.ts`) via authenticated `gh api`. Sources agree. **No `website/` tree or `.tsx` exists on this machine** — only compiled assets. Real source path confirmed: `website/src/apps/code-review-sage/CodeReviewSagePage.tsx` with a full `components/` dir.

### Q1 — GitHub touchpoints (narrow, concrete)
Hardcoded github.com lives in just TWO places in the app bundle, and both feed real backend calls:
- `Ct(e)` builds `https://github.com/${owner}/${repo}` and that URL is the repo identity sent to `repoPrs()` and `reviewRepo()`. **The load-bearing host assumption.**
- Add-PR input parser accepts GitHub only: `/^https:\/\/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)$/` → `{provider:'github'}`. A pasted Bitbucket URL is rejected. Bare `owner/repo` also defaults to a github.com host.

Already provider-aware (NOT github-only): display parsers `Ue`/`Ge` handle `pull|merge_requests` and the GitLab `-/` segment; the bundle carries a `gitlab` literal. There is **NO** "GitHub Enterprise can't be pinned" string and **NO** hardcoded `gh` setup gate — auth remediation is provider-detected (`gh auth login` | `glab auth login`) and errors are machine codes (`owner_not_configured`, `source_busy`, `setup_required` as a generic boolean), not github prose.

### Q2 — The draft-review panel + Publish button ALREADY EXISTS
It is `components/DraftReviewActions.tsx` (the publish bar), wired into `PrReviewDetail.tsx`, with siblings `PostCommentsButton.tsx` and `ShipSummaryCard.tsx`. A Bitbucket port lands in these SAME three components, not a new panel.

Two distinct backend contracts back it:
1. **Shared platform source layer** (`api/client.ts`, all POST under `/api/source/pull-request/`): `pending-review` (list draft → `{reviewId, body, comments[], headSha, stale, contentDigest, ...}`), `submit-review` (`{url, reviewId, event: APPROVE|REQUEST_CHANGES|COMMENT, contentDigest}` → publish), plus `comment`, `reply`, `resolve/unresolve`, `checks`, `status`, `ready`, `auto-merge`, `pull-request` (fetch source/diff).
2. **App's own layer** (`api.ts`, base `/api/apps/code-review-sage`): `POST /runs/{id}/post` → `{ok, run_id, posting, pending}` (the app's own post-findings path with progress/pending count), plus `/review`, `/review-repo`, `/repo-prs?repo=`, `/recent-repos`, `/my-repos`, `/repos` (pinned), `/settings`, `/namespaces`, `/learnings`, `/chat`, `/followup`.

### Q3 — The UI is NOT fire-and-forget; it bakes in GitHub PENDING-review semantics
`DraftReviewActions` strings: `pending_only_you_can_see_it_until_you_publish`, `draft_review`, `no_draft_review_on_this_pull_request`, `publishing_is_irreversible_and_visible_to_everyone`, verdicts `published_verdict_approved|changes_requested|comment`, block reasons (still running / replaced by later review / redacted content / written against different content). It imports a `git-pull-request-draft` icon. `submit-review` uses GitHub's `APPROVE|REQUEST_CHANGES|COMMENT` verbs and a `contentDigest` that binds publish to the exact content the user read (stale/digest guard).

### Load-bearing implication for the map
There are TWO candidate publish surfaces, and the port must pick which one Sage drives on Bitbucket:
- **(A) Platform source layer** `/api/source/pull-request/{pending-review,submit-review}` — the existing draft/publish UI already calls this, but it assumes a native PENDING-review object with a `reviewId` + APPROVE/REQUEST_CHANGES/COMMENT verdict. Bitbucket Cloud has no such native object (per TASK-1.5), so a Bitbucket source provider would have to FAKE this shape server-side.
- **(B) App layer** `POST /runs/{id}/post` — the app's own posting path (`posting`/`pending` progress), which is where TASK-1.5's Sage-side staged model naturally lives (Sage holds its own draft, its own Publish, a sequential Rovo add-comment loop).

TASK-1.5 chose the Sage-side model (B-shaped). This research shows the SHIPPING UI's draft/publish bar is wired to (A). So the UI port is not just "add a Publish button" — it's deciding whether Bitbucket Sage keeps using `DraftReviewActions` against a faked `/api/source` PENDING contract, or whether the Bitbucket UI drives the app's own `/runs/{id}/post` path and `DraftReviewActions` is bypassed/reworked. That is the sharpened UI-port decision (graduated to the map).
---
<!-- COMMENTS:END -->
