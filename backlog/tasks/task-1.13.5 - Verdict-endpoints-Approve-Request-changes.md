---
id: TASK-1.13.5
title: 'Verdict endpoints: Approve / Request-changes'
status: To Do
assignee: []
created_date: '2026-09-12 15:24'
labels:
  - ready-for-agent
dependencies:
  - TASK-1.13.2
parent_task_id: TASK-1.13
ordinal: 19000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

A reviewer records a verdict on the PR from Sage without switching to the Bitbucket web UI. Two separate endpoints, kept distinct from Publish so a one-way verdict never fires by accident while just posting comments.

- `/approve` wraps the Rovo `approveBitbucketRepoPullRequest` executeWrite op.
- `/request-changes` wraps `requestChangesOnBitbucketRepoPullRequest`.
- Both take `{workspaceId, repoId, prId}` (no verdict/body field — verified live). Target must be in the configured allowlist.
- One-way by design: Rovo exposes no un-approve/withdraw and no reviewer-status read; undo is the Bitbucket web UI. (The confirm dialog stating this lives in the UI port, out of local scope.)

Depends only on config/target-scope, so it can proceed in parallel with the fetch → draft → publish chain.

## Acceptance criteria

- [ ] `/approve` fires the Rovo approve op for an in-scope PR
- [ ] `/request-changes` fires the Rovo request-changes op for an in-scope PR
- [ ] Both refuse a target outside the allowlist
- [ ] Endpoints are separate from the publish/post endpoint
- [ ] Rovo executeWrite faked at the transport boundary; tests assert the request shape Sage builds, not tool names

## Blocked by

- TASK-1.13.2 (fail-closed config + `allowed_targets`).
<!-- SECTION:DESCRIPTION:END -->
