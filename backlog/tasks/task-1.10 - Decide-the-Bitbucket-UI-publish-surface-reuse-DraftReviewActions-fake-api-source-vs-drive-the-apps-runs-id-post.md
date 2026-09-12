---
id: TASK-1.10
title: >-
  Decide the Bitbucket UI publish surface: reuse DraftReviewActions (fake
  /api/source) vs drive the app's /runs/{id}/post
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 12:14'
updated_date: '2026-09-12 13:07'
labels:
  - 'wayfinder:grilling'
dependencies:
  - TASK-1.7
parent_task_id: TASK-1
ordinal: 11000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

TASK-1.9 revealed the shipping Code Review Sage UI ALREADY has a draft-review + Publish surface (`components/DraftReviewActions.tsx`, wired into `PrReviewDetail.tsx`, with `PostCommentsButton.tsx` + `ShipSummaryCard.tsx`). It is a PORT, not net-new. But it is wired to the shared platform source layer `POST /api/source/pull-request/{pending-review, submit-review}`, which assumes a NATIVE PENDING-review object: a `reviewId`, an APPROVE|REQUEST_CHANGES|COMMENT verdict, and a `contentDigest` staleness guard. Bitbucket Cloud has no native pending-review object (TASK-1.5).

TASK-1.5 chose a **Sage-side staged review** (app holds its own draft, a Sage Publish button, a sequential Rovo add-comment loop), which is naturally shaped for the app's OWN layer `POST /api/apps/code-review-sage/runs/{id}/post` (returns `{posting, pending}` progress) — NOT the platform source layer.

So there are two candidate publish surfaces and the port must pick one:

- **(A) Reuse `DraftReviewActions` against `/api/source/pull-request/*`.** Keep the shipping UI as-is; make a Bitbucket source provider FAKE the pending-review/submit-review shape server-side (synthesize a `reviewId`, map the verdict, compute a `contentDigest`, run the Rovo add-comment loop on submit). Pro: zero UI change, keeps the polished irreversibility/staleness UX. Con: server has to fabricate a GitHub-shaped review object Bitbucket doesn't have; verdict semantics (APPROVE/REQUEST_CHANGES) may not map cleanly to advisory-only V1.
- **(B) Drive the app's own `/runs/{id}/post` path from the Bitbucket UI.** `DraftReviewActions` is bypassed or reworked; the Publish button calls the app layer where TASK-1.5's model already lives. Pro: matches the decided Sage-side model directly, no faked platform object, advisory-only fits naturally. Con: reworks/forks a shipping component; loses (or must re-implement) the `contentDigest` staleness + irreversibility guards.

DECIDE which surface Bitbucket Sage drives, and what that implies for the UI port (which components change, whether the source-provider layer gets a Bitbucket impl, how the verdict/advisory-only + staleness guards carry over). Grill + domain-modeling. This is the UI-port keystone; the concrete component-by-component UI spec graduates from its resolution.

Depends on TASK-1.5 (posting model, done) and TASK-1.7 (poster + idempotency contract) — the publish surface must agree with the poster contract TASK-1.7 fixes.
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: sage-port-charting
created: 2026-09-12 13:07
---
RESOLUTION — Bitbucket UI publish surface decided.

**Decision: Option B.** The Bitbucket Publish bar drives the app's own layer `POST /api/apps/code-review-sage/runs/{id}/post`, NOT a faked platform-source `/api/source/pull-request/*`. Rationale: Option A would make shared platform code fabricate a GitHub-native PENDING object Bitbucket lacks (a synthesized `reviewId`, a borrowed `contentDigest`, APPROVE/REQUEST_CHANGES verdicts ruled out for advisory posting) — a permanent coupling of Sage's port to the platform source layer. TASK-1.7 already built the honest posting contract in the app layer; B points the UI at it.

**Implications for the UI port:**
- No Bitbucket implementation of the `/api/source/pull-request/*` provider is written. The source layer stays GitHub-only; Bitbucket Sage bypasses it.
- `DraftReviewActions` is reworked, not reused as-is: it calls `/runs/{id}/post` (returns `{posting, pending}` progress) instead of `pending-review`/`submit-review`.
- **Staleness guard: KEPT, Sage-owned.** Re-implemented off the ReviewTarget `revision` (source.commit.hash, from TASK-1.6): block/warn on publish if the PR head moved since the draft was computed. Not borrowed from the source layer's `contentDigest`.
- **Irreversibility warning: KEPT** — and more true than on GitHub, since Bitbucket comments are permanent (TASK-1.7). Progress display kept.

**Scope change vs TASK-1.5 (advisory-only V1): verdict-driving moves INTO V1.** Approve / Request-changes fire from Sage directly, no Bitbucket web-UI context switch (explicit user requirement this session). Confirmed live via Atlassian Rovo MCP `discover`:
- `approveBitbucketRepoPullRequest` — executeWrite — args `{workspaceId: string, repoId: string, prId: number}`
- `requestChangesOnBitbucketRepoPullRequest` — executeWrite — args `{workspaceId: string, repoId: string, prId: number}`
- No verdict/body field on either; ids match TASK-1.6 (workspaceId='dflds', repoId='content.hub', prId from parsed PR).

**Verdict UI shape:** discrete **Approve** / **Request changes** buttons SEPARATE from Publish (Bitbucket models them as distinct endpoints, unlike GitHub's fused single-submit review). Publishing comments and stamping a verdict are independent actions.

**The un-approve constraint (load-bearing):** Rovo exposes only POST `/approve`; there is NO `unapprove`/withdraw op and NO reviewer-status READ op. So Sage can approve but cannot retract from the same transport, and cannot reliably display current verdict. **Handling = confirm-before-fire:** verdict actions are one-way from Sage's surface — a confirmation step states that undo requires the Bitbucket web UI, then fires. The UI owns the limitation instead of hiding it.

Sources: Rovo op names + un-approve gap from Atlassian 'Supported tools' catalog (research TASK-1.10 subagent); arg schemas from a live `discover` call in the main session (workspaceId/repoId/prId, executeWrite, no verdict field).
---
<!-- COMMENTS:END -->
