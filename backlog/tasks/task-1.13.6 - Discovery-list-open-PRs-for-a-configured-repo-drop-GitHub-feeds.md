---
id: TASK-1.13.6
title: 'Discovery: list open PRs for a configured repo; drop GitHub feeds'
status: To Do
assignee: []
created_date: '2026-09-12 15:24'
labels:
  - ready-for-agent
dependencies:
  - TASK-1.13.2
parent_task_id: TASK-1.13
ordinal: 20000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

A reviewer picks a PR to review without pasting a link: Sage lists the open PRs for a configured Bitbucket repo. The GitHub-only personal feed pickers, which have no Bitbucket equivalent, are removed so the UI never surfaces them.

- Rebuild list-open-PRs on the Rovo `listBitbucketRepoPullRequests` op, scoped to a configured repo (must be in the allowlist).
- Drop `list_user_repos` and `list_contributed_repos` and their `/my-repos` + `/recent-repos` routes.
- `test_user_repos.py` shrinks as the personal pickers are removed; `test_discovery.py` covers the new list-open-PRs path.

## Acceptance criteria

- [ ] Listing open PRs for a configured repo returns them via the Rovo list op
- [ ] Listing refuses a repo outside the allowlist
- [ ] `/my-repos` and `/recent-repos` routes removed; `list_user_repos`/`list_contributed_repos` gone
- [ ] Discovery tests cover the new path; removed-picker tests deleted, suite green

## Blocked by

- TASK-1.13.2 (fail-closed config + `allowed_targets`).
<!-- SECTION:DESCRIPTION:END -->
