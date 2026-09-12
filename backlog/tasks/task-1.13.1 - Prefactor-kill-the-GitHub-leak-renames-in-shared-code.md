---
id: TASK-1.13.1
title: 'Prefactor: kill the GitHub-leak renames in shared code'
status: To Do
assignee: []
created_date: '2026-09-12 15:23'
labels:
  - ready-for-agent
dependencies: []
parent_task_id: TASK-1.13
ordinal: 15000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

Mechanical rename that removes GitHub-specific names from platform-neutral shared code, done first so later Bitbucket tickets edit clean seams. Nothing changes behaviourally: the GitHub review-and-post path works end-to-end exactly as before, and the full test suite stays green.

Renames (update every caller):
- `github_review_payload` → `review_payload`
- `build_github_review_payload` → `build_review_payload`
- `_draft_confirmed` → `_posts_confirmed`

This is a wide refactor (blast radius across shared code), so grep all call sites before and after and land it as one green step. "Make the change easy, then make the easy change."

## Acceptance criteria

- [ ] All three symbols renamed across every caller (grep confirms zero old references remain)
- [ ] GitHub review + post path still works end-to-end
- [ ] Full test suite green with no behavioural change

## Blocked by

- None (can start immediately).
<!-- SECTION:DESCRIPTION:END -->
