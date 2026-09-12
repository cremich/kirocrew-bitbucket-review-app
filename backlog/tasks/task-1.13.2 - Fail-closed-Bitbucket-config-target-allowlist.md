---
id: TASK-1.13.2
title: Fail-closed Bitbucket config + target allowlist
status: To Do
assignee: []
created_date: '2026-09-12 15:23'
labels:
  - ready-for-agent
dependencies: []
parent_task_id: TASK-1.13
ordinal: 16000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

Give Sage a fail-closed notion of which Bitbucket repos it may touch. An operator configures allowed targets as workspace/repo pairs; an unconfigured Sage has no safe default and rejects everything. This ticket does not fetch or post — it only establishes the allowlist and its CRUD surface so every later Bitbucket action can revalidate scope.

- Add `bitbucket_repos: []` to DEFAULT_CONFIG (no safe default → fail closed); keep `github_hosts` (the port is additive, GitHub keeps working).
- `allowed_targets(config)` returns a frozenset of `(workspace, repo)` pairs; empty and fail-closed when unconfigured.
- `/repos` CRUD manages the `bitbucket_repos` list.

## Acceptance criteria

- [ ] `bitbucket_repos` defaults to empty; unconfigured Sage resolves zero allowed targets
- [ ] `allowed_targets(config)` returns the configured `(workspace, repo)` pairs
- [ ] `/repos` CRUD adds/lists/removes configured Bitbucket targets
- [ ] GitHub config path unchanged

## Blocked by

- None (can start immediately).
<!-- SECTION:DESCRIPTION:END -->
