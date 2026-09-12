---
id: TASK-1.6
title: Spec the Bitbucket PR fetch + ReviewTarget mapping adapter
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 11:05'
updated_date: '2026-09-12 12:08'
labels:
  - 'wayfinder:grilling'
dependencies:
  - TASK-1.5
parent_task_id: TASK-1
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

Specify the Bitbucket single-PR fetch adapter that produces a normalized ReviewTarget (14 fields, adapters.py:42-63). Today GitHub uses an LLM instruction string (FETCH_SPECS, the worker runs `gh api`) for single-PR fetch and a deterministic `gh` subprocess for discovery (see TASK-1.1). For Bitbucket the transport is Rovo MCP (see TASK-1.4): getBitbucketRepoPullRequest + getBitbucketRepoPullRequestDiff for one PR, listBitbucketRepoPullRequests for discovery.

Decide: (a) does the Bitbucket single-PR fetch stay an LLM-instruction (tell the worker which MCP tools to call) or become a deterministic Python MCP call? (b) the field mapping from Bitbucket PR payload -> ReviewTarget (author, target_branch, revision/commit, files[]{path,diff}, existing_comments, linked_issue — Bitbucket has no #-issue convention, what replaces it?), (c) the parse_bitbucket_payload equivalent and where detect_platform routes to it, (d) change_id / repo_identity shape (GH- prefix -> BB- ? repo_identity = bitbucket.org/dflds/content.hub?).

Blocked on TASK-1.5: the fetch may need to carry diff-side/line anchor data (from/to) that only matters given the chosen posting model.
<!-- SECTION:DESCRIPTION:END -->

## Comments

<!-- COMMENTS:BEGIN -->
author: sage-port-charting
created: 2026-09-12 12:08
---
RESOLUTION (1/2) — Bitbucket PR fetch + ReviewTarget mapping adapter spec.

Ground truth read from source this session: ReviewTarget has 14 fields (adapters.py:42-63); single-PR fetch is an LLM-instruction string (pipeline.FETCH_SPECS / fetch_spec, worker runs `gh api`), while discovery (list_open_prs, list_user_repos) is a deterministic `gh` subprocess via discovery._run_gh; parse_github_payload is the token-free normalizer; detect_platform routes on allowed_hosts() + '/pull/' in the parsed path.

=== (a) LLM-instruction vs deterministic Python for the single-PR fetch ===
DECISION: KEEP the fetch as an LLM-instruction (a `fetch_spec('bitbucket')` string). Do NOT write a deterministic Python MCP client for single-PR fetch.
Why: the Rovo MCP tools are callable only by the LLM worker (the MCP client is the agent, not this Python process — there is no in-process MCP call seam here the way discovery.py has `gh` on PATH). Mirroring GitHub's own split keeps the adapter seam identical: fetch = instruction, parse = deterministic Python. The instruction tells the worker to `discover` the Bitbucket tools at runtime (names vary per TASK-1.4), then call getBitbucketRepoPullRequest + getBitbucketRepoPullRequestDiff (+ listBitbucketRepoPullRequestComments), and assemble ONE JSON object of the same shape parse_bitbucket_payload consumes.
Discovery (list_open_prs equivalent) is the mirror question and belongs to TASK-1.8, NOT here — flagged so it isn't lost: for Bitbucket, list_open_prs cannot stay a `gh` subprocess; it becomes an LLM-instruction (listBitbucketRepoPullRequests) or is deferred. Left to 1.8's discovery scope.

=== (b) Field mapping: Bitbucket PR payload -> ReviewTarget (14 fields) ===
Worker assembles a merged JSON object; parse_bitbucket_payload maps it. Source = Bitbucket Cloud PR (/2.0/repositories/{ws}/{repo}/pullrequests/{id}) + diff:
- platform = 'bitbucket'
- repo_identity = 'bitbucket.org/dflds/content.hub' (host/workspace/repo — same three-part shape as GitHub; workspace plays owner's role)
- change_id = BB-<workspace>-<repo>-<id> (see part 2, (d))
- url = links.html.href
- title = title
- description = summary.raw  (Bitbucket calls the body `summary`, under .raw)
- linked_issue = see part 2, (c)
- author = author.nickname (fallback author.display_name, then account_id) — NOT a login; Bitbucket has no stable @handle
- target_branch = destination.branch.name
- revision = source.commit.hash (head SHA the review anchors to)
- files[] {path, diff}: Bitbucket has NO per-file `patch` array. getBitbucketRepoPullRequestDiff returns ONE unified diff for the whole PR. The parser SPLITS that unified diff on `diff --git a/... b/...` (or the `+++ b/<path>` header) into per-file {path, diff} chunks. This is the single biggest shape difference from GitHub and the main new code in the parser. (/diffstat gives paths but not hunks, so split-the-unified-diff is the path.)
- existing_comments = listBitbucketRepoPullRequestComments passed through (brain context + 1.7 dedup marker scan)
- design_discussion = [] (always empty; matches GitHub per TASK-1.1)
- is_fix = detect_is_fix(title, description) (unchanged regex on title+summary)
---

author: sage-port-charting
created: 2026-09-12 12:08
---
RESOLUTION (2/2) — continued.

=== (c) parse_bitbucket_payload + where detect_platform routes ===
- detect_platform: today `host in allowed_hosts() and '/pull/' in path -> 'github'`. Add a Bitbucket branch. Bitbucket Cloud PR URLs are `bitbucket.org/<ws>/<repo>/pull-requests/<id>` (note: `pull-requests`, hyphenated + plural, NOT `/pull/`). So: parsed host == 'bitbucket.org' and '/pull-requests/' in path -> 'bitbucket'. Add `_BB_PR_PATH_RE = ^/([^/]+)/([^/]+)/pull-requests/(\d+)` alongside the GitHub one. Keep the SAME parsed-hostname EXACT-match + default-deny discipline (no substring/regex-on-raw-URL); add a `bitbucket_hosts` config key (default {'bitbucket.org'}) mirroring github_hosts — that config detail is TASK-1.8's.
- normalize(): add `elif platform == 'bitbucket': return parse_bitbucket_payload(raw_payload, link=link)`.
- parse_bitbucket_payload: SAME contract as parse_github_payload — tolerant _first() lookups, fail-fast when no files AND no description, fill missing ws/repo/id from the link via a new bitbucket_pr_ref. The one genuinely new mechanism is the unified-diff splitter for files[].

=== (c') linked_issue — Bitbucket has no #N convention ===
GitHub extracts `#123` from the PR body (GH_ISSUE_RE). Bitbucket Cloud's native issue tracker is largely unused (this repo uses Backlog.md + Jira), and there is no reliable `#N` grammar. DECISION: linked_issue stays a plain string but is populated from JIRA KEYS in the PR title/summary (regex `[A-Z][A-Z0-9]+-\d+`, e.g. CONTENT-42), since dflds runs Jira via the same Atlassian tenant. If none found, leave it ''. This is a soft, non-load-bearing field (advisory context for the brain, never an identity key), so an empty value is harmless. Do NOT invent a Bitbucket-issue fetch; Jira-key-in-text is the pragmatic port.

=== (d) change_id / repo_identity shape ===
- repo_identity = 'bitbucket.org/dflds/content.hub' (three-part host/org/repo; learning subsystem is platform-clean per TASK-1.2, so this key type ports unchanged).
- change_id = BB-<workspace>-<repo>-<id> via a new bitbucket_change_id() mirroring github_change_id(): filesystem-safe, _sanitize_seg per segment ('-' stays the delimiter). Bitbucket Cloud is single-host, so BB-<ws>-<repo>-<id> is the stable form; GH-prefixed ids stay byte-identical (different prefix — no collision).
- reviewed-index key -> bitbucket_review_key = 'bitbucket.org/<ws>/<repo>#<id>' (lossless, verbatim, lower-cased ws/repo), mirroring github_review_key: never names a file, so segments stay verbatim to avoid the lossy-sanitize collision documented on github_review_key.

=== Blocked-on-1.5 note RESOLVED ===
1.5 asked whether the fetch must carry diff-side/line anchor data. ANSWER: NO new field on ReviewTarget. files[].diff already carries the full unified-diff hunks, and Bitbucket's inline anchor keys (path, from=old side, to=new side, start_from/start_to) are DERIVED BY THE POSTER (1.7) from the finding's line + the diff, not stored on ReviewTarget. So the 14 fields are sufficient as-is. The from/to derivation is 1.7's problem, fed by files[].diff.

=== Downstream / graduated ===
- TASK-1.7 (poster) inherits: from/to inline-anchor derivation from files[].diff + finding.line; the marker-scan dedup reads the existing_comments this adapter carries.
- Bitbucket discovery equivalent of list_open_prs (LLM-instruction vs deferred) flagged INTO TASK-1.8's config/identity/discovery scope — not a new ticket.
- No new fog surfaced; no ticket invalidated; nothing ruled out of scope.
---
<!-- COMMENTS:END -->

## Final Summary

<!-- SECTION:FINAL_SUMMARY:BEGIN -->
Fetch stays an LLM-instruction (fetch_spec('bitbucket') -> discover + getBitbucketRepoPullRequest/getBitbucketRepoPullRequestDiff/listComments), mirroring GitHub's fetch=instruction / parse=deterministic split; no in-process MCP seam exists. New parse_bitbucket_payload maps the merged payload to the same 14-field ReviewTarget: platform='bitbucket', repo_identity='bitbucket.org/dflds/content.hub', change_id=BB-<ws>-<repo>-<id>, url=links.html.href, description=summary.raw, author=author.nickname, target_branch=destination.branch.name, revision=source.commit.hash. Biggest new code = a unified-diff splitter for files[] (Bitbucket returns ONE PR-wide diff, no per-file patch array). detect_platform gains a branch on host=='bitbucket.org' and '/pull-requests/' in path (hyphenated+plural), same parsed-hostname exact-match/default-deny discipline + a bitbucket_hosts config key. linked_issue is repurposed to Jira keys ([A-Z][A-Z0-9]+-\d+ in title/summary), else ''. New bitbucket_change_id + bitbucket_review_key mirror the GitHub helpers (lossless verbatim key, sanitized file-safe id). Blocked-on-1.5 resolved: NO new ReviewTarget field needed — inline from/to anchors are derived by the 1.7 poster from files[].diff, not stored. Discovery list_open_prs equivalent flagged into TASK-1.8; no new fog, nothing out of scope.
<!-- SECTION:FINAL_SUMMARY:END -->
