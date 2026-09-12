---
id: TASK-1.12
title: 'Spec the Bitbucket UI port: components, verdict controls, wire contracts'
status: Done
assignee:
  - sage-port-charting
created_date: '2026-09-12 13:08'
updated_date: '2026-09-12 13:46'
labels:
  - 'wayfinder:grilling'
dependencies:
  - TASK-1.10
  - TASK-1.11
parent_task_id: TASK-1
ordinal: 13000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
## Question

The UI-port keystone (TASK-1.10) is resolved: surface = app layer `/runs/{id}/post`, verdict-driving IN V1 as separate Approve/Request-changes buttons with confirm-before-fire, staleness guard Sage-owned, no source-provider Bitbucket impl. This ticket writes the concrete component-by-component UI-port SPEC that graduates from it. This is a SPEC (build out of scope), the last major piece before the destination handoff spec.

Cover, component by component (React UI lives in the external `kirodotdev/KiroCrew` tree at `website/src/apps/code-review-sage/`, NOT on this machine — reference by path, confirm current shape via `gh api` on HEAD as TASK-1.9 did):
- `DraftReviewActions.tsx`: rewire from `/api/source/pull-request/{pending-review,submit-review}` to app-layer publish (`POST /runs/{id}/post`, `{posting, pending}` progress) + the pre-publish draft GET (from TASK-1.11). Drop the verdict SELECTOR. Keep irreversibility warning + progress. Add the Sage-owned staleness guard (revision drift).
- NEW verdict controls: discrete Approve / Request-changes buttons calling the app-layer verdict endpoint that wraps Rovo `approveBitbucketRepoPullRequest` / `requestChangesOnBitbucketRepoPullRequest` (executeWrite, args {workspaceId, repoId, prId}). Confirm-before-fire dialog stating undo requires Bitbucket web UI. Decide component placement (in DraftReviewActions vs a sibling VerdictActions).
- `PostCommentsButton.tsx` + `ShipSummaryCard.tsx`: what changes given the app-layer contract + Bitbucket {summary, inline} work-list shape (TASK-1.7).
- `Ct()` URL builder: `https://github.com/${owner}/${repo}` → `https://bitbucket.org/${workspace}/${repo}` (feeds repoPrs/reviewRepo).
- Add-PR input regex: accept `bitbucket.org/{ws}/{repo}/pull-requests/{id}` (hyphenated+plural) alongside/instead of the github.com/.../pull/N form.
- The exact wire contracts (request/response JSON) for publish, draft-read, and the two verdict actions — the frontend↔backend seam the build implements against. Must agree with TASK-1.7 (poster) and TASK-1.8 (route wiring) and TASK-1.11 (draft store).

Grill + domain-modeling. Blocked on TASK-1.10 (done) and TASK-1.11 (draft store) — the draft-read contract this spec names is fixed there.
<!-- SECTION:DESCRIPTION:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
## Resolution — Bitbucket UI port spec (component-by-component + wire contracts)

Confirmed current shape against `kirodotdev/KiroCrew@HEAD` (`gh api`, as TASK-1.9). **Three map assumptions were wrong and are corrected here** — that is the load-bearing part of this ticket.

### CORRECTION 1 — there is no `Ct()` URL builder in `format.ts`
The map (from the minified bundle) named a `Ct()` building `https://github.com/${owner}/${repo}`. The real `lib/format.ts` has NO such function and NO github.com literal. The github coupling in `format.ts` is FOUR regex parsers:
- `repoOfRun`: `/(?:github\.com|gitlab\.com)\/([^/\s]+)\/([^/\s]+)/i`
- `sageSourceLink`: `/^https:\/\/github\.com\/([^/\s]+)\/([^/\s]+)\/pull\/(\d+)$/`
- `prLabelFromChange` + `prRefFromChange`: `/(?:pull|merge_requests)\/(\d+)/`

**Edit:** add `bitbucket.org` + `/pull-requests/(\d+)` to `repoOfRun` and the `prLabel/prRef` regexes; extend `sageSourceLink` to emit `{provider:'bitbucket', ...}` for a `bitbucket.org/{ws}/{repo}/pull-requests/{id}` URL (or return null if the shared `PullRequestPanel` has no Bitbucket provider — see CORRECTION 3). No URL-builder to touch; `repoPrs`/`reviewRepo` take a repo STRING (`owner/repo`), the backend (TASK-1.8) builds any bitbucket.org URL.

### CORRECTION 2 — `DraftReviewActions` and `PostCommentsButton` COLLAPSE into one on Bitbucket
Two SEPARATE publish paths ship today:
- `PostCommentsButton` → **app layer** `sageApi.postComments` → `POST /runs/{id}/post` → returns `{ok, run_id, posting, pending}`. This already IS the Sage-side model. Posts inline+ship comments.
- `DraftReviewActions` → **source layer** `api.pullRequestPendingReview(url)` + `api.submitPullRequestReview(url, reviewId, event, contentDigest)` on `../../../api/client`. This is the GitHub-PENDING release bar: read the pending draft GitHub holds, then submit it with a verdict `event`.

On Bitbucket there is NO pending draft to release — the app-layer post IS the publish (TASK-1.5/1.10). So `DraftReviewActions`'s reason to exist as a SECOND publish step disappears. **Decision: retire the source-layer `DraftReviewActions` for Bitbucket targets; `PostCommentsButton` (app layer) becomes the sole publish control.** What `DraftReviewActions` uniquely contributed and must NOT be lost:
1. **Pre-publish DRAFT PREVIEW** (renders body + inline anchors before the irreversible post) → moves to a new app-layer preview fed by `GET /runs/{id}/draft` (TASK-1.11). Rework `PostCommentsButton`'s confirm step to show this preview, OR keep a slimmed `DraftReviewActions` that reads the app-layer draft GET and calls app-layer post. **Recommendation: keep the component name `DraftReviewActions`, re-point it at the app layer** (draft GET + `/post`), drop the source-layer client + verdict selector. Less churn in `PrReviewDetail.tsx` wiring.
2. **Irreversibility warning** → KEPT, truer on Bitbucket (comments permanent, no delete-reset).
3. **`contentDigest`/`stale` staleness guard** → replaced by Sage-owned `revision` drift: the draft GET returns `stale` (TASK-1.11 decision 5), disable publish + show re-run warning when `stale=true`. Re-checked fail-closed server-side at publish.
4. **`supersededDraft` / `expectedReviewId`** guard (a later run replaced the pending draft) → DROPPED. Bitbucket has no shared pending object to be superseded; the draft store's `hold_for_publish` + record identity (TASK-1.11) is the replacement, enforced server-side.
5. **new-vs-published rendering** → drive off the draft GET's per-item `status: 'new'|'published'` (TASK-1.11 decision 4), NOT off `data.reviewId`. `postedKeys`/`posted_at`/`posted_comments` ALREADY exist on the `Run` type + `PostCommentsButton` (types.ts:96-102) and map straight onto TASK-1.11's `posted` map — the count-what's-left logic (`pendingCommentCount` subtracts `postedKeys[change_id].length`) ports UNCHANGED.

### CORRECTION 3 — the verdict SELECTOR is fused into the source-layer submit; splitting it out is real work
Today `DraftReviewActions` fires COMMENT/REQUEST_CHANGES/APPROVE as one `submitPullRequestReview(..., event, ...)` — publishing comments AND stamping the verdict in ONE GitHub POST. On Bitbucket these are DIFFERENT endpoints (comments = add-comment loop; verdict = Rovo `approveBitbucketRepoPullRequest`/`requestChangesOnBitbucketRepoPullRequest`). So:
- **Drop** the 3-way `PUBLISH_EVENTS` selector from the publish bar. Publish = post comments only, no verdict.
- **Add** a sibling `VerdictActions.tsx` (NOT inside the publish bar): two discrete buttons Approve / Request-changes, each with confirm-before-fire. Placed as a sibling in `PrReviewDetail.tsx` so a habituated hand on Publish never lands on an irreversible verdict (the shipping code's own comment warns about exactly this three-buttons-same-weight hazard). Verdict is independent of publish — a user may verdict without posting comments, or vice versa.
- **`APPROVE`-withheld logic** (`autoMergeArmed`/`staleDismissalEnabled`) is GitHub-specific (`api/client` draft fields) → DROPPED. Bitbucket has no such fields via Rovo and no reviewer-status read op. Verdict handling = confirm-before-fire dialog stating **undo requires the Bitbucket web UI** (Rovo has no un-approve/withdraw op — TASK-1.10 load-bearing constraint).

### WIRE CONTRACTS (the frontend↔backend seam the build implements against)

All on the app layer `/api/apps/code-review-sage`, same-origin session cookie (matches `api.ts`). Errors use the existing `SageApiError` `{error, code}` shape (frontend switches on `code`, never renders `error` prose).

**1. Draft read (pre-publish preview + staleness) — NEW, from TASK-1.11 decision 4**
```
GET /runs/{run_id}/draft?change_id=<cid>
200 → {
  change_id: string,
  revision: string,              // source.commit.hash at run completion (TASK-1.6)
  hold_for_publish: boolean,
  stale: boolean,                // live executeRead on PR head vs stored revision
  summary: { key: "design", body: string, status: "new"|"published" },
  inline: [{ key: string,        // marker id [code-review-sage:<path>#<rule>#<hash8>]
             path: string, from: number|null, to: number|null,
             body: string, status: "new"|"published" }],
  posted: { [marker_id: string]: { comment_id: string, posted_at: string, revision: string } }
}
error codes: draft_not_found, change_not_in_run
```

**2. Publish (post comments) — EXISTING app route, contract UNCHANGED**
```
POST /runs/{run_id}/post   body: { change_id, keys?: string[] }   // keys omitted = all pending
                           or   { groups: [{ change_id, keys? }] } // multi-change, one cycle
200 → { ok: boolean, run_id: string, posting: boolean, pending: number }
error codes: already_posting, nothing_to_post, target_stale (NEW — fail-closed staleness re-check), no_result_record
```
Poster (TASK-1.7) writes the `posted` map back inline on the record (TASK-1.11 decision 1); UI re-reads via the draft GET / run poll. `posting:true` = accepted+running (the endpoint returns before the loop finishes — `api.ts` already documents this for `postCommentGroups`).

**3 & 4. Verdict — NEW app routes wrapping Rovo executeWrite**
```
POST /runs/{run_id}/approve          body: { change_id }
POST /runs/{run_id}/request-changes  body: { change_id }
200 → { ok: boolean, verdict: "approved"|"changes_requested", pr: string }
error codes: verdict_failed, target_not_found, target_stale (optional — verdict is arguably head-agnostic; decide in TASK-1.8 route wiring)
```
Backend resolves `{workspaceId, repoId, prId}` from the run's change record (TASK-1.6: `change_id=BB-<ws>-<repo>-<id>`), calls Rovo `approveBitbucketRepoPullRequest` / `requestChangesOnBitbucketRepoPullRequest` (executeWrite, no verdict/body field). One-way: no un-approve op exists.

### Component change summary
| Component | Change |
|---|---|
| `DraftReviewActions.tsx` | Re-point source layer → app layer (`sageApi` draft GET + `/post`). Drop verdict selector, `contentDigest`, `supersededDraft`/`expectedReviewId`, `autoMerge`/`staleDismissal`. Keep preview + irreversibility warning. Render new-vs-published off `status`; disable on `stale`. |
| `VerdictActions.tsx` (NEW) | Approve / Request-changes buttons, confirm-before-fire, "undo needs Bitbucket web UI" copy. Sibling of the publish bar in `PrReviewDetail.tsx`, not fused. |
| `PostCommentsButton.tsx` | Contract already app-layer + Bitbucket-shaped (`{summary,inline}` work list = `red+yellow+1`). `pendingCommentCount`/`postedKeys` port UNCHANGED. Only i18n prose ("GitHub review") needs Bitbucket wording. |
| `ShipSummaryCard.tsx` | `SHIP_KEY='design'` unchanged, `row.ship_comment` unchanged, `build_ship_comment` platform-neutral. NO code change — the summary is TASK-1.7's always-present marker-bearing comment. Only prose review. |
| `lib/format.ts` | Add `bitbucket.org` + `/pull-requests/N` to `repoOfRun`, `prLabelFromChange`, `prRefFromChange`; extend/guard `sageSourceLink` for Bitbucket provider (or null if `PullRequestPanel` lacks a Bitbucket source impl — TASK-1.10 wrote none, so `sageSourceLink` returning null for bitbucket is the safe default: the source pane simply doesn't render, matching "Bitbucket bypasses the source layer"). |
| Add-PR input regex | Accept `bitbucket.org/{ws}/{repo}/pull-requests/{id}` alongside `github.com/.../pull/N` (lives in `AddReposModal.tsx`/`api.reviewLinks` path — the backend's `detect_platform` from TASK-1.6 is the authority; the UI regex only needs to not REJECT a valid Bitbucket URL). |

### Load-bearing constraints carried forward
- Publish and verdict are SEPARATE user actions on Bitbucket (were one GitHub POST). Never fuse.
- Staleness is Sage-owned (`revision`), checked at GET (badge) and re-checked fail-closed at POST (refuse, `target_stale`).
- Verdicts are one-way (no Rovo withdraw); confirm-before-fire dialog says so.
- `sageSourceLink`→null for Bitbucket is intentional: no Bitbucket source-provider impl exists (TASK-1.10), so the shared source pane stays GitHub-only and Bitbucket runs render from the app layer alone.

**This closes the last major decision ticket.** No fog remains toward the destination — only the handoff-spec assembly.
<!-- SECTION:NOTES:END -->
