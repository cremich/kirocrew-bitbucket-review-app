---
id: TASK-1.2
title: 'Research: review-posting + learning + identity touchpoints'
status: Done
assignee: []
created_date: '2026-09-12 11:04'
labels:
  - 'wayfinder:research'
dependencies: []
parent_task_id: TASK-1
ordinal: 3000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Map how findings are posted back, whether the GitHub PENDING/draft review primitive is used, the posting granularity, how bot identity is established, and what the learning subsystem persists (does it embed GitHub identifiers). Every GitHub-specific symbol with file:line.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
POSTING uses GitHub's PENDING (draft) review primitive as the single staging mechanism — the biggest porting hazard. Not done in Python: an isolated LLM "poster" session runs `gh api --method POST repos/<o>/<r>/pulls/<n>/reviews` with NO `event` key, so GitHub creates it PENDING and a HUMAN submits later (review_driver.py:540-566). GitHub allows one pending review per author, so each post DELETES the existing sage draft and RE-CREATES it (delete-and-recreate dedup, :551-566,:753-762).

Granularity: ONE pending review object per PR carrying inline line comments (each anchored file/line + commit_id) + one summary body. No task/checklist primitive used. Findings without a usable anchor fold into the body (:853-861).

Identity: NO dedicated bot/app account — uses gh's stored auth (:566-567). Sage drafts recognized ONLY by literal marker `[code-review-sage]` in the review body (pipeline.DRAFT_MARKER), never by author (:553-557). Marker-based ownership is portable in principle.

Learning: platform-CLEAN. Stores markdown pattern files keyed by scope/namespace (learning.py:84-91,271-273), NOT per-PR. repo_identity is only an optional filter arg. Grep found no github/GH-/node_id/pr_url/review_id. Ports unchanged. store.py data model also clean: repo_identity = plain host/owner/repo string, change_id = GH- prefix, no node IDs persisted.

Two load-bearing facts for design: (1) the whole posting contract hinges on GitHub's single author-owned PENDING review with human-submit-later — Bitbucket has no equivalent, this is the port's real work; (2) the data model itself is portable. Full file:line in subagent result d5628eca.
<!-- SECTION:FINAL_SUMMARY:END -->
