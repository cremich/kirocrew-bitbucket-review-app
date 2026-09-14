#!/usr/bin/env python3
"""Review pipeline — deterministic orchestration helpers (design §4).

The token-free backbone the orchestrating session calls between LLM judgment
steps: parse GitHub PR links, resolve a per-repo rule pack, prepare a
ReviewTarget (+ blast radius) for the gate, and build draft-only comment
payloads. The per-change Phase 1/2 judgment runs in a spawned clean session
(one per change) using the code-review-sage ruleset.

CLI subcommands let the agent invoke each step:
    python3 sage_lib/pipeline.py batch "<pasted PR links>"
    python3 sage_lib/pipeline.py rule-pack <repo_identity>
    python3 sage_lib/pipeline.py prepare --link <link> --payload-file <json>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Optional Kiro Crew redaction — scrubs LLM-generated text before it is
# posted to an external surface. Imported at module top (absent only when run
# fully standalone outside the KiroCrew runtime).
try:
    from kiro_crew.security import redact_credentials, redact_exfiltration_urls
except ImportError:  # pragma: no cover - standalone fallback
    redact_credentials = redact_exfiltration_urls = None  # type: ignore

_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:  # allow `python3 sage_lib/pipeline.py` (run as script)
    sys.path.insert(0, _APP_ROOT)

from sage_lib import adapters, blast_radius, discovery, results, store  # noqa: E402

# Identifies a pending review as OURS. The poster matches on it to delete only its
# own stale draft (never a human's in-progress one), and the driver matches on it to
# confirm a delivery it is about to record. One definition so those two readers and
# the writers below cannot drift apart.
DRAFT_MARKER = "[code-review-sage]"


def _redact(text: str) -> str:
    """Scrub credentials + exfiltration URLs before text leaves for an external
    surface. Delegates to `store`, which owns the redactor so readers outside the
    posting path can apply the same scrub; kept under this name because tests and
    other modules patch `pipeline._redact` to observe egress."""
    return store.redact_text(text)


# ---------------------------------------------------------------------------
# Entry point (a): single link  — just normalize(); handled by the adapter.
# Entry point (b): batch paste
# ---------------------------------------------------------------------------

#: Extracts the URL embedded in a pasted token so list markers ("- ", "* "),
#: markdown link syntax ("[PR](https://...)"), autolink brackets ("<https://...>"),
#: and leading prose ("see https://...") don't void the hostname when the whole
#: token is urlparse()d. Extraction only LOCATES the candidate — acceptance is
#: still decided by ``github_pr_ref``'s parsed-hostname allowlist, so a spoof
#: like ``https://evil.example/github.com/x/pull/1`` extracts as-is and is then
#: refused on its parsed host.
_EMBEDDED_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+")


def parse_batch(text: str) -> list[str]:
    """Split a pasted blob (newline/comma separated) into a de-duplicated,
    order-preserving list of PR URLs on allowed GitHub hosts (github.com plus
    any configured GitHub Enterprise host). Non-PR tokens are dropped."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for tok in re.split(r"[\n,]+", text):
        tok = tok.strip()
        if not tok:
            continue
        # Pasted lists from Slack/issues wrap URLs in bullets, markdown, or
        # angle brackets; hand the embedded URL (when present) to the parser
        # instead of the raw token. No match -> raw token, preserving the
        # schemeless "owner/repo/pull/N" case.
        m = _EMBEDDED_URL_RE.search(tok)
        candidate = m.group(0) if m else tok
        try:
            host, owner, repo, number = adapters.github_pr_ref(candidate)
        except adapters.AdapterParseError:
            continue
        link = f"https://{host}/{owner}/{repo}/pull/{number}"
        key = link.lower()
        if key not in seen:
            seen.add(key)
            out.append(link)
    return out


def list_open_prs(owner: str, repo: str, *, host: str = "github.com",
                  timeout: float = 60.0) -> list[dict]:
    """Enumerate a repo's OPEN pull requests via the authenticated ``gh`` CLI.

    Deterministic backbone (no LLM): runs ``gh api`` with a LIST argv (never
    ``shell=True``). ``owner``/``repo`` are constrained to ``[^/]+`` by
    ``adapters.parse_repo_ref`` before this is called and are interpolated only
    into the ``gh api`` PATH argument (which `gh` treats as an API path, not a
    shell command), so there is no shell-injection surface. ``host`` has passed
    the same parsed-hostname allowlist; a non-github.com (GitHub Enterprise)
    host is routed to ITS instance's API via ``--hostname`` (``gh`` must be
    authenticated for it: ``gh auth login --hostname <host>``). Returns
    ``[{url, number, head_sha, title, author, updated_at, draft, labels}]`` in
    GitHub's order. Raises ``RuntimeError`` (with the stderr tail) if `gh` is
    missing, unauthenticated, times out, or the repo can't be read.

    ``labels`` is the PR's label NAMES. It costs no extra request: the REST list
    payload this already asks for carries ``.labels`` inline, so the names are
    projected out of the response in hand rather than fetched per PR or read from
    a separate repo-labels endpoint. Order is GitHub's; a PR with none yields
    ``[]``, never a missing key, so a caller can narrow on it without a
    presence check.

    The ``gh`` binary is resolved through ``discovery.gh_bin()`` — the same
    validated resolution the dashboard's PR panel uses — rather than trusting a
    bare ``gh`` off ``PATH``."""
    path = f"repos/{owner}/{repo}/pulls?state=open&per_page=100"
    try:
        gh = discovery.gh_bin()
    except discovery.GhError as e:
        raise RuntimeError(str(e)) from e
    argv = [
        gh, "api", path, "--paginate",
        # `.labels` rides along in the list payload, so pulling the names here is
        # free. `// []` keeps the projection total: a PR with no labels must emit
        # an empty list rather than `null`, or the JSONL row below would carry a
        # non-list and every consumer would need its own guard.
        "--jq", ".[] | {url: .html_url, number: .number, "
                "head_sha: .head.sha, title: .title, author: .user.login, "
                "updated_at: .updated_at, draft: .draft, "
                "labels: [(.labels // [])[] | .name]}",
    ]
    h = adapters.canonical_host(host)
    # ALWAYS pin the hostname — including github.com. Omitting the flag lets
    # the `gh` CLI's configured default host (GH_HOST / `gh auth`) decide, so
    # a public PR on a machine whose gh defaults to an enterprise instance
    # would list the WRONG instance's PRs.
    if h:
        argv += ["--hostname", h]
    try:
        # Shared spawn chokepoint: trusted binary, minimal env (no gateway
        # secrets), SEL audit on success/failure/timeout.
        proc = discovery._run_gh(argv, timeout=timeout)
    except FileNotFoundError as e:
        raise RuntimeError("the `gh` CLI is not installed on this host") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"`gh` timed out listing open PRs for {owner}/{repo}") from e
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
        raise RuntimeError(
            f"gh api failed for {owner}/{repo} (exit {proc.returncode}): {tail}")
    prs: list[dict] = []
    for line in (proc.stdout or "").splitlines():   # --jq emits JSONL
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        prs.append({
            "url": obj.get("url") or "",
            "number": obj.get("number"),
            "head_sha": obj.get("head_sha") or "",
            "title": obj.get("title") or "",
            "author": obj.get("author") or "",
            "updated_at": obj.get("updated_at") or "",
            "draft": bool(obj.get("draft")),
            # Coerced like every field above rather than trusted: a name is only
            # useful as a non-empty string, and a non-list here (an older `gh`
            # whose jq dropped the projection, a hand-edited response) would
            # otherwise reach the client as the wrong type. A bad value narrows
            # to `[]`, which filters nothing — never to a truthy value that would
            # silently hide this PR from a labelled view.
            "labels": [s for s in (
                obj.get("labels") if isinstance(obj.get("labels"), list) else []
            ) if isinstance(s, str) and s],
        })
    # Non-silent: gh returned 0 but produced non-empty, unparseable output (e.g. a
    # gh build that pretty-prints jq). Don't masquerade that as "no open PRs".
    if not prs and (proc.stdout or "").strip():
        raise RuntimeError(
            f"could not parse `gh` output for {owner}/{repo} "
            "(expected one JSON object per line)")
    return prs


# ---------------------------------------------------------------------------
# Per-repo rule pack resolution (design §4.3 — read-only reuse, the ONLY merge)
# ---------------------------------------------------------------------------

def resolve_rule_pack(pack_name: str) -> str | None:
    """Find a rule-pack SKILL.md by skill name under the KiroCrew skills dir
    (``<config_dir>/skills/`` — ``~/.kiro/crew/skills/`` by default, honoring
    ``KIROCREW_HOME`` — this is where the app framework symlinks app + user
    skills). Prefers the most recently modified copy. Returns an absolute path
    or None."""
    if not pack_name or "/" in pack_name or ".." in pack_name:
        return None
    skills = store.crew_home() / "skills"
    if not skills.exists():
        return None
    candidates = list(skills.glob(f"{pack_name}/SKILL.md"))          # flat link
    candidates += list(skills.glob(f"*/{pack_name}/SKILL.md"))       # namespaced link
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return str(candidates[0])


def rule_pack_for_repo(repo_identity: str, config: dict | None = None) -> str | None:
    """Map a repo to its pack via config, then resolve to a file. None if no pack."""
    cfg = config or store.load_config()
    pack_name = (cfg.get("rule_packs") or {}).get(repo_identity)
    if not pack_name:
        return None
    return resolve_rule_pack(pack_name)


# ---------------------------------------------------------------------------
# Prepare a target for the Phase 1 gate (normalize + blast radius)
# ---------------------------------------------------------------------------

def prepare_target(link: str, raw_payload: dict | str, config: dict | None = None) -> dict:
    """Normalize a link+payload into a ReviewTarget and attach blast-radius
    signals + the resolved rule-pack path. This is the gate's input bundle.

    For a Bitbucket link the ``(workspace, repo)`` is revalidated against the
    fail-closed ``store.allowed_targets`` allowlist here — a defence-in-depth
    backstop to ``assert_bitbucket_allowed`` (which fires BEFORE the fetch). A
    target outside the allowlist raises ``adapters.UnsupportedPlatform`` rather
    than being reviewed."""
    cfg = config or store.load_config()
    if _is_bitbucket_link(link):
        assert_bitbucket_allowed(link, cfg)
    target = adapters.normalize(link, raw_payload)
    radius = blast_radius.analyze(target.files, cfg.get("sensitive_globs", []))
    return {
        "target": target.to_dict(),
        "blast_radius": radius,
        "rule_pack": rule_pack_for_repo(target.repo_identity, cfg),
        "warnings": adapters.validate_review_target(target),
    }


def _is_bitbucket_link(link: str) -> bool:
    """Whether ``link`` is a Bitbucket Cloud PR URL (host + /pull-requests/), read
    WITHOUT config so it can gate the config-scoped allowlist check itself."""
    try:
        return adapters.detect_platform(link) == "bitbucket"
    except adapters.UnsupportedPlatform:
        return False


def assert_bitbucket_allowed(link: str, config: dict | None = None) -> tuple[str, str, str]:
    """Reject a Bitbucket PR link whose ``(workspace, repo)`` is not in the
    configured allowlist — BEFORE any fetch is issued. Returns the parsed
    ``(workspace, repo, id)`` on success so the caller need not re-parse.

    The single scope gate for Bitbucket targets. ``store.allowed_targets`` is
    fail-closed: an unconfigured Sage resolves the empty set, so EVERY Bitbucket
    link is refused until an operator adds its repo to ``bitbucket_repos``. The
    driver must call this before building the fetch instruction so an out-of-scope
    PR is never fetched, normalized, or reviewed. Matching is case-insensitive by
    exact ``(ws, repo)`` pair equality (Bitbucket slug semantics), never a
    substring test. Raises ``adapters.UnsupportedPlatform`` for a rejected or
    unparseable link."""
    cfg = config if config is not None else store.load_config()
    try:
        workspace, repo, number = adapters.bitbucket_pr_ref(link)
    except adapters.AdapterParseError as exc:
        raise adapters.UnsupportedPlatform(str(exc)) from exc
    allowed = store.allowed_targets(cfg)
    if (workspace.strip().lower(), repo.strip().lower()) not in allowed:
        raise adapters.UnsupportedPlatform(
            f"Bitbucket target out of scope: {workspace}/{repo} is not in the "
            "configured `bitbucket_repos` allowlist (fail-closed — add it to "
            "review this PR)")
    return workspace, repo, number


# ---------------------------------------------------------------------------
# Draft-only comment posting — platform-keyed (hard rule: publish is ALWAYS False)
# ---------------------------------------------------------------------------

# Posting tool + anchoring hint, surfaced into the deep-review prompt. GitHub is
# the only platform: findings post to a PENDING (draft) review via the `gh` CLI.
POSTING_SPECS = {
    "github": {
        "tool": "one `gh api --method POST repos/<owner>/<repo>/pulls/<n>/reviews` "
                "call with NO `event` key (creates a PENDING, unsubmitted review)",
        "anchor": "a comments[] entry {path, line, side:'RIGHT'} against commit_id=<head SHA>",
        "top_anchor": "the review `body` field (a general summary on the pending review)",
    },
}


def posting_spec(platform: str) -> dict:
    """Posting tool + anchoring hint for a platform (GitHub is the only platform)."""
    return POSTING_SPECS.get(platform, POSTING_SPECS["github"])


# FETCH instruction surfaced into the gate/deep prompts so the worker retrieves
# the change BEFORE normalizing. GitHub uses the ``gh`` CLI (the repo may be
# private, so ``gh`` must be authenticated on the host). Symmetric with
# ``posting_spec`` — a new platform adds an entry, not a prompt rewrite.
FETCH_SPECS = {
    "github": (
        "use the `gh` CLI to fetch the PR (the repo may be PRIVATE, so `gh` must "
        "be authenticated on this host). Parse <owner>/<repo>/<number> from the "
        "URL, then run `gh api repos/<owner>/<repo>/pulls/<number>` (PR metadata "
        "including head.sha) and `gh api repos/<owner>/<repo>/pulls/<number>/files "
        "--paginate` (per-file `patch` diffs). Merge them into ONE JSON object of "
        'the form {...pull, "files":[{filename, patch}], "comments":[...]} and pass '
        "THAT object as the payload"
    ),
    "bitbucket": (
        "use the Atlassian Rovo MCP to fetch the PR. FIRST discover the available "
        "Bitbucket tools at runtime (call `discover`), then call the get-pull-request "
        "tool (PR metadata: id, title, summary, author.nickname, source.commit.hash, "
        "destination.branch.name), the get-diff tool (the WHOLE PR unified diff as one "
        "text blob — Bitbucket does NOT return a per-file array), and the get-comments "
        "tool. Use workspaceId=<workspace> and repoId=<repo> from the URL "
        "(bitbucket.org/<workspace>/<repo>/pull-requests/<id>). Merge them into ONE "
        'JSON object of the form {...pull, "diff":"<whole unified diff>", '
        '"comments":[...]} and pass THAT object as the payload. Do NOT call the '
        "Bitbucket REST API directly; drive everything through the discovered Rovo tools"
    ),
}


def fetch_spec(platform: str, host: str = "github.com") -> str:
    """FETCH instruction for a platform (GitHub or Bitbucket Cloud).

    For a GitHub Enterprise host the instruction routes every ``gh api`` call to
    that instance's API via ``--hostname`` — the host has already passed the
    adapters' parsed-hostname allowlist, so it is safe to interpolate. Bitbucket
    Cloud is single-host, so it takes no host argument and returns its
    Rovo-discovery instruction verbatim."""
    if platform == "bitbucket":
        return FETCH_SPECS["bitbucket"]
    spec = FETCH_SPECS.get(platform, FETCH_SPECS["github"])
    h = adapters.canonical_host(host)
    if h:
        # ALWAYS name the host — including github.com — so the worker's gh
        # calls can never drift to the CLI's configured default instance.
        spec += (
            f". The PR lives on the GitHub host `{h}`: add "
            f"`--hostname {h}` to EVERY `gh api` call"
        )
        if h != "github.com":
            spec += (
                f" (`gh` must be authenticated for that host: "
                f"`gh auth login --hostname {h}`)"
            )
    return spec


def _comment_body(finding: dict) -> str:
    """Build the platform-neutral comment body (redacted). Shared across platforms.

    When the finding carries a ``headline`` it leads the body in bold, and the
    observation follows as its own paragraph. The headline is the one line a
    reader on a busy pull request is guaranteed to see, so it belongs above the
    evidence rather than buried as the first sentence of it. A record without a
    headline (any review predating the field) renders exactly as before — the
    observation leads — so old records keep posting unchanged.
    """
    sev = "🔴" if finding.get("severity") == "red" else "🟡"
    lang = finding.get("lang", "")
    snippet = finding.get("snippet", "")
    headline = str(finding.get("headline", "") or "").strip()
    observation = str(finding.get("observation", "") or "").strip()
    lead = f"{sev} **{headline}**\n\n{observation}" if headline else f"{sev} {observation}"
    body = (
        f"{lead}\n\n"
        f"```{lang}\n{snippet}\n```\n\n"
        f"**Why it matters:** {finding.get('consequence', '').strip()}\n\n"
        f"**Suggestion:** {finding.get('suggestion', '').strip()}\n\n"
        f"_{DRAFT_MARKER}_"
    )
    # Redact LLM-generated content before it leaves for an external surface.
    return _redact(body)


def build_comment_payload(finding: dict, change_id: str, revision: str,
                          platform: str = "github") -> dict:
    """Build a DRAFT-only comment payload from a finding. ``publish`` is ALWAYS
    False (draft-only safety). For GitHub this is the single-finding anchor shape
    ({path, line, side} against the head commit SHA); the full pending review is
    assembled by ``build_review_payload``."""
    body = _comment_body(finding)
    if platform == "github":
        line = int(finding.get("line", 0) or 0)
        return {
            "path": finding.get("file", ""),
            "line": line,
            "side": "RIGHT",
            "commit_id": revision,
            "content": body,
            "publish": False,  # NON-NEGOTIABLE — a human submits the pending review.
        }
    raise ValueError(f"unsupported posting platform: {platform!r}")


def build_ship_comment(record: dict) -> str:
    """Build the (redacted) top-level SHIP-READINESS comment body — posted on EVERY
    review. The ship decision keys on 🔴 must-fix ONLY: the change is good to ship
    iff there are zero 🔴 findings AND the design verdict is not BLOCK. 🟡 should-fix
    findings are surfaced (inline + counted) but never gate the ship call. The
    verdict header + counts are deterministic; the one-line reason comes from the
    reviewer-recorded ``ship_summary`` (falling back to the design headline). Python-
    authored + ``_redact``-scrubbed so the body is clean before it reaches the CR."""
    p1 = record.get("phase1", {}) or {}
    verdict = str(p1.get("gate_verdict", "")).upper()
    counts = record.get("counts", {}) or {}
    red = int(counts.get("red", 0) or 0)
    yellow = int(counts.get("yellow", 0) or 0)
    design_block = verdict == "BLOCK"
    ready = red == 0 and not design_block

    reason = str(record.get("ship_summary") or "").strip()
    if not reason:
        # Records without a ship_summary: fall back to the design headline, else a
        # deterministic phrase, so the comment always states a reason.
        headline = str(p1.get("design_headline") or "").strip()
        if headline:
            reason = headline
        elif ready:
            reason = "No blocking must-fix issues found."
        elif design_block:
            reason = "Design needs rework before shipping."
        else:
            reason = f"{red} blocking must-fix issue(s) to resolve first."

    header = "✅ **Good to ship**" if ready else "🔴 **Not ready to ship**"
    tally: list[str] = []
    if red:
        tally.append(f"{red} must-fix 🔴")
    if yellow:
        tally.append(f"{yellow} should-fix 🟡 (non-blocking)")
    if design_block:
        tally.append("design flagged")
    tally_line = ("\n" + " · ".join(tally)) if tally else ""
    body = f"{header}: {reason}{tally_line}\n\n_{DRAFT_MARKER}_"
    return _redact(body)


def build_pending_comments(record: dict) -> list[dict]:
    """Build the full set of DRAFT comments for a reviewed change, with every body
    Python-redacted HERE (the deterministic chokepoint). The poster worker posts
    each ``body`` VERBATIM — it only resolves the (non-sensitive) anchor. Entries:
      - finding: ``{"kind": "finding", "file", "line", "body"}``  (line-anchored,
        one per surviving 🔴/🟡 finding; nice-to-haves are already dropped upstream)
      - design:  ``{"kind": "design", "body"}``                   (top-level)
    The top-level entry is the SHIP-READINESS comment and is ALWAYS emitted (on a
    clean PASS it states "good to ship"), so the author always gets a straight
    ship / no-ship call with the reason. It keeps kind ``design`` for the top-level
    anchor + posting accounting."""
    out: list[dict] = []
    for i, f in enumerate(record.get("findings", []) or []):
        out.append({
            "kind": "finding",
            # Stable identity for selective posting: the record is frozen once the
            # review has run, and the report rows are generated from the same list
            # in the same order, so the index is a durable handle the UI can name
            # one comment by. Callers filter on this; nothing else keys off it.
            "key": f"finding:{i}",
            "file": str(f.get("file", "")),
            "line": int(f.get("line", 0) or 0),
            "body": _comment_body(f),   # _comment_body already applies _redact
        })
    out.append({"kind": "design", "key": "design",
                "body": build_ship_comment(record)})
    return out


def review_payload_units(payload: dict) -> int:
    """How many deliverable units a GitHub review payload actually contains.

    The poster is instructed to write ``posted_comments = len(comments) + 1 when
    body is non-empty``, so delivery evidence is counted in PAYLOAD UNITS. The
    number of FINDINGS is a different quantity and must not be compared against
    it: a finding with no usable ``{path, line}`` anchor is folded into the review
    body rather than becoming its own inline comment (see
    ``build_review_payload``). One unanchored finding would make a complete
    delivery look short, and the caller would re-post comments already on the pull
    request.

    This is the single place that number is derived, so the comparison in
    ``post_recorded`` and the durable ``posting_expected`` cannot drift apart.
    """
    return len(payload.get("comments") or []) + (1 if payload.get("body") else 0)


def build_review_payload(record: dict) -> dict:
    """Assemble the payload for ONE ``gh api POST .../pulls/<n>/reviews`` call from
    a record's ``pending_comments``. The result deliberately has **no** ``event``
    key, so GitHub creates the review as PENDING (unsubmitted) — the GitHub
    equivalent of a draft (``publish=False``) invariant; a human submits it.

    Bodies are taken VERBATIM from ``pending_comments`` (already Python-redacted by
    ``build_pending_comments`` — the deterministic chokepoint); this only builds the
    envelope + anchors, so no LLM free-text is composed here. The ``design`` entry
    becomes the review ``body``; each ``finding`` becomes an inline ``comments[]``
    entry anchored on ``{path, line, side:'RIGHT'}`` against the head commit SHA
    (``record['revision']``). GitHub rejects inline comments outside the diff, so a
    finding lacking a usable ``{path, line}`` anchor is folded into the review body
    rather than dropped (design note §4 known constraint)."""
    pending = record.get("pending_comments") or []
    # commit_id (revision) is written by the LLM worker, so redact it too before it
    # reaches the GitHub API — same egress treatment as body/path (idempotent; a
    # real SHA never matches credential/URL patterns).
    commit_id = _redact(str(record.get("revision", "") or "")).strip()
    body_parts: list[str] = []
    comments: list[dict] = []
    for e in pending:
        kind = e.get("kind")
        # Defense-in-depth: bodies are already redacted at the deterministic
        # chokepoint (build_pending_comments -> _comment_body / build_ship_comment,
        # each of which runs _redact = redact_exfiltration_urls + redact_credentials).
        # Re-run _redact here — it is idempotent — so this external-egress point is
        # self-evidently safe even if a caller passes unredacted pending_comments.
        text = _redact(e.get("body", "") or "")
        if kind == "design":
            if text:
                body_parts.insert(0, text)   # the ship-readiness summary leads the body
            continue
        # `file` is LLM-derived and goes to an external surface, so redact it too
        # (idempotent; real file paths never match credential/URL patterns, and a
        # prompt-injected path is neutralized — a mangled path just fails GitHub's
        # in-diff anchor check, which is fail-safe).
        path = _redact(str(e.get("file", "") or ""))
        line = int(e.get("line", 0) or 0)
        if path and line > 0:
            comments.append({"path": path, "line": line, "side": "RIGHT", "body": text})
        elif text:
            body_parts.append(text)          # unanchored finding -> folded into the body
    payload: dict = {"body": "\n\n".join(p for p in body_parts if p), "comments": comments}
    if not commit_id:
        # Refuse rather than post unanchored. GitHub defaults a review with no
        # `commit_id` to the pull request's CURRENT head, which silently breaks the
        # invariant the whole submit path rests on: that a draft is bound to the head
        # it was written against. The submit guard's stale-head check then compares
        # the draft's head to the live head and passes trivially — they match because
        # GitHub stamped it at post time, not because anything reviewed that code.
        # `APPROVE` would authorize a head no review ever looked at.
        #
        # `revision` is not in the result contract's required keys, so a
        # contract-valid record can reach here without one; that is exactly the
        # case this refuses. Raising here rather than downstream keeps it a property
        # of the payload: no caller can construct an unanchored review.
        raise ValueError(
            "refusing to build a review payload with no commit_id: the record has no "
            "`revision`, and GitHub would anchor the draft to the current head "
            "instead of the reviewed one"
        )
    payload["commit_id"] = commit_id         # anchors comments to the reviewed head
    # NOTE: intentionally NO "event" key -> the review stays PENDING (unsubmitted).
    return payload


# ---------------------------------------------------------------------------
# Bitbucket Cloud publish payload (TASK-1.13.9)
# ---------------------------------------------------------------------------
#
# Bitbucket has NO single-POST PENDING review and NO delete-reset (a posted comment
# is permanent). So the shape and the idempotency model diverge from GitHub's:
#   * The review is a WORK LIST posted one Rovo call at a time — an always-present
#     marker-bearing SUMMARY comment (canonical) plus one INLINE comment per
#     Critical/High (🔴) finding only. 🟡 findings are surfaced in the summary tally
#     (via build_ship_comment) but never post their own inline comment.
#   * Inline {path, from, to} anchors are derived HERE from the diff-split hunk
#     headers: an ADDED line anchors on `to` (new side), any other located line on
#     `from` (old side). A finding that cannot be located in any hunk FOLDS INTO the
#     summary so none is silently dropped.
#   * Every posted comment carries a hidden per-comment MARKER
#     `[code-review-sage:<path>#<rule>#<hash8>]`. It is deliberately line-drift
#     tolerant (the hash is over path+rule+body, NOT the line), so a fix that shifts
#     lines does not resurrect a comment, and it is the RECOVERY path when Sage's own
#     `posted` ledger is lost. `<path>` for the summary is the sentinel ``__summary__``.

# Match the GitHub draft marker's shape but carry a stable per-comment identity, so
# a re-publish can recognise an existing Sage comment on the pull request even when
# the local `posted` ledger is gone. The `#`/`:` separators are characters a repo
# path can contain but the parser splits on the FIRST two only, so a path with `#`
# is tolerated.
_BB_MARKER_RE = re.compile(
    r"\[code-review-sage:(?P<path>.*?)#(?P<rule>[^#]*)#(?P<hash>[0-9a-f]{8})\]")
_BB_SUMMARY_PATH = "__summary__"


def _marker_hash(path: str, rule: str, body: str) -> str:
    """8-hex stable identity for a Bitbucket comment: sha over path+rule+body.

    Deliberately NOT line-anchored — a fix that only shifts a line must not make an
    already-posted comment look new (line-drift tolerance). Body is included so a
    reworded finding on the same path/rule posts as a distinct comment rather than
    silently updating the old text under a stale identity."""
    import hashlib
    raw = f"{path}\x00{rule}\x00{body}".encode("utf-8", "replace")
    return hashlib.sha256(raw).hexdigest()[:8]


def bitbucket_marker(path: str, rule: str, body: str) -> str:
    """The hidden per-comment marker embedded in every posted Bitbucket comment:
    ``[code-review-sage:<path>#<rule>#<hash8>]``. This IS the marker_id used as the
    key in the record's ``posted`` ledger, and what a re-publish greps existing PR
    comments for to skip/update rather than duplicate."""
    return f"[code-review-sage:{path}#{rule}#{_marker_hash(path, rule, body)}]"


def parse_bitbucket_marker(text: str) -> str:
    """Return the FULL ``[code-review-sage:...]`` marker found in a comment body, or
    ``""``. Used by the re-publish recovery path to match an existing PR comment to a
    marker_id in the ``posted`` ledger without depending on Sage's local store."""
    m = _BB_MARKER_RE.search(text or "")
    return m.group(0) if m else ""


def _diff_line_side(diff: str, line: int) -> str | None:
    """Locate ``line`` in a file's unified diff and say which SIDE it is on.

    Walks the ``@@ -a,b +c,d @@`` hunk headers and counts, per hunk, the NEW-side
    line number for added/context lines and the OLD-side number for removed/context
    lines. Returns ``"to"`` when ``line`` is the new-side number of an ADDED (``+``)
    line, ``"from"`` when it is the old-side number of a REMOVED (``-``) line, and
    ``None`` when ``line`` cannot be located in any hunk (unanchorable -> folds into
    the summary). Context lines are matched on their new-side number as ``"to"`` so a
    finding on an unchanged-but-shown line still anchors on the side Bitbucket accepts."""
    if not diff or line <= 0:
        return None
    old_ln = new_ln = 0
    in_hunk = False
    for raw in diff.splitlines():
        m = re.match(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            old_ln = int(m.group(1))
            new_ln = int(m.group(2))
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            if new_ln == line:
                return "to"
            new_ln += 1
        elif raw.startswith("-") and not raw.startswith("---"):
            if old_ln == line:
                return "from"
            old_ln += 1
        elif raw.startswith("\\"):    # "\ No newline at end of file" — no line number
            continue
        else:                          # context line: advances BOTH sides
            if new_ln == line:
                return "to"
            old_ln += 1
            new_ln += 1
    return None


def bitbucket_anchor(files: list[dict], path: str, line: int) -> dict | None:
    """Derive the Bitbucket inline anchor ``{from|to}`` for a finding on ``path`` at
    ``line`` by walking the matching file's diff-split hunk headers.

    ``files`` is the record's ``[{path, diff}]`` (as produced by
    ``adapters.split_unified_diff``). Returns ``{"to": line}`` for an added/context
    line, ``{"from": line}`` for a removed line, or ``None`` when the file is not in
    the diff or the line is outside every hunk — the caller then folds that finding
    into the summary instead of dropping it."""
    if not path or line <= 0:
        return None
    for f in files or []:
        if str(f.get("path") or "") != path:
            continue
        side = _diff_line_side(str(f.get("diff") or ""), int(line))
        if side == "to":
            return {"to": int(line)}
        if side == "from":
            return {"from": int(line)}
        return None      # right file, line not in any hunk -> unanchorable
    return None          # file not in the diff -> unanchorable


def _finding_rule(finding: dict, index: int) -> str:
    """A stable rule token for a finding's marker: prefer the ``dimension`` field
    (the review dimension that fired), falling back to the finding's list index so
    two findings on the same path with no dimension still get distinct markers."""
    dim = str(finding.get("dimension") or "").strip()
    dim = re.sub(r"\s+", "-", dim).lower()
    return dim or f"finding-{index}"


def _append_to_summary(summary_body: str, folded: list[str]) -> str:
    """Append folded (unanchorable) findings to the summary body so none is dropped.

    Each folded finding is added under a short heading, keeping the marker OUT of the
    per-finding text (the summary carries its OWN marker). The redactor has already
    run on both the summary and each finding body upstream; re-running is idempotent."""
    if not folded:
        return summary_body
    tail = "\n\n---\n\n**Additional findings (could not be anchored to a diff line):**\n\n"
    tail += "\n\n".join(folded)
    return _redact(summary_body + tail)


def build_bitbucket_publish(record: dict) -> dict:
    """Assemble the Bitbucket publish WORK LIST from a recorded review.

    Returns ``{"summary": {...}, "inline": [...]}`` where:
      * ``summary`` is ALWAYS present and canonical:
        ``{"key": "design", "marker": <marker>, "body": <ship-comment + folded>}``.
      * ``inline`` is one entry per anchorable Critical/High (🔴) finding:
        ``{"key": "finding:<i>", "path", "from"|"to", "marker", "body"}``.

    🔴-only for inline (🟡 are reflected in the summary tally, not their own comment),
    per TASK-1.5's shape. A 🔴 finding whose line cannot be located in the diff FOLDS
    INTO the summary rather than being dropped. Bodies are taken from the same
    ``_comment_body`` / ``build_ship_comment`` builders GitHub uses (already
    ``_redact``-scrubbed — the deterministic chokepoint); this only adds the marker
    and resolves the anchor. Each ``key`` mirrors ``build_pending_comments`` so the
    ``posted``/``posted_keys`` ledgers align with the draft-preview GET."""
    files = record.get("files") or []
    inline: list[dict] = []
    folded: list[str] = []
    for i, f in enumerate(record.get("findings", []) or []):
        # Inline comments are for 🔴 (Critical/High) only. 🟡 are surfaced in the
        # summary's tally line (build_ship_comment counts them) but do not each post.
        if str(f.get("severity") or "") != "red":
            continue
        body = _comment_body(f)
        path = _redact(str(f.get("file") or ""))
        line = int(f.get("line", 0) or 0)
        rule = _finding_rule(f, i)
        anchor = bitbucket_anchor(files, path, line) if path else None
        marker = bitbucket_marker(path or _BB_SUMMARY_PATH, rule, body)
        marked_body = _redact(f"{body}\n\n<!-- {marker} -->")
        if anchor is not None:
            entry = {
                "kind": "finding",
                "key": f"finding:{i}",
                "path": path,
                "marker": marker,
                "body": marked_body,
            }
            entry.update(anchor)            # {"to": n} or {"from": n}
            inline.append(entry)
        else:
            # Unanchorable 🔴 -> fold into the summary so it still reaches the author.
            folded.append(f"{body}\n\n<!-- {marker} -->")

    ship_body = build_ship_comment(record)
    summary_marker = bitbucket_marker(_BB_SUMMARY_PATH, "summary", ship_body)
    summary_body = _append_to_summary(ship_body, folded)
    summary_body = _redact(f"{summary_body}\n\n<!-- {summary_marker} -->")
    summary = {
        "kind": "design",
        "key": "design",
        "marker": summary_marker,
        "body": summary_body,
    }
    return {"summary": summary, "inline": inline}


def bitbucket_publish_units(payload: dict) -> int:
    """How many deliverable units a Bitbucket publish work list contains: the
    always-present summary plus every inline comment. The Bitbucket analogue of
    ``review_payload_units`` — the single place the expected count is derived so the
    driver's delivery check cannot drift from the builder."""
    return 1 + len(payload.get("inline") or [])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Code Review Sage pipeline helpers")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("batch").add_argument("text")

    sub.add_parser("rule-pack").add_argument("repo_identity")

    pp = sub.add_parser("prepare")
    pp.add_argument("--link", required=True)
    pp.add_argument("--payload-file", required=True)

    sub.add_parser("list-results")

    args = ap.parse_args(argv)

    if args.cmd == "batch":
        print(json.dumps(parse_batch(args.text), indent=2))
    elif args.cmd == "rule-pack":
        print(json.dumps({"rule_pack": rule_pack_for_repo(args.repo_identity)}))
    elif args.cmd == "prepare":
        payload = json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
        print(json.dumps(prepare_target(args.link, payload), indent=2))
    elif args.cmd == "list-results":
        print(json.dumps(results.list_results(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_main())
