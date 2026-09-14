"""Verdict endpoints — Approve / Request-changes (TASK-1.13.5).

A reviewer records a verdict on a Bitbucket Cloud pull request FROM Sage without
switching to the Bitbucket web UI. Two SEPARATE endpoints, kept distinct from
Publish (``/runs/{id}/post``) so a one-way verdict never fires by accident while
merely posting comments.

These tests fake the Rovo executeWrite transport at the SAME boundary the poster
uses (an injected ``(task, timeout) -> {ok, output, error}`` dispatch — here the
module-level ``_verdict_dispatch`` seam). They assert the request SHAPE Sage
builds — which op, and the ``{workspaceId, repoId, prId}`` target it names — never
tool names, and they lock in the fail-closed allowlist gate and the separation
from the publish endpoint.

Kept in this NEW file (not test_backend_routes.py) to avoid colliding with the
sibling ticket 1.13.6 which also edits routes.py + that test file.
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent
_ROUTES = _APP_ROOT / "backend" / "routes.py"
if str(_APP_ROOT) not in sys.path:
    sys.path.insert(0, str(_APP_ROOT))

from sage_lib import store  # noqa: E402  (app root added to sys.path above)


def _load_routes_module():
    spec = importlib.util.spec_from_file_location(
        "sage_backend_routes_verdict_under_test", str(_ROUTES))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Req:
    """Minimal aiohttp request stand-in: a method and an async JSON body, which
    is all the verdict handlers touch (they read the body, never route params —
    the target arrives in the body, not the URL)."""

    def __init__(self, method="POST", body=None):
        self.method = method
        self._body = body

    async def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class _VerdictTestBase(unittest.IsolatedAsyncioTestCase):
    """Shared setup: an isolated KIROCREW_HOME so config writes are sandboxed,
    a freshly loaded routes module, and a fake executeWrite dispatch that RECORDS
    the task+timeout it was handed instead of reaching a live pool."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_home = os.environ.get("KIROCREW_HOME")
        os.environ["KIROCREW_HOME"] = self.tmp
        self.mod = _load_routes_module()
        # Fake the executeWrite boundary: capture what Sage dispatches.
        self.dispatched = []

        def _fake_dispatch(task, timeout):
            self.dispatched.append({"task": task, "timeout": timeout})
            return {"ok": True, "output": "verdict recorded", "error": ""}

        self.mod._verdict_dispatch = _fake_dispatch

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("KIROCREW_HOME", None)
        else:
            os.environ["KIROCREW_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _allow(self, workspace="dflds", repo="content.hub"):
        """Put one target into the fail-closed allowlist (config.json)."""
        store.ensure_layout()
        cfg_path = store.data_dir() / "config.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["bitbucket_repos"] = [{"workspace": workspace, "repo": repo}]
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        # Sanity: the resolver honours what we just wrote.
        assert (workspace, repo) in store.allowed_targets(store.load_config())


class TestApprove(_VerdictTestBase):
    async def test_approve_fires_for_in_scope_pr(self):
        self._allow()
        resp = await self.mod._handle_approve(
            _Req(body={"workspaceId": "dflds", "repoId": "content.hub",
                       "prId": "42"}))
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "approve")
        # Exactly one dispatch fired.
        self.assertEqual(len(self.dispatched), 1)

    async def test_approve_task_names_the_approve_op_and_target(self):
        # SHAPE assertion: the instruction names the approve executeWrite op and
        # carries the {workspaceId, repoId, prId} target — no verdict/body field.
        self._allow()
        await self.mod._handle_approve(
            _Req(body={"workspaceId": "dflds", "repoId": "content.hub",
                       "prId": "42"}))
        task = self.dispatched[0]["task"]
        self.assertIn("approveBitbucketRepoPullRequest", task)
        self.assertNotIn("requestChangesOnBitbucketRepoPullRequest", task)
        self.assertIn('"workspaceId": "dflds"', task)
        self.assertIn('"repoId": "content.hub"', task)
        self.assertIn('"prId": "42"', task)

    async def test_approve_refuses_out_of_allowlist(self):
        # Nothing configured -> fail closed, and NO dispatch fires (a verdict is
        # one-way; an out-of-scope target must never reach the transport).
        resp = await self.mod._handle_approve(
            _Req(body={"workspaceId": "evilcorp", "repoId": "content.hub",
                       "prId": "42"}))
        self.assertEqual(resp.status, 403)
        self.assertEqual(json.loads(resp.body)["code"], "target_not_allowed")
        self.assertEqual(self.dispatched, [])

    async def test_approve_missing_fields_rejected(self):
        self._allow()
        for body in ({}, {"workspaceId": "dflds"},
                     {"workspaceId": "dflds", "repoId": "content.hub"}):
            resp = await self.mod._handle_approve(_Req(body=body))
            self.assertEqual(resp.status, 400)
        self.assertEqual(self.dispatched, [])


class TestRequestChanges(_VerdictTestBase):
    async def test_request_changes_fires_for_in_scope_pr(self):
        self._allow()
        resp = await self.mod._handle_request_changes(
            _Req(body={"workspaceId": "dflds", "repoId": "content.hub",
                       "prId": "7"}))
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.body)
        self.assertTrue(data["ok"])
        self.assertEqual(data["action"], "request-changes")
        self.assertEqual(len(self.dispatched), 1)

    async def test_request_changes_task_names_the_op_and_target(self):
        self._allow()
        await self.mod._handle_request_changes(
            _Req(body={"workspaceId": "dflds", "repoId": "content.hub",
                       "prId": "7"}))
        task = self.dispatched[0]["task"]
        self.assertIn("requestChangesOnBitbucketRepoPullRequest", task)
        self.assertNotIn("approveBitbucketRepoPullRequest", task)
        self.assertIn('"workspaceId": "dflds"', task)
        self.assertIn('"repoId": "content.hub"', task)
        self.assertIn('"prId": "7"', task)

    async def test_request_changes_refuses_out_of_allowlist(self):
        resp = await self.mod._handle_request_changes(
            _Req(body={"workspaceId": "dflds", "repoId": "not-configured",
                       "prId": "7"}))
        self.assertEqual(resp.status, 403)
        self.assertEqual(self.dispatched, [])


class TestVerdictBoundaryProperties(_VerdictTestBase):
    """Properties that hold across BOTH verdict endpoints."""

    async def test_allowlist_match_is_case_insensitive(self):
        # Configured lowercase; a target arriving in mixed case still resolves,
        # matching Bitbucket slug semantics / store.allowed_targets.
        self._allow(workspace="dflds", repo="content.hub")
        resp = await self.mod._handle_approve(
            _Req(body={"workspaceId": "DFLDS", "repoId": "Content.Hub",
                       "prId": "42"}))
        self.assertEqual(resp.status, 200)
        self.assertEqual(len(self.dispatched), 1)

    async def test_verdict_task_carries_no_verdict_body_field(self):
        # The ops take {workspaceId, repoId, prId} and NOTHING else (verified
        # live). The instruction must say so, so the worker cannot invent a body.
        self._allow()
        await self.mod._handle_approve(
            _Req(body={"workspaceId": "dflds", "repoId": "content.hub",
                       "prId": "42"}))
        task = self.dispatched[0]["task"].lower()
        self.assertIn("no verdict, body, or comment field", task)

    async def test_dispatch_failure_surfaces_as_502(self):
        # A verdict op that does not complete is reported, not swallowed as ok.
        self._allow()

        def _fail(task, timeout):
            return {"ok": False, "output": "", "error": "rovo timeout"}

        self.mod._verdict_dispatch = _fail
        resp = await self.mod._handle_request_changes(
            _Req(body={"workspaceId": "dflds", "repoId": "content.hub",
                       "prId": "9"}))
        self.assertEqual(resp.status, 502)
        self.assertEqual(json.loads(resp.body)["code"], "verdict_failed")

    def test_verdict_routes_are_separate_from_publish(self):
        # The two verdict handlers are distinct callables from the publish/post
        # handler — a verdict endpoint is not the post endpoint under another name.
        self.assertIsNot(self.mod._handle_approve, self.mod._handle_run_post)
        self.assertIsNot(self.mod._handle_request_changes,
                         self.mod._handle_run_post)
        self.assertIsNot(self.mod._handle_approve,
                         self.mod._handle_request_changes)

    def test_verdict_routes_registered_on_their_own_paths(self):
        # Registration wires /approve and /request-changes as their own POST
        # routes, distinct from /post — the ownership contract for this ticket.
        from aiohttp import web
        app = web.Application()
        self.mod.register_routes(app)
        posts = {
            r.resource.canonical: r.handler
            for r in app.router.routes() if r.method == "POST"
        }
        approve = "/api/apps/code-review-sage/runs/{run_id}/approve"
        reqchg = "/api/apps/code-review-sage/runs/{run_id}/request-changes"
        post = "/api/apps/code-review-sage/runs/{run_id}/post"
        self.assertIn(approve, posts)
        self.assertIn(reqchg, posts)
        self.assertIn(post, posts)
        self.assertIs(posts[approve], self.mod._handle_approve)
        self.assertIs(posts[reqchg], self.mod._handle_request_changes)
        # And the verdict routes are NOT the publish handler.
        self.assertIsNot(posts[approve], posts[post])
        self.assertIsNot(posts[reqchg], posts[post])


if __name__ == "__main__":
    unittest.main()
