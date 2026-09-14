"""Draft preview GET — new vs published, staleness badge (TASK-1.13.8).

A reviewer opens a HELD draft in Sage's UI and sees every finding with its
proposed comment BEFORE publishing anything. ``GET /runs/{id}/draft?change_id=``
returns ``{change_id, revision, hold_for_publish, findings[], summary, inline[],
posted[], stale}``:

  * the work list (summary + inline[]) is derived LAZILY here from the record's
    findings via ``pipeline.build_pending_comments`` — never stored, so a
    re-publish and this preview always agree on what would be sent;
  * each item carries its marker ``key`` and a ``status: new|published`` derived
    from the record's ``posted_keys`` ledger (empty until 1.13.9 publishes);
  * ``stale`` is ONE live-head read compared against the stored ``revision``;
  * the endpoint posts NOTHING and mutates NOTHING.

The live-head read is faked at its transport seam (module-level ``_draft_live_head``),
mirroring how ``_verdict_dispatch`` / discovery's ``execute_read`` are faked — no
live Rovo/pool is touched. Kept in this NEW file to avoid colliding with sibling
tickets that edit test_backend_routes.py / test_run_endpoints.py.
"""
from __future__ import annotations

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

from sage_lib import results, store  # noqa: E402  (app root on sys.path above)


def _load_routes_module():
    spec = importlib.util.spec_from_file_location(
        "sage_backend_routes_draftget_under_test", str(_ROUTES))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# A Bitbucket Cloud PR URL and the change id the app derives from it. Kept in
# sync with adapters.bitbucket_change_id (BB-<ws>-<repo>-<n>).
_LINK = "https://bitbucket.org/dflds/content.hub/pull-requests/42"
_CID = "BB-dflds-content.hub-42"


def _rec(cid: str = _CID, *, revision: str = "headsha0", red: int = 1,
         yellow: int = 1, posted_keys=None) -> dict:
    """A minimal contract-valid draft record carrying findings + a revision.

    Two findings so the derived work list has two inline items plus the always-on
    ship-readiness (design) summary — enough to assert the new|published split.
    """
    rec = {
        "schema": "code-review-sage-result", "version": 1, "change_id": cid,
        "platform": "bitbucket", "repo_identity": "bitbucket.org/dflds/content.hub",
        "revision": revision, "title": cid,
        "phase1": {"gate_verdict": "CONCERNS", "design_risk": "medium",
                   "criticality": "low"},
        "blast_radius": {"rating": "SMALL", "signals": {}},
        "counts": {"red": red, "yellow": yellow},
        "findings": [
            {"dimension": "correctness", "severity": "red", "file": "a.py",
             "line": 10, "snippet": "x", "observation": "o1",
             "consequence": "c1", "suggestion": "s1"},
            {"dimension": "correctness", "severity": "yellow", "file": "b.py",
             "line": 20, "snippet": "y", "observation": "o2",
             "consequence": "c2", "suggestion": "s2"},
        ],
        # Per-file diffs so the 🔴 finding on a.py:10 anchors to a real new-side
        # line (build_bitbucket_publish would otherwise fold an unanchorable 🔴
        # into the summary). Mirrors what adapters.split_unified_diff produces.
        "files": [
            {"path": "a.py",
             "diff": "diff --git a/a.py b/a.py\n"
                     "--- a/a.py\n+++ b/a.py\n"
                     "@@ -8,2 +8,4 @@\n line8\n line9\n"
                     "+added10\n+added11\n"},
            {"path": "b.py",
             "diff": "diff --git a/b.py b/b.py\n"
                     "--- a/b.py\n+++ b/b.py\n"
                     "@@ -18,2 +18,4 @@\n l18\n l19\n+added20\n+added21\n"},
        ],
        "deep_reviewed": True, "files_covered": ["a.py", "b.py"],
        "coverage_complete": True,
    }
    if posted_keys is not None:
        rec["posted_keys"] = list(posted_keys)
    return rec


class _Req:
    """Minimal aiohttp request stand-in: match_info (the {run_id} path param) and
    a query mapping. The draft handler reads only those two — never a JSON body."""

    def __init__(self, run_id: str = "run-1", query: dict | None = None):
        self.match_info = {"run_id": run_id}
        self.query = query or {}


class _DraftGetTestBase(unittest.IsolatedAsyncioTestCase):
    """Isolated KIROCREW_HOME + a fresh routes module, with the live-head read
    faked so no pool is touched. A run and a draft record are registered per test
    via ``_seed``."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old_home = os.environ.get("KIROCREW_HOME")
        os.environ["KIROCREW_HOME"] = self.tmp
        store.ensure_layout()
        self.mod = _load_routes_module()
        self.run_id = "run-1"
        # Fake the ONE live-head read at its transport seam. Default: the PR head
        # matches the stored revision (not stale). Individual tests override
        # ``self.live_head`` to drive the staleness computation.
        self.live_head = "headsha0"
        self.head_calls: list[str] = []

        def _fake_head(link: str) -> str:
            self.head_calls.append(link)
            return self.live_head

        self.mod._draft_live_head = _fake_head

    def tearDown(self):
        if self._old_home is None:
            os.environ.pop("KIROCREW_HOME", None)
        else:
            os.environ["KIROCREW_HOME"] = self._old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, rec: dict | None = None, *, hold: bool = True) -> dict:
        """Register a done run for _LINK and write its draft record to disk."""
        rec = rec if rec is not None else _rec()
        results.write_result(rec, None, self.run_id)
        if hold:
            results.set_hold_for_publish(rec["change_id"], None, self.run_id)
        run = {
            "run_id": self.run_id,
            "changes": [_LINK],
            "change_ids": [rec["change_id"]],
            "status": "done",
        }
        self.mod._RUNS.insert(0, run)
        return run


class TestShape(_DraftGetTestBase):
    async def test_returns_documented_shape(self):
        self._seed()
        resp = await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))
        self.assertEqual(resp.status, 200)
        data = json.loads(resp.body)
        for key in ("change_id", "revision", "hold_for_publish", "findings",
                    "summary", "inline", "posted", "stale"):
            self.assertIn(key, data, f"missing {key} in draft payload")
        self.assertEqual(data["change_id"], _CID)
        self.assertEqual(data["revision"], "headsha0")
        self.assertIs(data["hold_for_publish"], True)

    async def test_work_list_split_summary_and_inline(self):
        # A Bitbucket draft previews EXACTLY what publish sends: an always-on
        # summary plus INLINE comments for Critical/High (🔴) findings ONLY. The
        # record has one 🔴 + one 🟡, so the 🔴 is the single inline item and the
        # 🟡 is reflected in the summary tally, never its own inline comment
        # (build_bitbucket_publish, the same builder the poster uses).
        self._seed()
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertEqual(len(data["inline"]), 1)
        self.assertIsNotNone(data["summary"])
        self.assertEqual(data["summary"]["kind"], "design")
        # findings[] mirrors the inline finding-kind items.
        self.assertEqual(len(data["findings"]), 1)
        for item in data["inline"]:
            self.assertEqual(item["kind"], "finding")

    async def test_every_item_carries_marker_key_and_status(self):
        self._seed()
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        for item in data["inline"] + [data["summary"]]:
            self.assertTrue(item.get("key"), "item missing marker key")
            self.assertIn(item.get("status"), ("new", "published"))


class TestNewVsPublished(_DraftGetTestBase):
    async def test_all_new_when_nothing_posted(self):
        # posted_keys empty (the state before any publish) -> every item is new.
        self._seed(_rec())
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertEqual(data["posted"], [])
        self.assertTrue(all(i["status"] == "new"
                            for i in data["inline"] + [data["summary"]]))

    async def test_published_when_marker_in_posted_ledger(self):
        # A key in posted_keys reads as published. On a Bitbucket draft the work
        # list is summary (design) + 🔴-only inline, so the record's one 🔴
        # (findings index 0) yields inline key finding:0; the 🟡 is not an inline
        # item. Marking finding:0 + design posted flips both to published.
        self._seed(_rec(posted_keys=["finding:0", "design"]))
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        by_key = {i["key"]: i["status"] for i in data["inline"] + [data["summary"]]}
        self.assertEqual(by_key["finding:0"], "published")
        self.assertEqual(by_key["design"], "published")
        # The 🟡 finding is not previewed as inline (publish never posts it).
        self.assertNotIn("finding:1", by_key)
        self.assertEqual(sorted(data["posted"]), ["design", "finding:0"])


class TestStaleness(_DraftGetTestBase):
    async def test_not_stale_when_head_matches_revision(self):
        self.live_head = "headsha0"   # equals the record's revision
        self._seed(_rec(revision="headsha0"))
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertIs(data["stale"], False)
        self.assertEqual(self.head_calls, [_LINK])   # exactly one live read

    async def test_stale_when_head_moved(self):
        self.live_head = "movedsha9"   # PR head advanced past the reviewed one
        self._seed(_rec(revision="headsha0"))
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertIs(data["stale"], True)

    async def test_head_compare_is_case_insensitive(self):
        # The stored revision may be upper/mixed case; the read is lower-cased.
        self.live_head = "abcdef1"
        self._seed(_rec(revision="ABCDEF1"))
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertIs(data["stale"], False)

    async def test_unreadable_head_reports_not_stale(self):
        # An empty read (transport failed / PR unreachable) must NOT flip the
        # badge — silence beats warning on every draft we cannot reach.
        self.live_head = ""
        self._seed(_rec(revision="headsha0"))
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertIs(data["stale"], False)

    async def test_no_read_when_revision_missing(self):
        # No stored revision -> nothing to compare, so the live read is skipped
        # entirely and the draft reports not-stale.
        rec = _rec()
        rec.pop("revision", None)
        self._seed(rec)
        data = json.loads((await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))).body)
        self.assertIs(data["stale"], False)
        self.assertEqual(self.head_calls, [])


class TestPureRead(_DraftGetTestBase):
    async def test_endpoint_does_not_mutate_the_record(self):
        # A preview must not touch the on-disk draft: same bytes before and after.
        self._seed(_rec(posted_keys=["finding:0"]))
        path = results.result_path(_CID, None, self.run_id)
        before = path.read_bytes()
        await self.mod._handle_run_draft(_Req(self.run_id, {"change_id": _CID}))
        self.assertEqual(path.read_bytes(), before)

    async def test_hold_flag_unchanged_by_preview(self):
        self._seed()
        await self.mod._handle_run_draft(_Req(self.run_id, {"change_id": _CID}))
        rec = results.read_result(_CID, None, self.run_id)
        self.assertIs(rec["hold_for_publish"], True)


class TestErrors(_DraftGetTestBase):
    async def test_unknown_run_404(self):
        # No run seeded.
        resp = await self.mod._handle_run_draft(
            _Req("nosuchrun", {"change_id": _CID}))
        self.assertEqual(resp.status, 404)
        self.assertEqual(json.loads(resp.body)["code"], "run_not_found")

    async def test_missing_change_id_400(self):
        self._seed()
        resp = await self.mod._handle_run_draft(_Req(self.run_id, {}))
        self.assertEqual(resp.status, 400)
        self.assertEqual(json.loads(resp.body)["code"], "change_id_required")

    async def test_no_draft_record_404(self):
        # Run exists but no record was written for this change (never held /
        # swept). 404 keyed on the change.
        run = {
            "run_id": self.run_id, "changes": [_LINK],
            "change_ids": [_CID], "status": "done",
        }
        self.mod._RUNS.insert(0, run)
        resp = await self.mod._handle_run_draft(
            _Req(self.run_id, {"change_id": _CID}))
        self.assertEqual(resp.status, 404)
        self.assertEqual(json.loads(resp.body)["code"], "draft_not_found")

    async def test_malformed_run_id_404(self):
        # _run_id_param VALIDATES the path param and raises HTTPNotFound for a
        # non-safe id (aiohttp renders that as a 404) — a path-traversal attempt
        # never reaches the record read.
        from aiohttp import web
        with self.assertRaises(web.HTTPNotFound):
            await self.mod._handle_run_draft(
                _Req("../etc", {"change_id": _CID}))


class TestRouteRegistration(_DraftGetTestBase):
    def test_draft_route_registered_as_get(self):
        from aiohttp import web
        app = web.Application()
        self.mod.register_routes(app)
        gets = {
            r.resource.canonical: r.handler
            for r in app.router.routes() if r.method == "GET"
        }
        draft = "/api/apps/code-review-sage/runs/{run_id}/draft"
        self.assertIn(draft, gets)
        self.assertIs(gets[draft], self.mod._handle_run_draft)
        # It is NOT the report handler under another name.
        report = "/api/apps/code-review-sage/runs/{run_id}/report"
        self.assertIsNot(gets[draft], gets[report])


if __name__ == "__main__":
    unittest.main()
