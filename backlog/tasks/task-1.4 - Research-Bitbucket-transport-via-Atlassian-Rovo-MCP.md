---
id: TASK-1.4
title: 'Research: Bitbucket transport via Atlassian Rovo MCP'
status: Done
assignee: []
created_date: '2026-09-12 11:04'
labels:
  - 'wayfinder:research'
dependencies: []
parent_task_id: TASK-1
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Which Bitbucket PR operations does the Atlassian Rovo MCP expose (list/get/diff/comment/task)? Does Bitbucket Cloud have any draft/pending-review primitive analogous to GitHub's PENDING review? What identifiers does an inline PR comment require? Any high-volume posting limits? Cite doc URLs.
<!-- SECTION:DESCRIPTION:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Transport works via Rovo MCP v2 (https://mcp.atlassian.com/v2/mcp) — discover a tool, then executeRead/executeWrite/executeDestructive. v2 catalog names: listBitbucketRepoPullRequests, getBitbucketRepoPullRequest, getBitbucketRepoPullRequestDiff, listBitbucketRepoPullRequestComments, addBitbucketRepoPullRequestComment (inline vs general = same tool, difference is the `inline` object), createBitbucketRepoPullRequestTask. NAMING CAVEAT: Atlassian's own docs disagree (short vs Repo-segment names) — resolve exact strings via `discover` at runtime, do not hard-code. No App Password / separate REST client needed.

CRITICAL: Bitbucket Cloud has NO draft/pending-review primitive. No `reviews` resource; only POST .../pullrequests/{id}/comments, which publishes IMMEDIATELY. A `pending` boolean exists on the comment schema but has NO documented behavior and NO submit endpoint — treat staged review as absent. approve/request-changes/decline are separate, post no text. So Sage's "submit review" becomes a sequential post loop; no atomic multi-comment submit.

Inline anchor: body {"content":{"raw":"..."}} + `inline` object with keys path, from (old/removed side), to (new/added side), start_from/start_to (multi-line range). Single-line = set exactly one of from/to. General comment = same body minus `inline`.

High-volume: no bulk endpoint (1 comment = 1 POST = 1 MCP call). Bitbucket REST limits rolling 1h; authed /2.0/repositories 1000-10000/hr. Rovo MCP site cap: Free 500/hr, Standard 1000/hr, Premium/Enterprise 1000 + 20/user up to 10000/hr, shared credit pool. Open: `pending` field's real behavior (needs scratch-PR probe); whether MCP stacks REST limits on the site cap. Doc URLs in subagent result a9a8c54d.
<!-- SECTION:FINAL_SUMMARY:END -->
