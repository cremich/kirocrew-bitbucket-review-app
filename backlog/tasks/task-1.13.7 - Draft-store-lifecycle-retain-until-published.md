---
id: TASK-1.13.7
title: 'Draft-store lifecycle: retain-until-published'
status: To Do
assignee: []
created_date: '2026-09-12 15:24'
labels:
  - ready-for-agent
dependencies:
  - TASK-1.13.4
parent_task_id: TASK-1.13
ordinal: 21000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

A reviewer's completed review is held as an internal draft so nothing is posted until they decide, and that draft survives until published or discarded. The result record IS the Sage draft.

- Add a `hold_for_publish` flag. The clear sweep (which fires at the NEXT run's START, not at run end) skips records where `hold_for_publish=true AND fully_posted=false`.
- The flag clears only when every wanted marker is confirmed via read-back, or on explicit discard. No TTL.
- At run completion persist findings + `revision`; the `{summary, inline}` work list is computed lazily on GET/publish (derived, rebuilt each time — not persisted).

Correction the code forced: the map's premise that results are cleared after a run was wrong — `clear_results()` runs at the next run's start, which is exactly why the record can serve as the persistent draft.

## Acceptance criteria

- [ ] A held draft (`hold_for_publish=true`, not fully posted) survives the next run's start-of-run clear
- [ ] The flag clears on full read-back confirmation or explicit discard; no TTL expiry
- [ ] Findings + `revision` persisted at completion; work list not persisted
- [ ] Store/driver tests cover the sweep-skip and clear conditions

## Blocked by

- TASK-1.13.4 (Bitbucket review run produces the draft record).
<!-- SECTION:DESCRIPTION:END -->
