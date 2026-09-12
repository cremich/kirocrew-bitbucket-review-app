---
id: TASK-1.3
title: 'Research: backend routes + config + app.json + React UI'
status: Done
assignee: []
created_date: '2026-09-12 11:04'
labels:
  - 'wayfinder:research'
dependencies: []
parent_task_id: TASK-1
ordinal: 4000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Map GitHub-specificity across backend routes, config keys, the app.json manifest, and the React UI. Which routes assume GitHub, which config keys are GitHub-bound, what app.json declares, and where the UI builds github.com URLs. Every GitHub-specific symbol with file:line.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Routes: all under /api/apps/code-review-sage (routes.py:2434-2464). 8 assume GitHub: POST /review, POST /review-repo, GET /repo-prs, GET /recent-repos, GET /my-repos, GET/POST/DELETE /repos (rejects non-github.com hosts, "GitHub Enterprise repos can't be pinned" :1532,1545), POST /runs/{id}/post (publishes PENDING draft). PR-URL parse helper _pasted_pr_ref requires /pull/ (:1476).

Config: github_hosts is the central key (store.py:87 DEFAULT_GITHUB_HOSTS, :120 DEFAULT_CONFIG), consumed adapters.allowed_hosts (:115). review.auto_post semantics = "PENDING draft review". gh CLI is a declared dependency (app.json:41). No GITHUB_TOKEN in code — auth delegated to gh's own stored auth. github.com URL builders across routes.py:619-623,784,1487,1514; adapters.py:73-74,422; pipeline.py:91; review_driver.py:997,1015.

app.json: NO config JSON schema block (runtime config lives in store.DEFAULT_CONFIG), NO secrets/vault block (creds from gh CLI). Permissions: api scoped to the app prefix, network+storage true. GitHub-bound: description/tags("github")/highlights/useCases/configuration prose + dependencies.commands ["gh"].

UI: NOT IN THIS REPO. README.md:52-54 locates it at website/src/apps/code-review-sage/CodeReviewSagePage.tsx (registered website/src/apps/builtinRegistry.ts), outside this project tree — no .tsx/.ts anywhere here. Backend contract the UI renders: PR URLs shaped https://{host}/{owner}/{repo}/pull/{number}, the pinning error strings, setup_required for missing gh, and a settings payload that does NOT expose github_hosts. The website/ React port needs a separate research pass against that repo. Full file:line in subagent result 18b6c92b.
<!-- SECTION:FINAL_SUMMARY:END -->
