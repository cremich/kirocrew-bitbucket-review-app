---
id: TASK-1.1
title: 'Research: PR fetch + discovery + pipeline touchpoints'
status: Done
assignee: []
created_date: '2026-09-12 11:04'
labels:
  - 'wayfinder:research'
dependencies: []
parent_task_id: TASK-1
ordinal: 2000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Map the PR fetch, discovery, and pipeline touchpoints: the ReviewTarget shape, exactly how a GitHub PR is fetched today, the discovery flow, the adapter seam vs the platform-agnostic brain, and every GitHub-specific symbol with file:line.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
ReviewTarget dataclass (adapters.py:42-63) has 14 fields: platform, repo_identity, change_id, url, title, description, linked_issue, author, target_branch, revision, files[], existing_comments[], design_discussion[], is_fix. This is the platform-agnostic contract.

TWO fetch paths: (A) single PR for review = an LLM INSTRUCTION STRING (pipeline.py FETCH_SPECS, :272-306) — the worker LLM runs `gh api repos/<o>/<r>/pulls/<n>` + `/files`, result normalized by parse_github_payload (adapters.py:328). NOT a Python subprocess. (B) discovery + verification = deterministic `gh` subprocess: pipeline.list_open_prs (pipeline.py:99-160) and discovery.run_gh_json (discovery.py:128-160). No requests/httpx anywhere; all HTTP via gh CLI.

Discovery = two-stage picker: which repos (discovery.list_user_repos `gh api user/repos`, list_contributed_repos `users/<login>/events`) then which open PRs (pipeline.list_open_prs).

Adapter seam (fork per platform): nearly all of adapters.py, all gh plumbing in discovery.py, and pipeline.py dicts FETCH_SPECS/POSTING_SPECS + list_open_prs + build_github_review_payload. Brain (untouched): ReviewTarget itself, review_pool.py orchestration, review_driver.py prompt-building (reaches platform only through pipeline.*/adapters.*). Leak: review_driver.py references cur["github_review_payload"] directly (:783,:859).

GitHub symbols/literals: detect_platform, github_pr_ref/parts/change_id/review_key, parse_github_payload (adapters.py); _GITHUB_HOST/_WWW_GITHUB_HOST (:73-74), _PR_PATH_RE (:23), GH- id prefix (:268); gh_env/gh_bin/run_gh_json (discovery.py); FETCH_SPECS["github"]/list_open_prs (pipeline.py); config key github_hosts (adapters.py:105,115). Full file:line list captured in subagent result fa34fcc5.
<!-- SECTION:FINAL_SUMMARY:END -->
