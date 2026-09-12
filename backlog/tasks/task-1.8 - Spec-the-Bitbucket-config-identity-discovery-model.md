---
id: TASK-1.8
title: 'Spec the Bitbucket config, identity + discovery model'
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 11:05'
updated_date: '2026-09-12 12:10'
labels:
  - 'wayfinder:grilling'
dependencies:
  - TASK-1.5
parent_task_id: TASK-1
ordinal: 9000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Specify the config, identity, and discovery model for Bitbucket — the platform-neutral replacement for GitHub's github_hosts + gh-CLI-auth + gh-user-recent-repos scheme (see TASK-1.1, TASK-1.3).

Cover: (a) config keys — what replaces github_hosts (store.py:87,120)? A bitbucket_workspaces list, or explicit workspace/repo (dflds/content.hub)? How is the target repo configured given app.json has no config schema block today; (b) identity/auth — transport is Rovo MCP (no gh, no App Password per TASK-1.4), so how is "who is Sage posting as" established, and how does the `[code-review-sage]` marker ownership work when the author is the Rovo MCP token's user; (c) discovery — the deterministic gh-subprocess repo/PR pickers (list_user_repos via `gh api user/repos`, list_contributed_repos via events, list_open_prs) rebuilt on listBitbucketRepoPullRequests + Bitbucket repo listing via MCP, or dropped for a simpler "paste/configure the repo" flow; (d) app.json + prose — gh dependency removed, github tags/description/useCases rewritten, whether a config schema/secrets block is now needed.

Blocked on TASK-1.5 (identity/ownership follows the posting model) and TASK-1.4 (discovery ops — done).
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
created: 2026-09-12 12:10
---
**Resolution — Bitbucket config, identity + discovery model. Decision: A2 + C1.**

**(a) Config — replaces `github_hosts`.** Drop the host-allowlist model (single-host `bitbucket.org` makes a host list dead weight). New config key `bitbucket_repos: [{workspace, repo}]` in `store.DEFAULT_CONFIG` (replaces `github_hosts` at store.py:87,120), e.g. `[{"workspace": "dflds", "repo": "content.hub"}]`. `adapters.allowed_hosts()` (adapters.py:101-126) is replaced by `allowed_targets(config)` returning a frozenset of `(workspace, repo)` pairs; the host-revalidation fail-closed guard in review_driver.py:251 / _confirmed_host becomes a `(workspace, repo)`-in-scope check. NO safe default (unlike github.com) — an unconfigured Sage reviews nothing (fails closed), never defaults to a public target.

**(b) Identity/auth.** Sage posts as whatever the Rovo MCP token's user is — the direct analog of GitHub's 'whatever gh is authed as'. No dedicated bot account, no App Password (per TASK-1.4). Ownership stays MARKER-based, unchanged: `pipeline.DRAFT_MARKER = "[code-review-sage]"` (pipeline.py:43) is embedded in the summary body (pipeline.py:329,394) and every inline comment; re-publish identifies Sage's own comments by the marker (+ the TASK-1.5 path+rule+content-hash per-comment marker), NOT by author. This is why the port needs no identity work — ownership was never author-based.

**(c) Discovery — DROPPED.** Delete `discovery.list_user_repos` (gh api user/repos) and `discovery.list_contributed_repos` (gh events feed): both are personal-gh-identity pickers with no clean Rovo-token mapping and no Bitbucket events equivalent. Keep only PR enumeration: `pipeline.list_open_prs` (pipeline.py:99) rebuilt on Rovo `listBitbucketRepoPullRequests(workspaceId, repoId)` scoped to a configured repo. Target selection = paste a PR URL OR pick from a configured repo's open PRs. Routes (TASK-1.3): remove GET /recent-repos and GET /my-repos; GET /repo-prs rebuilt on listBitbucketRepoPullRequests; /repos CRUD becomes management of the `bitbucket_repos` config list (drops the 'GitHub Enterprise repos can't be pinned' host check at routes.py:1532,1545).

**(d) app.json + prose.** Remove `dependencies.commands: ["gh"]` (app.json:41). Rewrite the `github` tag, description, highlights, useCases, and configuration prose to Bitbucket Cloud. ADD a config schema block declaring `bitbucket_repos` (required, no default) — needed precisely because there is no safe default to fall back to, unlike github_hosts.

**Rejected: A1+C2** (bitbucket_workspaces list + rebuilt discovery). Only worth it if Sage must serve many repos across a workspace like the GitHub original; the actual use is one repo (dflds/content.hub), so A2+C1 is the smallest faithful port and sidesteps the gh-events identity mismatch.
---
<!-- COMMENTS:END -->
