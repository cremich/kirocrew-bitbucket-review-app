---
id: TASK-1.13.3
title: 'Manifest: declare Rovo/Atlassian dependency, drop gh'
status: To Do
assignee: []
created_date: '2026-09-12 15:23'
labels:
  - ready-for-agent
dependencies: []
parent_task_id: TASK-1.13
ordinal: 17000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Parent

TASK-1.13 — Port Code Review Sage from GitHub to Bitbucket Cloud (spec).

## What to build

Make the app manifest describe the Bitbucket/Rovo reality so install requirements are accurate. Independent of the code path — pure manifest + prose.

- Drop `gh` from `app.json` `dependencies.commands`.
- Rewrite the GitHub-oriented prose to describe Bitbucket Cloud via the Atlassian Rovo MCP transport (no App Password, no `gh` CLI).
- Add a config-schema note for `bitbucket_repos`.

## Acceptance criteria

- [ ] `gh` removed from `dependencies.commands`
- [ ] Manifest prose describes Bitbucket/Rovo instead of GitHub
- [ ] Config-schema note for `bitbucket_repos` present

## Blocked by

- None (can start immediately).
<!-- SECTION:DESCRIPTION:END -->
