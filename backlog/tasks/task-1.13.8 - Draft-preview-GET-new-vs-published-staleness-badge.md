---
id: TASK-1.13.8
title: 'Draft preview GET: new vs published, staleness badge'
status: To Do
assignee: []
created_date: '2026-09-12 15:24'
labels:
  - ready-for-agent
dependencies:
  - TASK-1.13.7
parent_task_id: TASK-1.13
ordinal: 22000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

A reviewer opens the held draft in Sage's UI and sees every finding with its proposed comment, before publishing anything. Each item is labelled new vs already-published so the reviewer knows what a re-publish will actually do, and a stale badge warns when the PR head moved.

- Draft `GET /runs/{id}/draft` returns `{change_id, revision, hold_for_publish, findings[], summary, inline[], posted[], stale}`.
- Each finding/item carries its marker key and a `status: new | published`.
- The work list (`summary`, `inline[]`) is computed lazily on this GET (derived, not stored).
- `stale` computed by one Rovo read of the live PR head vs the stored `revision`.
- Pure read — this endpoint posts nothing.

## Acceptance criteria

- [ ] `GET /runs/{id}/draft` returns the documented shape
- [ ] Each item carries a marker key and `status: new|published`
- [ ] `stale` is true when the live PR head differs from the stored `revision`
- [ ] The endpoint posts nothing and mutates nothing
- [ ] Run-endpoint tests cover the shape and the staleness computation

## Blocked by

- TASK-1.13.7 (draft-store lifecycle: the held record this reads).
<!-- SECTION:DESCRIPTION:END -->
