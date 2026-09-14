#!/usr/bin/env python3
"""Repo discovery — so the user PICKS a pull request instead of pasting its URL.

Two jobs:

1. **Which open PRs does a configured Bitbucket repo have?** Answered by the
   Rovo ``listBitbucketRepoPullRequests`` op (:func:`list_open_prs`), scoped to a
   repo that is on the fail-closed allowlist (``store.allowed_targets``). Rovo is
   reachable only from the LLM worker, so the transport is INJECTED as an
   ``execute_read`` callable rather than called in-process here — the same
   worker-owns-Rovo boundary the fetch/posting specs use. A repo that is not on
   the allowlist is refused before any transport call.

   The GitHub personal-feed pickers this module used to carry
   (``list_user_repos`` / ``list_contributed_repos``, driven off the ``gh`` event
   feed) are GONE: Bitbucket Cloud exposes no events feed and Rovo has no
   equivalent op, so there is nothing to port. Target selection is now "paste a PR
   link, or pick from a configured repo's open PRs".

2. **Which repos has the user pinned here?** A tiny app-local list
   (``data/repos.json``) so the picker opens on the repos they care about instead
   of re-deriving on every visit.
"""
from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from sage_lib import store
from sage_lib.store import redact_text as pipeline_redact

# Guarded top-level import, matching pipeline.py: this module is also imported on
# the standalone path, where `kiro_crew` is not importable. A bare module-level
# import would turn that into an ImportError at import time.
try:
    from kiro_crew import github_runner
except ImportError:  # pragma: no cover - standalone fallback
    github_runner = None  # type: ignore

GH_TIMEOUT_SEC = 45.0

# Serializes the read-modify-write of repos.json so two concurrent adds can't
# clobber each other (the write itself is atomic; this guards read+merge).
_REPOS_LOCK = threading.Lock()


class GhError(RuntimeError):
    """A ``gh`` invocation failed (transient: not authed, network, 404)."""


class GhSetupError(GhError):
    """``gh`` is missing or unusable on this host — a setup problem the user must
    fix, distinct from a transient API failure, so the UI can offer instructions
    instead of an error toast."""


_GH_OVERRIDE_ENV = "KIROCREW_SAGE_GH"


def gh_env() -> dict[str, str]:
    """A minimal environment for ``gh``: the platform's safe-key base plus gh's
    own auth + network/TLS vars when set — never the gateway's full environment.
    Owned by the shared hardened runner so every gh surface stays in lockstep."""
    if github_runner is None:  # pragma: no cover - standalone fallback
        raise RuntimeError("gh_env requires the Kiro Crew runtime")
    return github_runner.gh_env()


def gh_bin() -> str:
    """Absolute path to an acceptable ``gh``, resolved and cached by the shared
    hardened runner (``github_runner.resolve_gh``).

    Set ``KIROCREW_SAGE_GH`` to an absolute path to override (still validated).
    Raises :class:`GhSetupError` when no acceptable executable is found."""
    if github_runner is None:  # pragma: no cover - standalone fallback
        raise RuntimeError("gh_bin requires the Kiro Crew runtime")
    try:
        return github_runner.resolve_gh(override_env=_GH_OVERRIDE_ENV)
    except github_runner.SetupError as exc:
        raise GhSetupError(str(exc)) from exc


def _run_gh(argv: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    """Route a resolved-gh argv through the shared spawn chokepoint
    (``github_runner.run_gh``): minimal env plus an SEL audit event on success,
    failure, and timeout. Transparent to this module's error mapping —
    ``FileNotFoundError`` and ``subprocess.TimeoutExpired`` propagate."""
    if github_runner is None:  # pragma: no cover - standalone fallback
        raise RuntimeError("gh execution requires the Kiro Crew runtime")
    try:
        return github_runner.run_gh(argv, timeout=timeout, audit_caller="core:code-review-sage")
    except github_runner.SetupError as exc:
        # Audit-or-deny refusal (SEL unavailable) — transient, retryable.
        raise GhError(str(exc)) from exc


def run_gh_json(path: str, jq: str | None = None, *,
                timeout: float = GH_TIMEOUT_SEC,
                paginate: bool = False, host: str | None = None) -> list[dict]:
    """Run ``gh api <path>`` and parse the result into a list of dicts.

    ``path`` is an API path, never a shell string, and the argv is a LIST (no
    ``shell=True``). A caller-supplied ``host`` is ALWAYS pinned explicitly via
    ``--hostname`` — including github.com — so the call can never drift to the
    ``gh`` CLI's configured default host (``GH_HOST`` / ``gh auth``): a public
    PR must not be read from an enterprise instance and vice versa. Callers
    derive the host from a URL that already passed the adapters'
    parsed-hostname allowlist; with no ``host`` the call targets whatever
    instance the user's ``gh`` is set up for (dashboard browsing calls). With
    ``jq`` the output is JSONL (one object per line); each unparseable line is
    skipped, but output that is entirely unparseable raises rather than
    masquerading as an empty result."""
    argv = [gh_bin(), "api", path]
    h = (host or "").strip().lower()
    if h == "www.github.com":
        h = "github.com"
    if h:
        argv += ["--hostname", h]
    if paginate:
        argv.append("--paginate")
    if jq:
        argv += ["--jq", jq]
    try:
        proc = _run_gh(argv, timeout=timeout)
    except FileNotFoundError as exc:
        raise GhSetupError("the `gh` CLI is not installed on this host") from exc
    except subprocess.TimeoutExpired as exc:
        raise GhError(f"`gh api {path}` timed out") from exc
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
        if "auth login" in tail or "not logged" in tail.lower():
            raise GhSetupError(f"`gh` is not authenticated: {tail}")
        raise GhError(f"`gh api {path}` failed (exit {proc.returncode}): {tail}")
    text = (proc.stdout or "").strip()
    if not text:
        return []
    if not jq:
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GhError(f"could not parse `gh api {path}` output") from exc
        if isinstance(loaded, list):
            return [r for r in loaded if isinstance(r, dict)]
        return [loaded] if isinstance(loaded, dict) else []
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    if not out:
        raise GhError(
            f"could not parse `gh api {path}` output "
            "(expected one JSON object per line)")
    return out


def current_login(*, timeout: float = GH_TIMEOUT_SEC) -> str | None:
    """The authenticated ``gh`` login, or None when it can't be determined.

    Runs ``gh api user --jq .login`` directly rather than through
    ``run_gh_json``: ``--jq .login`` emits a BARE STRING, which the JSONL dict
    parser cannot represent. Raises :class:`GhSetupError` when ``gh`` itself is
    unusable, because "no login" and "no gh" need different UI treatment."""
    argv = [gh_bin(), "api", "user", "--jq", ".login"]
    try:
        proc = _run_gh(argv, timeout=timeout)
    except FileNotFoundError as exc:
        raise GhSetupError("the `gh` CLI is not installed on this host") from exc
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or "").strip().splitlines()[-3:])
        if "auth login" in tail or "not logged" in tail.lower():
            raise GhSetupError(f"`gh` is not authenticated: {tail}")
        return None
    login = (proc.stdout or "").strip()
    return login or None


class TargetNotAllowed(RuntimeError):
    """A ``(workspace, repo)`` that is not on the fail-closed allowlist.

    Raised by :func:`list_open_prs` BEFORE any transport call, so a repo the
    operator never opted in via ``bitbucket_repos`` can never be enumerated —
    the discovery analogue of the revalidation every other Bitbucket action does
    against ``store.allowed_targets``."""


def _norm_pr_row(raw: dict) -> dict | None:
    """Normalize ONE Rovo pull-request object into the picker's row shape.

    The picker (and the reviewed-index annotation in the route layer) consume the
    same field set the GitHub path emits: ``{url, number, head_sha, title,
    author, updated_at, draft, labels}``. Bitbucket's payload names these
    differently and nests some, so this projects them out defensively — a row
    with no usable ``number`` is dropped (it cannot be turned into a review
    target), and every other field degrades to empty/false rather than raising,
    so one malformed entry never sinks the whole list. Field names follow
    Bitbucket Cloud's PR object: ``id`` (the PR number), ``links.html.href`` (the
    web URL), ``source.commit.hash`` (the head SHA), ``author.display_name``,
    ``updated_on``, and ``draft``."""
    if not isinstance(raw, dict):
        return None
    number = raw.get("id")
    if number is None:
        number = raw.get("number")
    if not isinstance(number, int):
        try:
            number = int(number)  # tolerate a numeric string
        except (TypeError, ValueError):
            return None

    # web URL: prefer the nested links.html.href, fall back to a flat "url".
    url = ""
    links = raw.get("links")
    if isinstance(links, dict):
        html = links.get("html")
        if isinstance(html, dict):
            url = str(html.get("href") or "")
    if not url:
        url = str(raw.get("url") or "")

    # head SHA: Bitbucket carries it as source.commit.hash.
    head_sha = ""
    source = raw.get("source")
    if isinstance(source, dict):
        commit = source.get("commit")
        if isinstance(commit, dict):
            head_sha = str(commit.get("hash") or "")
    if not head_sha:
        head_sha = str(raw.get("head_sha") or "")

    # author: Bitbucket nests display_name under author.
    author = ""
    a = raw.get("author")
    if isinstance(a, dict):
        author = str(a.get("display_name") or a.get("nickname") or "")
    elif isinstance(a, str):
        author = a

    # A Bitbucket PR in draft carries a truthy `draft` flag; older payloads omit
    # it entirely, so absence reads as not-a-draft.
    draft = bool(raw.get("draft"))

    return {
        "url": url,
        "number": number,
        "head_sha": head_sha,
        "title": str(raw.get("title") or ""),
        "author": author,
        "updated_at": str(raw.get("updated_on") or raw.get("updated_at") or ""),
        "draft": draft,
        # No label concept is carried through discovery for Bitbucket; keep the
        # key present and empty so the picker narrows on it without a presence
        # check, exactly as the GitHub path guarantees.
        "labels": [],
    }


def list_open_prs(workspace: str, repo: str, *, execute_read,
                  config: dict | None = None) -> list[dict]:
    """Enumerate a configured Bitbucket repo's OPEN pull requests via Rovo.

    Rebuilds the GitHub picker's "list a repo's open PRs" on the Rovo
    ``listBitbucketRepoPullRequests`` op. Two things make this the Bitbucket
    analogue of ``pipeline.list_open_prs`` rather than a copy of it:

    * **Fail-closed scope.** ``(workspace, repo)`` MUST be on
      ``store.allowed_targets(config)`` — the same set every other Bitbucket
      action revalidates against. A repo that is not configured raises
      :class:`TargetNotAllowed` BEFORE any transport call, so discovery can never
      reach a repo the operator did not opt in. Matching is by exact,
      case-insensitive ``(workspace, repo)`` equality (Bitbucket slug semantics),
      never a substring test.
    * **Injected transport.** Rovo MCP is reachable only from the LLM worker, so
      there is no in-process ``gh``-style subprocess seam here. The caller passes
      ``execute_read`` — a callable ``(op_name: str, params: dict) -> list|dict``
      that performs the Rovo ``executeRead`` — and this function owns only the
      allowlist gate and the response normalization. That keeps the transport
      boundary a single injected dependency (faked at that boundary in tests)
      instead of baking an MCP assumption into a pure module.

    Returns ``[{url, number, head_sha, title, author, updated_at, draft,
    labels}]`` — the same row shape the GitHub path emits — so the picker and the
    reviewed-index annotation consume both paths identically. Malformed entries
    are dropped rather than raising; a genuinely empty repo returns ``[]``.
    """
    ws = store._clean_slug(workspace)
    rp = store._clean_slug(repo)
    if (ws, rp) not in store.allowed_targets(config):
        raise TargetNotAllowed(
            f"{workspace}/{repo} is not a configured Bitbucket target "
            "(add it via /bitbucket-repos)")

    # The one Rovo call. `workspaceId`/`repoId` are the argument names the op
    # takes (verified via `discover`); pass the ORIGINAL spellings — the op is
    # case-insensitive on slugs and the allowlist check above already authorized
    # this pair.
    resp = execute_read(
        "listBitbucketRepoPullRequests",
        {"workspaceId": workspace, "repoId": repo},
    )

    # Rovo may hand back either a bare list of PR objects or a paginated envelope
    # ({"values": [...]}); accept both, and anything else reads as "no open PRs".
    if isinstance(resp, dict):
        raw_rows = resp.get("values") or resp.get("pullRequests") or []
    elif isinstance(resp, (list, tuple)):
        raw_rows = list(resp)
    else:
        raw_rows = []

    out: list[dict] = []
    for raw in raw_rows:
        row = _norm_pr_row(raw)
        if row is not None:
            out.append(row)
    return out


# --- Pinned repos ------------------------------------------------------------

def repos_path(root: Path | None = None) -> Path:
    return store.data_dir(root) / "repos.json"


def read_repos(root: Path | None = None) -> list[dict]:
    """The user's pinned repos, newest-added first. ``[]`` when unset/unreadable.

    Read through the no-link guard and scrubbed on the way out, because this file
    sits in a directory a review worker can reach and `GET /repos` renders the
    result in the sidebar. Two distinct exposures, so two defences: a prompt-injected
    worker can plant a symlink at the path (the guard refuses to dereference it), and
    it can write a credential into a field of a legitimate file (redaction removes
    it). Values are read for display only, so scrubbing them changes nothing a caller
    depends on -- `owner`/`repo` are re-validated by the write path, and a real
    owner/repo never matches a credential shape.
    """
    within = store.data_dir(root)
    data = store.read_json_nolink(repos_path(root), within)
    if data is None:
        return []
    repos = data.get("repos")
    if not isinstance(repos, list):
        return []
    # `owner`/`repo` must be non-empty STRINGS, not merely truthy: a worker owns
    # this file, and a planted dict or list is truthy, survives redaction (which
    # walks strings), and reaches the client as an object React cannot render --
    # taking the page down rather than showing one bad row.
    return [_redact_repo(r) for r in repos
            if isinstance(r, dict)
            and isinstance(r.get("owner"), str) and r["owner"]
            and isinstance(r.get("repo"), str) and r["repo"]]


def _redact_repo(row: dict) -> dict:
    """Scrub every string in a pinned-repo row -- keys as well as values.

    A worker writing this file controls key names too, so a credential can ride in
    either half; the report path learned the same lesson one module over. Non-string
    scalars pass through, and nested containers are walked so a value that is a dict
    or list cannot smuggle a string past an `isinstance(v, str)` test.
    """
    return {_redact_any(k): _redact_any(v) for k, v in row.items()}


def _redact_any(value: object) -> object:
    if isinstance(value, str):
        return pipeline_redact(value) if value else value
    if isinstance(value, dict):
        return {_redact_any(k): _redact_any(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_any(v) for v in value]
    return value


def _write_repos(repos: list[dict], root: Path | None = None) -> Path:
    store.ensure_layout(root)
    path = repos_path(root)
    payload = json.dumps({"repos": repos}, indent=2).encode("utf-8")
    store.atomic_write_locked(path, payload)
    return path


def _same(a: dict, b_owner: str, b_repo: str) -> bool:
    """Repo identity is case-insensitive on GitHub, so compare that way."""
    return (str(a.get("owner", "")).lower() == b_owner.lower()
            and str(a.get("repo", "")).lower() == b_repo.lower())


def add_repo(owner: str, repo: str, root: Path | None = None) -> list[dict]:
    """Pin a repo (idempotent, most-recent first). Returns the new list."""
    with _REPOS_LOCK:
        repos = [r for r in read_repos(root) if not _same(r, owner, repo)]
        repos.insert(0, {
            "owner": owner, "repo": repo, "full_name": f"{owner}/{repo}",
            "added_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        _write_repos(repos, root)
        return repos


def remove_repo(owner: str, repo: str, root: Path | None = None) -> list[dict]:
    """Unpin a repo. Returns the new list."""
    with _REPOS_LOCK:
        repos = [r for r in read_repos(root) if not _same(r, owner, repo)]
        _write_repos(repos, root)
        return repos
