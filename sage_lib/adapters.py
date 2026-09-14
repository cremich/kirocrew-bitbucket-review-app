#!/usr/bin/env python3
"""Source adapter — normalize a GitHub PR link into a single ``ReviewTarget``.

The brain only ever sees a ``ReviewTarget``. Adding a new platform later is a new
adapter, not a brain change. This build ships the **GitHub PR** adapter only.

The network fetch itself is performed by the pipeline via the ``gh`` CLI; this
module is the deterministic, token-free part: parsing the fetched payload into a
``ReviewTarget``.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import urlparse

from sage_lib import store

# Matches the PR path grammar shared by github.com and GitHub Enterprise Server:
# /<owner>/<repo>/pull/<number>[...]. Applied to the PARSED URL path only, AFTER
# the hostname allowlist check — never to the raw link.
_PR_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)/pull/(\d+)")
# A change is a "fix" if its title/description signals a bug/revert/incident.
FIX_RE = re.compile(r"\b(fix(es|ed)?|revert(s|ed)?|bug|hotfix|regression|incident|patch)\b", re.I)
# GitHub-style issue reference (e.g. "#204") linked from the PR body.
GH_ISSUE_RE = re.compile(r"#(\d+)")

# Bitbucket Cloud PR path grammar: /<workspace>/<repo>/pull-requests/<id>[...].
# Hyphenated + plural — deliberately distinct from GitHub's /pull/<n>. Applied to
# the PARSED URL path only, AFTER host validation — never to the raw link.
_BB_PR_PATH_RE = re.compile(r"^/([^/]+)/([^/]+)/pull-requests/(\d+)")
# Bitbucket Cloud's only host. A constant (not an inline literal) keeps the
# host equality test from reading as a URL-substring check.
_BITBUCKET_HOST = "bitbucket.org"
# A Jira issue key linked from the PR summary (e.g. "CONTENT-204"). Bitbucket has
# no GitHub-style "#N" issue refs; work items are Jira keys.
JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")


class AdapterError(ValueError):
    """Base class for adapter failures (fail-fast)."""


class UnsupportedPlatform(AdapterError):
    """The link's platform is not supported in this build."""


class AdapterParseError(AdapterError):
    """The fetched payload could not be normalized into a ReviewTarget."""


@dataclass
class ReviewTarget:
    """The single normalized shape the review brain consumes."""

    platform: str
    repo_identity: str          # host/org/repo — the learning key
    change_id: str
    url: str
    title: str = ""
    description: str = ""
    linked_issue: str = ""
    author: str = ""
    target_branch: str = ""
    revision: str = ""
    files: list[dict] = field(default_factory=list)        # [{path, diff}]
    existing_comments: list[dict] = field(default_factory=list)
    design_discussion: list[dict] = field(default_factory=list)
    is_fix: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

# Canonical public-GitHub hostnames. Named constants (rather than inline
# literals) keep membership tests on the parsed-host SET from reading as
# URL-substring checks — the sets these appear in hold bare hostnames, never
# URLs.
_GITHUB_HOST = "github.com"
_WWW_GITHUB_HOST = "www.github.com"


def canonical_host(host: str) -> str:
    """``www.github.com`` -> ``github.com``; otherwise the lowercased hostname.

    Persisted identities (``repo_identity``, change ids, reviewed keys) use the
    canonical form so the two spellings of github.com map to one record."""
    h = (host or "").strip().lower()
    return _GITHUB_HOST if h == _WWW_GITHUB_HOST else h


def _urlparse_host_path(text: str) -> tuple[str, str]:
    """Parse ``text`` into ``(hostname, path)``, reading a malformed URL as
    having neither.

    ``urlparse`` raises ``ValueError`` on some malformed input (an unmatched
    ``[`` is "Invalid IPv6 URL"), and ``.hostname`` can raise on a malformed
    netloc — both reachable from user-pasted link text, where one bad link must
    read as "not a link" (default-deny) rather than crash the whole request."""
    try:
        parsed = urlparse(text)
        return (parsed.hostname or "").lower(), parsed.path or ""
    except ValueError:
        return "", ""


def allowed_hosts(config: dict | None = None) -> frozenset[str]:
    """The exact set of hostnames accepted as GitHub-API-compatible.

    The single resolution point for "which hosts are acceptable". Sourced from
    ``config.json``'s ``github_hosts`` list — GitHub Enterprise Server hosts are
    opt-in, mirroring ``gh auth login --hostname`` — and defaults to github.com,
    so behaviour is unchanged when nothing is configured. ``www.github.com`` is
    accepted whenever ``github.com`` is.

    Membership tests against this set MUST use the PARSED URL hostname and
    exact equality — never a substring, suffix, or regex-on-raw-URL test — so
    ``notgithub.com``, ``github.com.evil.example``, and a permitted host that
    appears only in a URL's path are all refused."""
    cfg = config if config is not None else store.read_config_quiet()
    raw = cfg.get("github_hosts") if isinstance(cfg, dict) else None
    hosts: set[str] = set()
    if isinstance(raw, (list, tuple)):
        for entry in raw:
            h = str(entry or "").strip().lower()
            if "://" in h:  # tolerate a pasted URL in config (malformed -> skipped)
                h = _urlparse_host_path(h)[0]
            h = h.strip("/").rstrip(".")
            if h:
                hosts.add(h)
    if not hosts:
        hosts = set(store.DEFAULT_GITHUB_HOSTS)
    # `hosts` holds bare hostnames (never URLs); this is exact set membership.
    # The two public-GitHub spellings imply each other: `www.github.com`
    # canonicalizes to `github.com` downstream, so a www-only config must also
    # accept the canonical form or accepted links would fail to round-trip.
    if hosts & {_GITHUB_HOST, _WWW_GITHUB_HOST}:
        hosts.add(_GITHUB_HOST)
        hosts.add(_WWW_GITHUB_HOST)
    return frozenset(hosts)


def detect_platform(link: str, *, config: dict | None = None) -> str:
    """Return ``github`` for a PR link on an allowed GitHub host, else raise
    UnsupportedPlatform.

    The host is validated against the ``allowed_hosts()`` allowlist by EXACT
    match of the PARSED URL hostname (not a substring of the raw link), so a
    URL where an allowed host merely appears in the path/query/userinfo (e.g.
    ``https://evil.example/github.com/x/pull/1``) or as a spoofable
    prefix/suffix (``notgithub.com``, ``github.com.evil.example``) is rejected,
    and a malformed URL reads as unsupported rather than raising ``ValueError``.
    Aligns with SSRF/allowlist guidance (parse to components, default-deny)."""
    if not link or not isinstance(link, str):
        raise UnsupportedPlatform("empty or non-string link")
    host, path = _urlparse_host_path(link)
    if host in allowed_hosts(config) and "/pull/" in path:
        return "github"
    # Bitbucket Cloud: single fixed host, hyphenated+plural PR segment. The
    # GitHub branch above is checked first and stays untouched; a Bitbucket link
    # can never satisfy it (github.com != bitbucket.org, and /pull-requests/ is
    # not /pull/).
    if host == _BITBUCKET_HOST and "/pull-requests/" in path:
        return "bitbucket"
    raise UnsupportedPlatform(f"unsupported link/platform: {link!r} (expected a GitHub or Bitbucket PR URL)")


def _sanitize_seg(s: str) -> str:
    """Make an owner/repo segment safe for use in a change-id (which names a
    result record on disk). Non ``[A-Za-z0-9.]`` chars — including ``-`` — become
    ``_``. Excluding ``-`` is deliberate: ``-`` is the segment delimiter in
    ``github_change_id`` (``GH-<owner>-<repo>-<n>``), so keeping it inside a
    segment would make different owner/repo pairs collide (e.g. ``a-b``/``c`` vs
    ``a``/``b-c``). Stripping it to ``_`` keeps ``-`` unambiguous as the delimiter."""
    return re.sub(r"[^A-Za-z0-9.]", "_", str(s or "")).strip("_") or "unknown"


def github_pr_ref(link: str, *, config: dict | None = None) -> tuple[str, str, str, str]:
    """Parse ``(host, owner, repo, number)`` from a PR URL on an allowed GitHub
    host. Fails fast.

    Host membership uses the same PARSED-hostname exact-match allowlist as
    ``detect_platform`` (never a substring of the raw link). The host comes
    back canonicalized (``www.github.com`` -> ``github.com``) so identities
    derived from it are stable. A scheme-less link (``github.com/o/r/pull/1``)
    is tolerated by retrying with ``https://``; a malformed link is rejected
    like any other non-PR link (never a ``ValueError`` out of ``urlparse``)."""
    if not link or not isinstance(link, str):
        raise AdapterParseError(f"not a GitHub PR link: {link!r}")
    text = link.strip()
    host, path = _urlparse_host_path(text)
    if not host and "://" not in text:
        host, path = _urlparse_host_path("https://" + text)
    if host not in allowed_hosts(config):
        raise AdapterParseError(f"not a GitHub PR link: {link!r}")
    m = _PR_PATH_RE.match(path)
    if not m:
        raise AdapterParseError(f"not a GitHub PR link: {link!r}")
    owner, repo, number = m.group(1), m.group(2), m.group(3)
    repo = re.sub(r"\.git$", "", repo)  # tolerate a trailing .git
    return canonical_host(host), owner, repo, number


def github_pr_parts(link: str) -> tuple[str, str, str]:
    """Parse ``(owner, repo, number)`` from a PR URL on an allowed GitHub host.
    Fails fast. Callers that need the host use ``github_pr_ref``."""
    _host, owner, repo, number = github_pr_ref(link)
    return owner, repo, number


def link_names_a_host(link: str) -> bool:
    """Whether ``link`` plausibly NAMES a network host — an explicit
    ``scheme://`` form (even with an unparseable host) or a leading
    domain-shaped segment (``ghe.corp/…``).

    Callers use this to distinguish a URL whose host failed validation (must
    FAIL CLOSED — routing it at a default host could cross GitHub instances)
    from a bare legacy change token (``CR-1``) that carries no host to cross
    to. Deliberately over-matches: a dot in the first segment reads as a
    domain, because refusing is the safe side."""
    if not link or not isinstance(link, str):
        return False
    text = link.strip()
    if _urlparse_host_path(text)[0]:
        return True
    if "://" in text:  # a scheme is present but the host is unparseable
        return True
    return "." in text.split("/", 1)[0]


def parse_repo_ref(link: str, *, config: dict | None = None) -> tuple[str, str, str]:
    """Parse ``(host, owner, repo)`` from a GitHub REPO URL (no ``/pull/``).

    Mirrors ``detect_platform``'s PARSED-hostname allowlist (default-deny,
    SSRF/allowlist guidance) but accepts a bare repo URL like
    ``https://github.com/<owner>/<repo>`` so a batch of that repo's open PRs can
    be enumerated. Raises ``UnsupportedPlatform`` for a host outside the
    allowlist (including a malformed URL, which parses to no host) and
    ``AdapterParseError`` when the owner/repo path segments are missing."""
    if not link or not isinstance(link, str):
        raise UnsupportedPlatform("empty or non-string repo link")
    host, path = _urlparse_host_path(link)
    hosts = allowed_hosts(config)
    if host not in hosts:
        raise UnsupportedPlatform(
            f"unsupported repo host: {link!r} "
            f"(expected a repo URL on one of: {', '.join(sorted(hosts))})")
    if "/pull/" in path:
        # A PR URL, not a repo URL — route the user to the paste flow so we don't
        # silently review the PR's whole repo.
        raise AdapterParseError(
            f"that's a PR URL, not a repo URL: {link!r} (paste it in the PR box)")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        raise AdapterParseError(f"not a GitHub repo link: {link!r}")
    owner, repo = parts[0], re.sub(r"\.git$", "", parts[1])
    _seg = re.compile(r"^[A-Za-z0-9._-]+$")
    if owner in (".", "..") or repo in (".", "..") or not (_seg.match(owner) and _seg.match(repo)):
        raise AdapterParseError(f"invalid owner/repo in {link!r}")
    return canonical_host(host), owner, repo


def parse_repo_url(link: str) -> tuple[str, str]:
    """Parse ``(owner, repo)`` from a GitHub REPO URL (no ``/pull/``). Callers
    that need the host use ``parse_repo_ref``."""
    _host, owner, repo = parse_repo_ref(link)
    return owner, repo


def github_change_id(owner: str, repo: str, number: str | int,
                     host: str = "github.com") -> str:
    """Filesystem-safe, platform-namespaced change id: ``GH-<owner>-<repo>-<n>``.
    Unlike a raw URL, this is a valid filename.

    A non-github.com (GitHub Enterprise) host gets a leading sanitized host
    segment (``GH-<host>-<owner>-<repo>-<n>``) so the same owner/repo/number on
    two hosts cannot share one result file. github.com ids keep the host-less
    shape byte-identical so already-persisted records still resolve."""
    h = canonical_host(host)
    prefix = f"GH-{_sanitize_seg(h)}-" if h and h != "github.com" else "GH-"
    return f"{prefix}{_sanitize_seg(owner)}-{_sanitize_seg(repo)}-{number}"


def github_review_key(owner: str, repo: str, number: str | int,
                      host: str = "github.com") -> str:
    """Collision-free canonical identity for the durable reviewed-index key.

    Distinct from ``github_change_id``: that value ALSO names an on-disk result
    file, so it runs owner/repo through ``_sanitize_seg`` — which collapses ``-``
    to ``_`` to keep ``-`` unambiguous as its segment delimiter. That sanitization
    is lossy: ``acme/service-api`` and ``acme/service_api`` both become
    ``GH-acme-service_api-<n>``, so two DIFFERENT repos with the same PR number
    shared one ``reviewed.json`` key and clobbered each other's dedup record —
    silently skipping a requested review when their PR heads happened to share a
    commit SHA (as mirrored repos can).

    This key never names a file, so it keeps owner/repo verbatim and joins with
    ``/`` (a character GitHub owner/repo can never contain), giving a lossless,
    unambiguous identity. Owner/repo are lower-cased because GitHub treats them
    case-insensitively for identity. The key is HOST-qualified so the same
    owner/repo on two GitHub hosts cannot collide; the github.com default keeps
    already-persisted keys byte-identical."""
    h = canonical_host(host) or "github.com"
    return f"{h}/{str(owner).lower()}/{str(repo).lower()}#{number}"


# ---------------------------------------------------------------------------
# Bitbucket Cloud — link parsing + identity helpers
# ---------------------------------------------------------------------------

def bitbucket_pr_ref(link: str) -> tuple[str, str, str]:
    """Parse ``(workspace, repo, id)`` from a Bitbucket Cloud PR URL. Fails fast.

    Symmetric with ``github_pr_ref`` but for the single ``bitbucket.org`` host and
    the hyphenated+plural ``/pull-requests/<id>`` segment. Host is validated by
    EXACT match of the PARSED hostname (never a substring of the raw link), so a
    URL where ``bitbucket.org`` appears only in the path/userinfo, or a spoofed
    host (``notbitbucket.org``, ``bitbucket.org.evil.example``), is refused. A
    scheme-less link (``bitbucket.org/ws/r/pull-requests/1``) is tolerated by
    retrying with ``https://``; a malformed link is rejected like any other
    non-PR link (never a ``ValueError`` out of ``urlparse``). Bitbucket does not
    support an on-prem/self-hosted analogue here (that is Bitbucket Data Center,
    out of scope), so there is no configurable host allowlist — the host is the
    fixed constant."""
    if not link or not isinstance(link, str):
        raise AdapterParseError(f"not a Bitbucket PR link: {link!r}")
    text = link.strip()
    host, path = _urlparse_host_path(text)
    if not host and "://" not in text:
        host, path = _urlparse_host_path("https://" + text)
    if host != _BITBUCKET_HOST:
        raise AdapterParseError(f"not a Bitbucket PR link: {link!r}")
    m = _BB_PR_PATH_RE.match(path)
    if not m:
        raise AdapterParseError(f"not a Bitbucket PR link: {link!r}")
    workspace, repo, number = m.group(1), m.group(2), m.group(3)
    repo = re.sub(r"\.git$", "", repo)  # tolerate a trailing .git
    return workspace, repo, number


def bitbucket_change_id(workspace: str, repo: str, number: str | int) -> str:
    """Filesystem-safe, platform-namespaced change id: ``BB-<ws>-<repo>-<n>``.

    The Bitbucket analogue of ``github_change_id``: the value ALSO names an
    on-disk result file, so workspace/repo run through ``_sanitize_seg`` — which
    collapses ``-`` to ``_`` to keep ``-`` unambiguous as the segment delimiter,
    so different ws/repo pairs cannot collide on one result file. Bitbucket Cloud
    is single-host, so there is no host segment (unlike GitHub Enterprise)."""
    return f"BB-{_sanitize_seg(workspace)}-{_sanitize_seg(repo)}-{number}"


def bitbucket_review_key(workspace: str, repo: str, number: str | int) -> str:
    """Collision-free, LOSSLESS canonical identity for the durable reviewed-index.

    The Bitbucket analogue of ``github_review_key``: it never names a file, so it
    keeps workspace/repo verbatim and joins with ``/`` and ``#`` (characters a
    Bitbucket slug can never contain), giving an unambiguous identity where
    ``bitbucket_change_id``'s ``_``-collapsing sanitization would be lossy.
    Slugs are lower-cased because Bitbucket treats workspace/repo names
    case-insensitively for identity."""
    return f"{_BITBUCKET_HOST}/{str(workspace).lower()}/{str(repo).lower()}#{number}"


def extract_jira_key(text: str) -> str:
    """Extract a linked Jira issue key (``PROJECT-123``) from the PR summary.

    Bitbucket has no GitHub-style ``#N`` issue references; a change's linked work
    item is a Jira key. Returns the first match or ``""``. The pattern requires an
    uppercase-led project token so a plain ``ABC-1`` inside prose matches but a
    lowercase word-hyphen-number (``foo-2``) does not."""
    m = JIRA_KEY_RE.search(text or "")
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first(d: dict, *keys, default=""):
    for k in keys:
        v = d.get(k)
        if v not in (None, ""):
            return v
    return default


def _author_alias(raw: dict) -> str:
    a = _first(raw, "author", "authorAlias", "owner", default="")
    if isinstance(a, dict):
        return _first(a, "alias", "login", "name", default="")
    return str(a) if a else ""


def detect_is_fix(title: str, description: str) -> bool:
    return bool(FIX_RE.search(f"{title}\n{description}"))


def extract_linked_issue(text: str) -> str:
    """Extract a linked GitHub issue reference (``#123``) from the PR body."""
    m = GH_ISSUE_RE.search(text or "")
    return f"#{m.group(1)}" if m else ""


# ---------------------------------------------------------------------------
# GitHub adapter
# ---------------------------------------------------------------------------

def parse_github_payload(raw: dict | str, *, link: str | None = None) -> ReviewTarget:
    """Normalize a GitHub PR payload into a ReviewTarget. The worker assembles
    this payload from ``gh api``: the ``pulls/{n}`` object merged with a ``files``
    array (each carrying its per-file ``patch``) and optional ``comments``. Tolerant
    of field-name variants (``filename``/``path``, ``patch``/``diff``); fails fast
    when there is no usable content. ``owner``/``repo``/``number`` are taken from
    the payload (``base.repo.full_name`` + ``number``) and fall back to the link/
    ``html_url`` so the adapter works whether or not the caller echoes the URL."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdapterParseError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdapterParseError("payload must be a JSON object")

    _base = raw.get("base")
    base: dict = _base if isinstance(_base, dict) else {}
    _head = raw.get("head")
    head: dict = _head if isinstance(_head, dict) else {}
    _base_repo = base.get("repo")
    base_repo: dict = _base_repo if isinstance(_base_repo, dict) else {}

    # NOTE: do NOT fall back to raw["id"] — on GitHub that is the internal
    # database id (e.g. 1847293847), NOT the PR number in the URL. Using it would
    # produce a change_id that mismatches _cid()'s URL-derived id (write/read would
    # hit different result files). The link/html_url fallback below supplies the
    # number when the payload omits it.
    number = _first(raw, "number", default="")
    owner = repo = host = ""
    full = _first(base_repo, "full_name", default="")
    if full and "/" in full:
        owner, repo = full.split("/", 1)

    # Fill any missing part (including the host) from the link, then from
    # html_url. Without a parseable URL the host defaults to github.com.
    html_url = _first(raw, "html_url", "url", default="")
    for candidate in (link, html_url):
        if owner and repo and number and host:
            break
        if not candidate:
            continue
        try:
            lh, lo, lr, ln = github_pr_ref(candidate)
        except AdapterParseError:
            continue
        host = host or lh
        owner = owner or lo
        repo = repo or lr
        number = number or ln
    host = host or "github.com"

    if not (owner and repo and number):
        raise AdapterParseError(
            "could not determine GitHub owner/repo/number from payload or link")

    description = _first(raw, "body", "description", default="")
    title = _first(raw, "title", default="") or (description.splitlines()[0] if description else "")

    raw_files = raw.get("files") or raw.get("diffs") or []
    files: list[dict] = []
    for d in raw_files:
        if not isinstance(d, dict):
            continue
        path = _first(d, "filename", "path", "name", default="")
        diff = _first(d, "patch", "diff", "unifiedDiff", default="")
        if path:
            files.append({"path": path, "diff": diff})

    # Fail fast: a PR with neither files nor a description is unusable.
    if not files and not description:
        raise AdapterParseError("payload has no files and no description")

    # GitHub author lives under user.login (fall back to the generic extractor).
    author = ""
    user = raw.get("user")
    if isinstance(user, dict):
        author = _first(user, "login", "name", default="")
    if not author:
        author = _author_alias(raw)

    revision = (_first(head, "sha", default="")
                or _first(raw, "head_sha", "sha", "revision", default=""))
    target_branch = (_first(base, "ref", default="")
                     or _first(raw, "base_ref", "targetBranch", default=""))

    comments = raw.get("comments") or raw.get("review_comments") or raw.get("allComments") or []
    if not isinstance(comments, list):
        comments = []

    return ReviewTarget(
        platform="github",
        repo_identity=f"{host}/{owner}/{repo}",
        change_id=github_change_id(owner, repo, number, host=host),
        url=html_url or f"https://{host}/{owner}/{repo}/pull/{number}",
        title=title,
        description=description,
        linked_issue=extract_linked_issue(description),
        author=str(author) if author else "",
        target_branch=target_branch,
        revision=str(revision),
        files=files,
        existing_comments=comments,
        design_discussion=[],
        is_fix=detect_is_fix(title, description),
    )


# ---------------------------------------------------------------------------
# Bitbucket Cloud adapter
# ---------------------------------------------------------------------------

def split_unified_diff(diff_text: str) -> list[dict]:
    """Split one PR-wide unified diff into ``[{path, diff}]`` on ``diff --git``
    headers.

    Bitbucket's diff endpoint returns ONE unified diff for the whole pull request,
    unlike GitHub's per-file ``patch`` array — so the deterministic split happens
    here rather than in the payload shape. Each file hunk begins with a
    ``diff --git a/<path> b/<path>`` header; the path is read from the ``b/``
    (post-image) side, falling back to the ``a/`` side for a pure deletion, and
    then stripped of the ``a/``/``b/`` prefix. Anything before the first header
    (rare preamble) is ignored. A quoted path (``diff --git "a/x y" "b/x y"``) is
    handled by taking the last whitespace-run's token off the header line and
    unquoting it. Returns ``[]`` for empty/whitespace input."""
    if not diff_text or not diff_text.strip():
        return []
    files: list[dict] = []
    # Split on the header while KEEPING it: prepend a sentinel newline so a
    # leading header still splits, then walk hunk by hunk.
    parts = re.split(r"(?m)^(?=diff --git )", diff_text)
    for chunk in parts:
        if not chunk.strip() or not chunk.startswith("diff --git "):
            continue
        header = chunk.splitlines()[0]
        path = _path_from_git_header(header)
        files.append({"path": path, "diff": chunk})
    return files


def _unquote_git_path(tok: str) -> str:
    """Strip a leading ``a/``/``b/`` prefix and surrounding quotes from a
    ``diff --git`` path token. Git quotes a path containing spaces or special
    chars with C-style double quotes; we only need the readable path, so a quoted
    token is unwrapped literally (no escape decoding — the value is a display/anchor
    key, matched later against the same-shaped fetch, not opened as a filename)."""
    t = tok.strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        t = t[1:-1]
    return re.sub(r"^[ab]/", "", t)


def _path_from_git_header(header: str) -> str:
    """Read the file path from a ``diff --git a/<path> b/<path>`` header line.

    Prefers the ``b/`` (post-image) path; falls back to the ``a/`` path (a pure
    deletion has no meaningful ``b/``). Tolerates the quoted form. An unparseable
    header yields ``""`` rather than raising — a malformed hunk still records as a
    file with an empty path rather than crashing the whole parse."""
    body = header[len("diff --git "):].strip()
    if not body:
        return ""
    # Quoted paths: "a/x y z" "b/x y z" — split on the boundary between the two
    # quoted tokens. Unquoted: split on the last ' b/' occurrence so a path with
    # spaces on the a-side still divides cleanly at the b-side prefix.
    if body.startswith('"'):
        m = re.match(r'^("(?:[^"\\]|\\.)*")\s+("(?:[^"\\]|\\.)*")$', body)
        if m:
            b = _unquote_git_path(m.group(2))
            return b or _unquote_git_path(m.group(1))
        return _unquote_git_path(body.split(" ", 1)[0])
    idx = body.rfind(" b/")
    if idx != -1:
        b = _unquote_git_path(body[idx + 1:])
        a = _unquote_git_path(body[:idx])
        return b or a
    # No ' b/' boundary — take the first token, best effort.
    return _unquote_git_path(body.split(" ", 1)[0])


def parse_bitbucket_payload(raw: dict | str, *, link: str | None = None) -> ReviewTarget:
    """Normalize a Bitbucket Cloud PR payload into the SAME 14-field ReviewTarget.

    The worker assembles ``raw`` from the Rovo Bitbucket tools (get-PR merged with
    the get-diff text and optional get-comments) — a mirror of the GitHub adapter,
    where fetch is an LLM instruction and this parse is deterministic. Field map:
      - ``repo_identity`` = ``bitbucket.org/<ws>/<repo>``
      - ``change_id``     = ``BB-<ws>-<repo>-<id>`` (``bitbucket_change_id``)
      - ``author``        = ``author.nickname`` (Bitbucket's stable display handle)
      - ``target_branch`` = ``destination.branch.name``
      - ``revision``      = ``source.commit.hash`` (the head anchor)
      - ``description``   = ``summary.raw`` / ``description``
      - ``linked_issue``  = first Jira key in the summary, else ``""``
      - ``files``         = ``split_unified_diff`` of the PR-wide ``diff`` text

    ``workspace``/``repo``/``id`` come from the payload (``source``/``destination``
    repo full_name + ``id``) and fall back to the link so the adapter works
    whether or not the caller echoes the URL. Fails fast: bad JSON, a non-object,
    no resolvable ws/repo/id, or a payload with neither files nor a description."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AdapterParseError(f"payload is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise AdapterParseError("payload must be a JSON object")

    def _repo_full_name(side: dict) -> str:
        r = side.get("repository") if isinstance(side.get("repository"), dict) else {}
        return _first(r, "full_name", default="")

    _dest = raw.get("destination") if isinstance(raw.get("destination"), dict) else {}
    _src = raw.get("source") if isinstance(raw.get("source"), dict) else {}

    number = str(_first(raw, "id", "number", default="") or "")
    workspace = repo = ""
    # Prefer the destination repo's full_name (`<workspace>/<repo>`); fall back to
    # the source side, then to the link.
    for full in (_repo_full_name(_dest), _repo_full_name(_src)):
        if full and "/" in full:
            workspace, repo = full.split("/", 1)
            break

    html_url = ""
    links = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    if isinstance(links.get("html"), dict):
        html_url = _first(links["html"], "href", default="")

    for candidate in (link, html_url):
        if workspace and repo and number:
            break
        if not candidate:
            continue
        try:
            lw, lr, ln = bitbucket_pr_ref(candidate)
        except AdapterParseError:
            continue
        workspace = workspace or lw
        repo = repo or lr
        number = number or ln

    if not (workspace and repo and number):
        raise AdapterParseError(
            "could not determine Bitbucket workspace/repo/id from payload or link")

    # summary.raw is Bitbucket's canonical PR description; tolerate a bare string
    # or a {raw} object, and a top-level `description`.
    summary = raw.get("summary")
    if isinstance(summary, dict):
        description = _first(summary, "raw", "html", default="")
    else:
        description = str(summary or "")
    if not description:
        description = _first(raw, "description", default="")
    title = _first(raw, "title", default="") or (description.splitlines()[0] if description else "")

    # Author: Bitbucket's stable handle is `author.nickname` (fall back to
    # display_name, then the generic extractor).
    author = ""
    author_obj = raw.get("author")
    if isinstance(author_obj, dict):
        author = _first(author_obj, "nickname", "display_name", "username", default="")
    if not author:
        author = _author_alias(raw)

    revision = ""
    src_commit = _src.get("commit") if isinstance(_src.get("commit"), dict) else {}
    revision = _first(src_commit, "hash", default="") or _first(raw, "revision", default="")

    target_branch = ""
    dest_branch = _dest.get("branch") if isinstance(_dest.get("branch"), dict) else {}
    target_branch = _first(dest_branch, "name", default="") or _first(raw, "target_branch", default="")

    # Bitbucket returns ONE PR-wide unified diff; split it into per-file entries.
    diff_text = _first(raw, "diff", "diff_text", default="")
    files = split_unified_diff(diff_text) if isinstance(diff_text, str) else []

    # Fail fast: a PR with neither files nor a description is unusable.
    if not files and not description:
        raise AdapterParseError("payload has no files and no description")

    comments = raw.get("comments") or raw.get("existing_comments") or []
    if not isinstance(comments, list):
        comments = []

    return ReviewTarget(
        platform="bitbucket",
        repo_identity=f"{_BITBUCKET_HOST}/{workspace}/{repo}",
        change_id=bitbucket_change_id(workspace, repo, number),
        url=html_url or f"https://{_BITBUCKET_HOST}/{workspace}/{repo}/pull-requests/{number}",
        title=title,
        description=description,
        linked_issue=extract_jira_key(f"{title}\n{description}"),
        author=str(author) if author else "",
        target_branch=target_branch,
        revision=str(revision),
        files=files,
        existing_comments=comments,
        design_discussion=[],
        is_fix=detect_is_fix(title, description),
    )


def normalize(link: str, raw_payload: dict | str) -> ReviewTarget:
    """Top-level entry: detect platform, then parse. Fails fast on unsupported."""
    platform = detect_platform(link)
    if platform == "github":
        return parse_github_payload(raw_payload, link=link)
    if platform == "bitbucket":
        return parse_bitbucket_payload(raw_payload, link=link)
    raise UnsupportedPlatform(f"unsupported platform: {platform!r}")


def validate_review_target(target: ReviewTarget) -> list[str]:
    """Non-fatal warnings about a normalized target — surfaces likely GitHub
    payload-mapping gaps before review. Empty == looks complete."""
    warns: list[str] = []
    if not target.files:
        warns.append("no files/diffs parsed — check the payload's `files` mapping")
    else:
        if not any(f.get("diff") for f in target.files):
            warns.append("files present but all diffs are empty — check the `patch` field name")
    if not target.title and not target.description:
        warns.append("no title or description — check the payload field names")
    if not target.target_branch:
        warns.append("no target branch parsed (branch-gate checks will be skipped)")
    if target.repo_identity.endswith("/unknown"):
        warns.append("repo could not be determined — learning key will be 'unknown'")
    return warns
