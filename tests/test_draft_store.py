"""Draft-store lifecycle: retain-until-published (TASK-1.13.7).

The completed review record IS the Sage draft. ``clear_results`` fires at the
NEXT run's START, so records already persist run-to-run; the one thing that would
wipe an unpublished draft is that start-of-run sweep. These tests pin the four
rules the lifecycle spec (TASK-1.11) settled:

  * a held draft (``hold_for_publish=true``, not fully posted) survives the sweep;
  * the hold releases on full read-back confirmation OR explicit discard — no TTL;
  * findings + ``revision`` are persisted on the record; the work list is derived;
  * an unheld / stale / malformed record is still swept (the skip is not a leak).
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from sage_lib import results
from sage_lib import store


def _rec(cid: str, *, revision: str = "abc123", red: int = 0, yellow: int = 1) -> dict:
    """A minimal contract-valid record carrying findings + a revision anchor."""
    return {
        "schema": "code-review-sage-result", "version": 1, "change_id": cid,
        "platform": "bitbucket", "repo_identity": "bitbucket.org/o/r",
        "revision": revision, "title": cid,
        "phase1": {"gate_verdict": "CONCERNS", "design_risk": "medium",
                   "criticality": "low"},
        "blast_radius": {"rating": "SMALL", "signals": {}},
        "counts": {"red": red, "yellow": yellow},
        "findings": [{"dimension": "correctness", "severity": "yellow",
                      "file": "f.py", "line": 3, "snippet": "x", "observation": "o",
                      "consequence": "c", "suggestion": "s"}],
        "deep_reviewed": True, "files_covered": ["f.py"], "coverage_complete": True,
    }


class _RootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "apps" / "code-review-sage"
        store.ensure_layout(self.root)
        self.run_id = "run-1"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rec: dict) -> None:
        results.write_result(rec, self.root, self.run_id)

    def _ids(self) -> list[str]:
        return sorted(r["change_id"] for r in
                      results.list_results(self.root, self.run_id))


class TestSweepSkip(_RootTest):
    """Acceptance: a held, not-fully-posted draft survives the start-of-run clear."""

    def test_held_unposted_draft_survives_the_clear_sweep(self):
        self._write(_rec("CR-HELD"))
        self.assertTrue(
            results.set_hold_for_publish("CR-HELD", self.root, self.run_id,
                                         revision="head-sha"))
        self._write(_rec("CR-PLAIN"))   # not held

        removed = results.clear_results(self.root, self.run_id)

        # Only the un-held record was swept; the held draft remains.
        self.assertEqual(removed, 1)
        self.assertEqual(self._ids(), ["CR-HELD"])
        # And it kept the revision it was pinned against.
        held = results.read_result("CR-HELD", self.root, self.run_id)
        self.assertEqual(held["revision"], "head-sha")
        self.assertIs(held["hold_for_publish"], True)

    def test_hold_flag_alone_is_not_enough_a_fully_posted_draft_is_swept(self):
        # hold_for_publish=true but the draft has been published + confirmed:
        # it has served its purpose and is sweep-eligible again.
        rec = _rec("CR-DONE")
        rec["hold_for_publish"] = True
        rec["post_ok"] = True
        rec["posting_expected"] = 2
        rec["posted_comments"] = 2
        self._write(rec)

        self.assertFalse(results.is_held(rec))
        self.assertEqual(results.clear_results(self.root, self.run_id), 1)
        self.assertEqual(self._ids(), [])

    def test_a_partially_posted_held_draft_still_survives(self):
        # Publish confirmed fewer units than expected: not fully posted, so still
        # held. Losing it here would strand the findings that never landed.
        rec = _rec("CR-PARTIAL")
        rec["hold_for_publish"] = True
        rec["post_ok"] = True
        rec["posting_expected"] = 3
        rec["posted_comments"] = 1
        self._write(rec)

        self.assertTrue(results.is_held(rec))
        self.assertEqual(results.clear_results(self.root, self.run_id), 0)
        self.assertEqual(self._ids(), ["CR-PARTIAL"])


class TestClearConditions(_RootTest):
    """Acceptance: the flag clears on confirmation or discard; no TTL."""

    def test_explicit_discard_releases_the_hold(self):
        self._write(_rec("CR-1"))
        results.set_hold_for_publish("CR-1", self.root, self.run_id)
        self.assertTrue(results.is_held(
            results.read_result("CR-1", self.root, self.run_id)))

        self.assertTrue(results.discard_draft("CR-1", self.root, self.run_id))

        rec = results.read_result("CR-1", self.root, self.run_id)
        self.assertIs(rec["hold_for_publish"], False)
        self.assertFalse(results.is_held(rec))
        # Now sweep-eligible.
        self.assertEqual(results.clear_results(self.root, self.run_id), 1)

    def test_full_readback_confirmation_releases_the_hold(self):
        # The publish path writes post_ok + posting_expected + posted_comments onto
        # the record; fully_posted() then reads true and the draft stops being held.
        rec = _rec("CR-2")
        rec["hold_for_publish"] = True
        self._write(rec)
        self.assertTrue(results.is_held(
            results.read_result("CR-2", self.root, self.run_id)))

        published = results.read_result("CR-2", self.root, self.run_id)
        published["post_ok"] = True
        published["posting_expected"] = 1
        published["posted_comments"] = 1
        results.write_result(published, self.root, self.run_id)

        self.assertFalse(results.is_held(
            results.read_result("CR-2", self.root, self.run_id)))
        self.assertEqual(results.clear_results(self.root, self.run_id), 1)

    def test_no_ttl_repeated_sweeps_never_expire_a_held_draft(self):
        self._write(_rec("CR-3"))
        results.set_hold_for_publish("CR-3", self.root, self.run_id)
        for _ in range(5):
            self.assertEqual(results.clear_results(self.root, self.run_id), 0)
        self.assertEqual(self._ids(), ["CR-3"])

    def test_discard_returns_false_when_there_is_nothing_to_discard(self):
        self.assertFalse(results.discard_draft("MISSING", self.root, self.run_id))
        self._write(_rec("CR-4"))   # exists but not held
        self.assertFalse(results.discard_draft("CR-4", self.root, self.run_id))

    def test_set_hold_returns_false_when_there_is_no_record(self):
        self.assertFalse(
            results.set_hold_for_publish("NOPE", self.root, self.run_id))


class TestFullyPostedPredicate(_RootTest):
    """fully_posted is the release condition — pin its edges directly."""

    def test_nothing_expected_is_not_fully_posted(self):
        # The run-completion non-auto-post state: post_ok True but posting_expected
        # 0. That must NOT read as fully posted, or the draft would never be held.
        rec = _rec("CR-5")
        rec["post_ok"] = True
        rec["posting_expected"] = 0
        rec["posted_comments"] = 0
        self.assertFalse(results.fully_posted(rec))

    def test_unconfirmed_post_is_not_fully_posted(self):
        rec = _rec("CR-6")
        rec["post_ok"] = False   # spawn returned but delivery unconfirmed
        rec["posting_expected"] = 2
        rec["posted_comments"] = 2
        self.assertFalse(results.fully_posted(rec))

    def test_a_truthy_string_does_not_count_as_posted(self):
        rec = _rec("CR-7")
        rec["post_ok"] = "true"   # not the boolean True
        rec["posting_expected"] = 1
        rec["posted_comments"] = 1
        self.assertFalse(results.fully_posted(rec))

    def test_confirmed_full_delivery_is_fully_posted(self):
        rec = _rec("CR-8")
        rec["post_ok"] = True
        rec["posting_expected"] = 2
        rec["posted_comments"] = 2
        self.assertTrue(results.fully_posted(rec))


class TestSweepIsNotALeak(_RootTest):
    """A skip that honored anything on disk would be a persistence hole."""

    def test_unheld_records_are_swept_as_before(self):
        self._write(_rec("A"))
        self._write(_rec("B"))
        self.assertEqual(results.clear_results(self.root, self.run_id), 2)
        self.assertEqual(self._ids(), [])

    def test_a_none_record_is_not_held(self):
        # read_result returns None for a missing/malformed/symlinked path; None is
        # never held, so the sweep stays free to remove it.
        self.assertFalse(results.is_held(None))
        self.assertFalse(results.fully_posted(None))


if __name__ == "__main__":
    unittest.main()
