"""Tests for ``sage_lib/discovery.py`` — the repo picker's gh-backed JSON reader,
the Bitbucket open-PR list op, and the app-local pinned-repo list.

Three surfaces:
  * ``run_gh_json`` — parse ``gh api`` output. These patch ``subprocess.run`` and
    ``gh_bin`` so no real ``gh`` is required, and lock in the argv-is-a-LIST /
    no-``shell=True`` contract and the JSONL parsing rules (skip blanks, raise on
    wholly-unparseable / non-zero exit, map an auth-failure stderr to
    ``GhSetupError``). ``run_gh_json`` stays because the GitHub PR-list path and
    the poster read-back still use it; the personal-feed pickers built on it
    (``list_user_repos`` / ``list_contributed_repos``) were removed in the
    Bitbucket port.
  * ``list_open_prs`` — the Bitbucket picker's "list a configured repo's open
    PRs" op. Rovo is faked at the transport boundary (an ``execute_read``
    callable), and the tests cover the fail-closed allowlist gate, the Rovo op +
    args, the row-shape normalization, and the paginated-envelope shape.
  * ``read_repos`` / ``add_repo`` / ``remove_repo`` — the pinned list, exercised
    against a tmp ``KIROCREW_HOME`` for a real round-trip (idempotent +
    case-insensitive, newest-first, tolerant of a missing/corrupt file).
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from sage_lib import discovery, store  # noqa: E402  (app root added to sys.path above)

#: A fake resolved ``gh`` that is ABSOLUTE on this host. ``github_runner.run_gh``
#: refuses any argv[0] that fails ``os.path.isabs``, and from Python 3.13
#: ``ntpath.isabs("/usr/bin/gh")`` is False (no drive), so the POSIX literal made
#: every stubbed call here fail on Windows before the stubbed subprocess ran.
_FAKE_GH = os.path.abspath(os.path.join(os.sep, "usr", "bin", "gh"))


def _proc(returncode=0, stdout="", stderr=""):
    """Stand in for what ``subprocess.run`` hands ``github_runner.run_gh``.

    A real ``CompletedProcess`` carrying BYTES, because ``run_gh`` decodes the
    streams itself (strictly, as UTF-8) rather than letting subprocess guess with
    the locale codec. A ``SimpleNamespace`` with ``str`` streams fails twice over:
    no ``args`` attribute, and no ``decode``.
    """
    return subprocess.CompletedProcess(
        args=["gh"],
        returncode=returncode,
        stdout=stdout.encode("utf-8") if isinstance(stdout, str) else stdout,
        stderr=stderr.encode("utf-8") if isinstance(stderr, str) else stderr,
    )


class TestRunGhJson(unittest.TestCase):
    def test_parses_jsonl_and_skips_blank_lines(self):
        out = ('{"type": "PushEvent", "repo": "a/b"}\n'
               '\n'
               '   \n'
               '{"type": "PullRequestEvent", "repo": "c/d"}\n')
        with unittest.mock.patch.object(discovery, "gh_bin", return_value=_FAKE_GH), \
             unittest.mock.patch.object(discovery.subprocess, "run",
                                        return_value=_proc(stdout=out)):
            rows = discovery.run_gh_json("users/x/events", jq=".[]")
        self.assertEqual([r["repo"] for r in rows], ["a/b", "c/d"])

    def test_uses_list_argv_without_shell(self):
        captured = {}

        def _fake_run(argv, *args, **kwargs):
            captured["argv"] = argv
            captured["kwargs"] = kwargs
            return _proc(stdout='{"repo": "a/b"}\n')

        with unittest.mock.patch.object(discovery, "gh_bin", return_value=_FAKE_GH), \
             unittest.mock.patch.object(discovery.subprocess, "run", side_effect=_fake_run):
            discovery.run_gh_json("users/x/events", jq=".[]")
        self.assertIsInstance(captured["argv"], list)
        self.assertEqual(captured["argv"][:3], [_FAKE_GH, "api", "users/x/events"])
        self.assertIn("--jq", captured["argv"])
        # never a shell string
        self.assertNotIn("shell", captured["kwargs"])
        self.assertNotEqual(captured["kwargs"].get("shell"), True)

    def test_raises_on_wholly_unparseable_output(self):
        with unittest.mock.patch.object(discovery, "gh_bin", return_value=_FAKE_GH), \
             unittest.mock.patch.object(discovery.subprocess, "run",
                                        return_value=_proc(stdout="not json\nstill not json")):
            with self.assertRaises(discovery.GhError):
                discovery.run_gh_json("users/x/events", jq=".[]")

    def test_raises_on_non_zero_exit(self):
        with unittest.mock.patch.object(discovery, "gh_bin", return_value=_FAKE_GH), \
             unittest.mock.patch.object(discovery.subprocess, "run",
                                        return_value=_proc(returncode=1, stderr="boom")):
            with self.assertRaises(discovery.GhError):
                discovery.run_gh_json("users/x/events", jq=".[]")

    def test_auth_failure_maps_to_setup_error(self):
        with unittest.mock.patch.object(discovery, "gh_bin", return_value=_FAKE_GH), \
             unittest.mock.patch.object(
                discovery.subprocess, "run",
                return_value=_proc(returncode=1,
                                   stderr="gh: To get started with GitHub CLI, "
                                          "please run: gh auth login")):
            with self.assertRaises(discovery.GhSetupError):
                discovery.run_gh_json("users/x/events", jq=".[]")


class TestListOpenPrs(unittest.TestCase):
    """The Bitbucket picker's ``list_open_prs``: fail-closed allowlist gate, the
    Rovo op + args, and row-shape normalization. Rovo is faked at the transport
    boundary via an injected ``execute_read`` callable — no MCP is touched."""

    _CFG = {"bitbucket_repos": [{"workspace": "dflds", "repo": "content.hub"}]}

    def _bb_pr(self, number=5, **over):
        """A Bitbucket Cloud PR object in the op's native shape."""
        row = {
            "id": number,
            "title": f"PR {number}",
            "draft": False,
            "updated_on": "2026-07-20T00:00:00Z",
            "author": {"display_name": "Ada Lovelace"},
            "source": {"commit": {"hash": "abc123"}},
            "links": {"html": {"href":
                      f"https://bitbucket.org/dflds/content.hub/pull-requests/{number}"}},
        }
        row.update(over)
        return row

    def test_returns_prs_via_the_rovo_op(self):
        calls = []

        def fake_read(op, params):
            calls.append((op, params))
            return [self._bb_pr(5), self._bb_pr(6, title="second")]

        prs = discovery.list_open_prs(
            "dflds", "content.hub", execute_read=fake_read, config=self._CFG)
        # The op and its args are what the ticket names.
        self.assertEqual(calls, [(
            "listBitbucketRepoPullRequests",
            {"workspaceId": "dflds", "repoId": "content.hub"},
        )])
        # Normalized into the picker's row shape (same keys the GitHub path emits).
        self.assertEqual(prs[0], {
            "url": "https://bitbucket.org/dflds/content.hub/pull-requests/5",
            "number": 5, "head_sha": "abc123", "title": "PR 5",
            "author": "Ada Lovelace", "updated_at": "2026-07-20T00:00:00Z",
            "draft": False, "labels": [],
        })
        self.assertEqual([p["number"] for p in prs], [5, 6])

    def test_refuses_a_repo_outside_the_allowlist(self):
        def fake_read(op, params):  # pragma: no cover - must never be reached
            raise AssertionError("transport called for a non-allowlisted repo")

        with self.assertRaises(discovery.TargetNotAllowed):
            discovery.list_open_prs(
                "evil", "repo", execute_read=fake_read, config=self._CFG)

    def test_allowlist_match_is_case_insensitive(self):
        seen = []

        def fake_read(op, params):
            seen.append(params)
            return []

        # DFLDS/Content.Hub must match the configured dflds/content.hub.
        discovery.list_open_prs(
            "DFLDS", "Content.Hub", execute_read=fake_read, config=self._CFG)
        # The ORIGINAL spelling is passed to the op (the op is slug-insensitive).
        self.assertEqual(seen, [{"workspaceId": "DFLDS", "repoId": "Content.Hub"}])

    def test_unconfigured_is_fail_closed(self):
        # No bitbucket_repos configured -> zero allowed targets -> refuse.
        def fake_read(op, params):  # pragma: no cover
            raise AssertionError("transport called with an empty allowlist")

        with self.assertRaises(discovery.TargetNotAllowed):
            discovery.list_open_prs(
                "dflds", "content.hub", execute_read=fake_read, config={})

    def test_accepts_a_paginated_values_envelope(self):
        def fake_read(op, params):
            return {"values": [self._bb_pr(9)], "size": 1}

        prs = discovery.list_open_prs(
            "dflds", "content.hub", execute_read=fake_read, config=self._CFG)
        self.assertEqual([p["number"] for p in prs], [9])

    def test_a_row_without_a_number_is_dropped(self):
        def fake_read(op, params):
            return [{"title": "no id here"}, self._bb_pr(3)]

        prs = discovery.list_open_prs(
            "dflds", "content.hub", execute_read=fake_read, config=self._CFG)
        self.assertEqual([p["number"] for p in prs], [3])

    def test_missing_fields_degrade_rather_than_raise(self):
        # A sparse PR object (only an id) still yields a row; every other field
        # reads empty/false, and labels is always the empty list.
        def fake_read(op, params):
            return [{"id": 4}]

        prs = discovery.list_open_prs(
            "dflds", "content.hub", execute_read=fake_read, config=self._CFG)
        self.assertEqual(prs[0], {
            "url": "", "number": 4, "head_sha": "", "title": "",
            "author": "", "updated_at": "", "draft": False, "labels": [],
        })

    def test_draft_flag_is_passed_through(self):
        def fake_read(op, params):
            return [self._bb_pr(7, draft=True)]

        prs = discovery.list_open_prs(
            "dflds", "content.hub", execute_read=fake_read, config=self._CFG)
        self.assertTrue(prs[0]["draft"])

    def test_non_list_response_reads_as_no_prs(self):
        def fake_read(op, params):
            return None

        prs = discovery.list_open_prs(
            "dflds", "content.hub", execute_read=fake_read, config=self._CFG)
        self.assertEqual(prs, [])


class TestPinnedRepos(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_home = os.environ.get("KIROCREW_HOME")
        os.environ["KIROCREW_HOME"] = self.tmp
        store.ensure_layout()

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("KIROCREW_HOME", None)
        else:
            os.environ["KIROCREW_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_repos_missing_file(self):
        # ensure_layout does not create repos.json, so it is genuinely absent
        self.assertFalse(discovery.repos_path().exists())
        self.assertEqual(discovery.read_repos(), [])

    def test_read_repos_corrupt_file(self):
        discovery.repos_path().write_text("}{ not json", encoding="utf-8")
        self.assertEqual(discovery.read_repos(), [])

    def test_add_repo_is_idempotent_case_insensitive_newest_first(self):
        discovery.add_repo("acme", "widget")
        repos = discovery.add_repo("beta", "tool")
        self.assertEqual(repos[0]["full_name"], "beta/tool")   # newest first
        # re-add the first with different casing — must not duplicate, moves to front
        repos = discovery.add_repo("ACME", "WIDGET")
        self.assertEqual(repos[0]["full_name"], "ACME/WIDGET")
        matches = [r for r in repos if r["full_name"].lower() == "acme/widget"]
        self.assertEqual(len(matches), 1)

    def test_remove_repo_is_case_insensitive(self):
        discovery.add_repo("acme", "widget")
        repos = discovery.remove_repo("ACME", "WIDGET")
        self.assertEqual(repos, [])


class TestPinnedRepoReadIsGuarded(unittest.TestCase):
    """`read_repos` feeds the sidebar straight from a worker-writable file."""

    def setUp(self):
        self.root = pathlib.Path(tempfile.mkdtemp())
        store.ensure_layout(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, payload):
        path = discovery.repos_path(self.root)
        path.write_text(payload, encoding="utf-8")
        return path

    def test_a_credential_in_a_field_is_redacted(self):
        cred = "ghp_0123456789abcdefghijklmnopqrstuvwxyzA"
        self._write(json.dumps({"repos": [
            {"owner": "acme", "repo": "widgets", "note": "token " + cred},
        ]}))
        rows = discovery.read_repos(self.root)
        self.assertEqual(len(rows), 1)
        self.assertNotIn(cred, json.dumps(rows))
        # The fields the app keys on survive intact -- a real owner/repo never
        # matches a credential shape, so redaction is a no-op for them.
        self.assertEqual(rows[0]["owner"], "acme")
        self.assertEqual(rows[0]["repo"], "widgets")

    def test_a_row_with_a_non_string_identity_is_dropped(self):
        """`owner`/`repo` are checked for TYPE, not truthiness.

        A worker owns this file, and a planted dict or list is truthy, survives
        redaction (which walks strings), and would reach the client as an object
        React cannot render -- taking the whole page down instead of showing one
        bad row."""
        self._write(json.dumps({"repos": [
            {"owner": {"nested": "obj"}, "repo": "widgets"},
            {"owner": "acme", "repo": ["list"]},
            {"owner": 7, "repo": "widgets"},
            {"owner": "", "repo": "widgets"},
            {"owner": "good", "repo": "row"},
        ]}))
        rows = discovery.read_repos(self.root)
        self.assertEqual([(r["owner"], r["repo"]) for r in rows], [("good", "row")])

    def test_a_credential_in_a_key_is_redacted(self):
        """A worker writing this file controls key names as well as values."""
        cred = "ghp_0123456789abcdefghijklmnopqrstuvwxyzA"
        self._write(json.dumps({"repos": [
            {"owner": "acme", "repo": "widgets", cred: "planted in the key"},
        ]}))
        rows = discovery.read_repos(self.root)
        self.assertNotIn(cred, json.dumps(rows))

    def test_a_credential_nested_in_a_value_is_redacted(self):
        """A dict or list value must not smuggle a string past a str check."""
        cred = "ghp_0123456789abcdefghijklmnopqrstuvwxyzA"
        self._write(json.dumps({"repos": [
            {"owner": "acme", "repo": "widgets",
             "meta": {"inner": ["see " + cred]}},
        ]}))
        rows = discovery.read_repos(self.root)
        self.assertNotIn(cred, json.dumps(rows))

    def test_a_symlink_planted_at_the_path_is_refused(self):
        """The reader must not dereference a link a worker planted.

        Without the no-link guard a plain read hands back attacker-chosen JSON from
        anywhere the gateway can read, under the shape the sidebar trusts.
        """
        outside = self.root.parent / "elsewhere.json"
        # Register first: on Windows the symlink attempt below commonly skips,
        # and unittest does not resume at the final unlink after skipTest().
        self.addCleanup(outside.unlink, missing_ok=True)
        outside.write_text(json.dumps({"repos": [
            {"owner": "evil", "repo": "payload"}]}), encoding="utf-8")
        path = discovery.repos_path(self.root)
        if path.exists() or path.is_symlink():
            path.unlink()
        try:
            path.symlink_to(outside)
        except OSError:                 # pragma: no cover - platform without symlinks
            self.skipTest("symlinks unavailable")
        rows = discovery.read_repos(self.root)
        self.assertEqual(rows, [], "followed the plant: " + repr(rows))
        outside.unlink()


if __name__ == "__main__":
    unittest.main()
