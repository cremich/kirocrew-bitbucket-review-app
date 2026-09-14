"""Unit tests for the review pipeline + result store."""
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from sage_lib import pipeline as P  # noqa: N812
from sage_lib import results as R  # noqa: N812
from sage_lib import store

from tests.fixtures import BITBUCKET_PAYLOAD

from kiro_crew import platform_compat


class TestBatchParse(unittest.TestCase):
    def test_mixed_separators_and_dedup(self):
        text = ("https://code.amazon.com/reviews/CR-1\n"
                "CR-2, CR-2\n"
                "  https://github.com/o/r/pull/3  \n"
                "garbage not a link\n"
                "https://github.com/o/r/pull/3/files")
        out = P.parse_batch(text)
        # GitHub-PR-only: non-PR tokens dropped; the PR URL is normalized + deduped.
        self.assertEqual(out, ["https://github.com/o/r/pull/3"])

    def test_empty(self):
        self.assertEqual(P.parse_batch(""), [])

    def test_a_malformed_link_does_not_sink_the_batch(self):
        # "https://[::1" makes urlparse raise ValueError. The user-facing failure
        # this guards: one malformed entry in a pasted batch crashing the whole
        # request (HTTP 500) and discarding every valid link with it.
        out = P.parse_batch("https://github.com/o/r/pull/3\n"
                            "https://[::1\n"
                            "https://github.com/o/r/pull/4")
        self.assertEqual(out, ["https://github.com/o/r/pull/3",
                               "https://github.com/o/r/pull/4"])

    def test_ghe_links_keep_their_host(self):
        cfg = {"github_hosts": ["github.com", "acme.ghe.com"]}
        text = ("https://acme.ghe.com/org/repo/pull/9\n"
                "https://github.com/o/r/pull/3\n"
                "https://evil.example/github.com/x/y/pull/1")
        with mock.patch.object(P.adapters.store, "read_config_quiet",
                               return_value=cfg):
            out = P.parse_batch(text)
        # The GHE host survives normalization (flattening it to github.com would
        # point the review at the wrong instance); the path-spoof is dropped.
        self.assertEqual(out, ["https://acme.ghe.com/org/repo/pull/9",
                               "https://github.com/o/r/pull/3"])

    def test_prefixed_tokens_yield_their_embedded_url(self):
        # Bulleted/markdown lists are the normal clipboard shape from Slack or
        # an issue. The user-facing failure this guards: a prefixed link dropped
        # silently, so the batch under-reviews with no error. Each shape must yield
        # its embedded PR URL.
        text = ("- https://github.com/o/r/pull/1\n"
                "* https://github.com/o/r/pull/2\n"
                "[PR](https://github.com/o/r/pull/3)\n"
                "<https://github.com/o/r/pull/4>\n"
                "see https://github.com/o/r/pull/5\n"
                "https://github.com/o/r/pull/6")
        self.assertEqual(P.parse_batch(text),
                         [f"https://github.com/o/r/pull/{n}"
                          for n in range(1, 7)])

    def test_prefix_extraction_does_not_weaken_host_matching(self):
        # Extraction locates the URL; acceptance still runs on the PARSED
        # hostname. A prefixed path-spoof must stay refused, and a token whose
        # first URL is hostile must not fall through to a later friendly one.
        text = ("- https://evil.example/github.com/x/y/pull/1\n"
                "see https://evil.example/z https://github.com/o/r/pull/2\n"
                "* https://github.com.evil.example/o/r/pull/3")
        self.assertEqual(P.parse_batch(text), [])


class TestRulePack(unittest.TestCase):
    def test_unmapped_repo_returns_none(self):
        cfg = {"rule_packs": {"github.com/org/other": "some-pack"}}
        self.assertIsNone(P.rule_pack_for_repo("github.com/org/repo", cfg))

    def test_resolve_missing_pack(self):
        self.assertIsNone(P.resolve_rule_pack("no-such-pack-xyz"))

    def test_resolve_rejects_traversal(self):
        self.assertIsNone(P.resolve_rule_pack("../../etc/passwd"))


class TestCommentPayload(unittest.TestCase):
    def test_publish_always_false(self):
        f = {"severity": "red", "file": "a.py", "line": 12, "lang": "python",
             "snippet": "x = 1", "observation": "obs", "consequence": "harm",
             "suggestion": "fix"}
        p = P.build_comment_payload(f, "GH-o-r-9", "sha", platform="github")
        self.assertIs(p["publish"], False)
        self.assertIn("🔴", p["content"])
        self.assertIn("x = 1", p["content"])
        self.assertIn("[code-review-sage]", p["content"])
        self.assertEqual(p["path"], "a.py")

    def test_yellow_severity(self):
        p = P.build_comment_payload({"severity": "yellow", "observation": "o"}, "GH-o-r-1", "s")
        self.assertIn("🟡", p["content"])
        self.assertIs(p["publish"], False)

    def test_posting_spec_is_platform_keyed(self):
        self.assertIn("gh api", P.posting_spec("github")["tool"])
        self.assertIn("gh api", P.posting_spec("unknown")["tool"])  # github fallback

    def test_github_comment_payload_anchor(self):
        f = {"severity": "red", "file": "src/a.rs", "line": 12, "lang": "rust",
             "snippet": "x", "observation": "o", "consequence": "c", "suggestion": "s"}
        p = P.build_comment_payload(f, "GH-o-r-5", "deadbeef", platform="github")
        self.assertIs(p["publish"], False)          # PENDING review — human submits
        self.assertEqual(p["path"], "src/a.rs")
        self.assertEqual(p["line"], 12)
        self.assertEqual(p["side"], "RIGHT")
        self.assertEqual(p["commit_id"], "deadbeef")  # head SHA
        self.assertIn("🔴", p["content"])

    def test_unsupported_posting_platform_raises(self):
        with self.assertRaises(ValueError):
            P.build_comment_payload({"severity": "yellow", "observation": "o"},
                                    "x", "1", platform="gitlab")


class TestGithubReviewPayload(unittest.TestCase):
    def _rec(self):
        return {
            "revision": "abc123sha",
            "pending_comments": [
                {"kind": "finding", "file": "src/a.rs", "line": 5, "body": "F1 body"},
                {"kind": "finding", "file": "src/b.rs", "line": 9, "body": "F2 body"},
                {"kind": "design", "body": "Ship summary body"},
            ],
        }

    def test_builds_pending_review_envelope(self):
        pay = P.build_review_payload(self._rec())
        # No `event` key -> PENDING (unsubmitted) review — the draft invariant.
        self.assertNotIn("event", pay)
        self.assertEqual(pay["commit_id"], "abc123sha")
        self.assertEqual(pay["body"], "Ship summary body")
        self.assertEqual(len(pay["comments"]), 2)
        self.assertEqual(pay["comments"][0],
                         {"path": "src/a.rs", "line": 5, "side": "RIGHT", "body": "F1 body"})

    def test_unanchored_finding_folds_into_body(self):
        rec = {"revision": "s", "pending_comments": [
            {"kind": "finding", "file": "", "line": 0, "body": "no-anchor finding"},
            {"kind": "design", "body": "summary"},
        ]}
        pay = P.build_review_payload(rec)
        self.assertEqual(pay["comments"], [])          # nothing anchorable
        self.assertIn("summary", pay["body"])
        self.assertIn("no-anchor finding", pay["body"])  # folded in, not dropped

    def test_refuses_to_build_an_unanchored_payload(self):
        """No `revision` must refuse, not omit `commit_id`.

        GitHub anchors a review with no `commit_id` to the pull request's CURRENT
        head. The submit guard's stale-head check then compares the draft's head to
        the live head and matches — because GitHub stamped it at post time, not
        because anything reviewed that code. APPROVE would authorize an unreviewed
        head. `revision` is not a required result-contract key, so a contract-valid
        record can arrive without one; this is the refusal for that case.
        """
        rec = {"pending_comments": [{"kind": "design", "body": "s"}]}
        with self.assertRaises(ValueError) as ctx:
            P.build_review_payload(rec)
        self.assertIn("commit_id", str(ctx.exception))

    def test_refuses_when_revision_is_empty_or_whitespace(self):
        """An empty or blank `revision` is as unanchored as a missing one."""
        for rev in ("", "   ", None):
            with self.subTest(revision=rev):
                rec = {"revision": rev,
                       "pending_comments": [{"kind": "design", "body": "s"}]}
                with self.assertRaises(ValueError):
                    P.build_review_payload(rec)

    def test_redacts_bodies_at_egress(self):
        # Defense-in-depth: even if a body reaches the payload builder unredacted,
        # the external-egress point re-runs _redact.
        rec = {"revision": "XSECRETX", "pending_comments": [
            {"kind": "finding", "file": "src/XSECRETX.py", "line": 1, "body": "leak XSECRETX"},
            {"kind": "design", "body": "summary XSECRETX"},
        ]}
        with mock.patch("sage_lib.pipeline._redact",
                        lambda s: s.replace("XSECRETX", "[redacted]")):
            pay = P.build_review_payload(rec)
        blob = (pay["body"] + " " + " ".join(c["body"] for c in pay["comments"])
                + " " + " ".join(c["path"] for c in pay["comments"]) + " " + pay["commit_id"])
        self.assertNotIn("XSECRETX", blob)
        self.assertIn("[redacted]", blob)
        self.assertEqual(pay["comments"][0]["path"], "src/[redacted].py")  # path redacted
        self.assertEqual(pay["commit_id"], "[redacted]")                    # commit_id redacted


class TestFetchSpec(unittest.TestCase):
    def test_github_uses_gh_cli(self):
        spec = P.fetch_spec("github")
        self.assertIn("gh api", spec)
        self.assertIn("pulls/<number>/files", spec)

    def test_unknown_platform_falls_back_to_github(self):
        self.assertEqual(P.fetch_spec("gitlab"), P.fetch_spec("github"))

    def test_bitbucket_is_llm_instruction_via_rovo(self):
        spec = P.fetch_spec("bitbucket")
        # An LLM instruction string that drives the Rovo MCP — NOT a REST client.
        self.assertIn("Rovo", spec)
        self.assertIn("discover", spec)
        self.assertIn("workspaceId", spec)
        self.assertIn("repoId", spec)
        # It must tell the worker there is ONE PR-wide diff (no per-file array),
        # and must NOT instruct a direct REST call.
        self.assertNotIn("api.bitbucket.org", spec)
        self.assertNotIn("gh api", spec)

    def test_bitbucket_ignores_host_argument(self):
        # Bitbucket Cloud is single-host; passing a host must not change the spec.
        self.assertEqual(P.fetch_spec("bitbucket"),
                         P.fetch_spec("bitbucket", host="whatever"))


class TestBitbucketAllowlistGuard(unittest.TestCase):
    """The scope gate: an out-of-allowlist Bitbucket link is rejected BEFORE any
    fetch. Faked entirely at the config boundary — no transport, no REST client,
    no discovered tool names asserted."""

    CFG_ALLOWED: dict = {"bitbucket_repos": [{"workspace": "dflds", "repo": "content.hub"}]}
    CFG_EMPTY: dict = {"bitbucket_repos": []}

    def test_configured_target_passes_and_returns_ref(self):
        ws, repo, num = P.assert_bitbucket_allowed(
            "https://bitbucket.org/dflds/content.hub/pull-requests/42",
            self.CFG_ALLOWED)
        self.assertEqual((ws, repo, num), ("dflds", "content.hub", "42"))

    def test_case_insensitive_match(self):
        ws, repo, _ = P.assert_bitbucket_allowed(
            "https://bitbucket.org/DFLDS/Content.Hub/pull-requests/1",
            self.CFG_ALLOWED)
        self.assertEqual((ws.lower(), repo.lower()), ("dflds", "content.hub"))

    def test_out_of_scope_target_rejected(self):
        with self.assertRaises(P.adapters.UnsupportedPlatform):
            P.assert_bitbucket_allowed(
                "https://bitbucket.org/other/repo/pull-requests/1",
                self.CFG_ALLOWED)

    def test_fail_closed_when_unconfigured(self):
        # Empty allowlist -> every Bitbucket target refused.
        with self.assertRaises(P.adapters.UnsupportedPlatform):
            P.assert_bitbucket_allowed(
                "https://bitbucket.org/dflds/content.hub/pull-requests/42",
                self.CFG_EMPTY)

    def test_unparseable_link_rejected(self):
        with self.assertRaises(P.adapters.UnsupportedPlatform):
            P.assert_bitbucket_allowed("not a link", self.CFG_ALLOWED)

    def test_prepare_target_enforces_guard_before_normalize(self):
        # prepare_target is the deterministic backstop: an out-of-scope link must
        # raise WITHOUT the payload ever being normalized/reviewed.
        with self.assertRaises(P.adapters.UnsupportedPlatform):
            P.prepare_target(
                "https://bitbucket.org/other/repo/pull-requests/1",
                {"id": 1, "summary": {"raw": "x"}, "diff": ""},
                config=self.CFG_ALLOWED)

    def test_prepare_target_allows_configured_bitbucket_link(self):
        bundle = P.prepare_target(
            "https://bitbucket.org/dflds/content.hub/pull-requests/42",
            BITBUCKET_PAYLOAD, config=self.CFG_ALLOWED)
        self.assertEqual(bundle["target"]["platform"], "bitbucket")
        self.assertEqual(bundle["target"]["change_id"], "BB-dflds-content.hub-42")

    def test_github_link_unaffected_by_bitbucket_guard(self):
        # A GitHub link must not be gated by the Bitbucket allowlist.
        bundle = P.prepare_target(
            "https://github.com/org/repo/pull/5",
            {"number": 5, "body": "hello",
             "html_url": "https://github.com/org/repo/pull/5"},
            config={"bitbucket_repos": []})
        self.assertEqual(bundle["target"]["platform"], "github")


class TestResultStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = Path(self.tmp) / "apps" / "code-review-sage"
        store.ensure_layout(self.root)
        self.rec = {
            "schema": "code-review-sage-result", "version": 1,
            "change_id": "GH-o-r-555", "platform": "github",
            "repo_identity": "github.com/o/r", "revision": "2",
            "phase1": {"gate_verdict": "CONCERNS", "design_risk": "medium",
                       "criticality": "medium", "rationale": "ok"},
            "findings": [{"dimension": "security", "severity": "red"}],
        }

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_read_roundtrip(self):
        R.write_result(self.rec, self.root)
        got = R.read_result("GH-o-r-555", self.root)
        self.assertEqual(got["phase1"]["gate_verdict"], "CONCERNS")

    @unittest.skipUnless(
        platform_compat.IS_POSIX,
        "POSIX mode bits are unobservable on Windows: the owner-only lockdown there is an "
        "ACL (platform_compat.restrict_to_owner), and st_mode always reports 0o666.",
    )
    def test_mode_0600(self):
        p = R.write_result(self.rec, self.root)
        self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_list_results(self):
        R.write_result(self.rec, self.root)
        self.assertEqual(len(R.list_results(self.root)), 1)

    def test_validation_rejects_missing(self):
        bad = {"schema": "x", "version": 1, "change_id": "CR-1"}
        with self.assertRaises(ValueError):
            R.write_result(bad, self.root)

    def test_validation_bad_verdict(self):
        bad = dict(self.rec)
        bad["phase1"] = {"gate_verdict": "MAYBE", "design_risk": "low", "criticality": "low"}
        self.assertTrue(any("gate_verdict" in e for e in R.validate_result(bad)))

    def test_safe_change_id(self):
        self.assertEqual(R.safe_change_id("github:org/repo#123"), "github_org_repo_123")
        self.assertEqual(R.safe_change_id("CR-12345678"), "CR-12345678")


class TestCommentBuilders(unittest.TestCase):
    """The driver builds the CR comment bodies in Python and redacts them HERE
    (deterministic chokepoint); the poster posts them verbatim."""

    def test_build_ship_comment_redacts(self):
        rec: dict[str, Any] = {"phase1": {"gate_verdict": "CONCERNS"},
               "counts": {"red": 0, "yellow": 1}, "ship_summary": "leak XSECRETX"}
        with mock.patch("sage_lib.pipeline._redact",
                        lambda s: s.replace("XSECRETX", "[redacted]")):
            body = P.build_ship_comment(rec)
        self.assertIn("[redacted]", body)
        self.assertNotIn("XSECRETX", body)

    def test_build_ship_comment_ship_decision(self):
        # Good to ship: PASS + zero red -> "Good to ship"; should-fix counted but
        # never gates the call.
        ready = {"phase1": {"gate_verdict": "PASS"}, "counts": {"red": 0, "yellow": 2},
                 "ship_summary": "No blocking issues; 2 optional notes."}
        rbody = P.build_ship_comment(ready)
        self.assertIn("Good to ship", rbody)
        self.assertIn("2 should-fix", rbody)
        self.assertNotIn("Not ready", rbody)
        # A red must-fix flips it to not-ready.
        blocked = {"phase1": {"gate_verdict": "PASS"}, "counts": {"red": 1, "yellow": 0},
                   "ship_summary": "1 must-fix: unbounded cache."}
        bbody = P.build_ship_comment(blocked)
        self.assertIn("Not ready to ship", bbody)
        self.assertIn("1 must-fix", bbody)
        # A genuine design BLOCK is not-ready even with zero red findings.
        design = {"phase1": {"gate_verdict": "BLOCK"}, "counts": {"red": 0, "yellow": 0},
                  "ship_summary": "Wrong layer."}
        dbody = P.build_ship_comment(design)
        self.assertIn("Not ready to ship", dbody)
        self.assertIn("design flagged", dbody)

    def test_build_ship_comment_null_ship_summary_falls_back(self):
        # An explicit JSON null must NOT leak the literal "None" — the fallback
        # (design headline, then a deterministic phrase) has to fire.
        rec = {"phase1": {"gate_verdict": "PASS", "design_headline": None},
               "counts": {"red": 0, "yellow": 0}, "ship_summary": None}
        body = P.build_ship_comment(rec)
        self.assertNotIn("None", body)
        self.assertIn("No blocking must-fix issues found.", body)
        # Explicit-null ship_summary with a real design headline uses the headline.
        rec2 = {"phase1": {"gate_verdict": "PASS", "design_headline": "solid design"},
                "counts": {"red": 0, "yellow": 0}, "ship_summary": None}
        self.assertIn("solid design", P.build_ship_comment(rec2))

    def test_build_pending_comments_structure(self):
        rec: dict[str, Any] = {"phase1": {"gate_verdict": "CONCERNS"},
               "counts": {"red": 0, "yellow": 1}, "ship_summary": "s",
               "findings": [{"file": "a.py", "line": 5, "severity": "yellow",
                             "observation": "o", "consequence": "c",
                             "suggestion": "s", "snippet": "x"}]}
        pend = P.build_pending_comments(rec)
        self.assertEqual([e["kind"] for e in pend], ["finding", "design"])
        self.assertEqual((pend[0]["file"], pend[0]["line"]), ("a.py", 5))
        self.assertTrue(pend[0]["body"] and pend[1]["body"])
        # PASS -> the ship-readiness (design) comment is STILL emitted (always-on)
        rec["phase1"]["gate_verdict"] = "PASS"
        self.assertEqual([e["kind"] for e in P.build_pending_comments(rec)], ["finding", "design"])

    def test_build_pending_comments_redacts_all_bodies(self):
        rec = {"phase1": {"gate_verdict": "BLOCK", "solution_assessment": "XSECRETX"},
               "findings": [{"file": "a.py", "line": 1, "severity": "red",
                             "observation": "XSECRETX", "consequence": "c",
                             "suggestion": "s", "snippet": "y"}]}
        with mock.patch("sage_lib.pipeline._redact",
                        lambda s: s.replace("XSECRETX", "[redacted]")):
            pend = P.build_pending_comments(rec)
        joined = " ".join(e["body"] for e in pend)
        self.assertNotIn("XSECRETX", joined)
        self.assertIn("[redacted]", joined)


def _bb_record(red_lines=(2,), yellow_lines=(3,), unanchor_lines=(),
               revision="abc123") -> dict:
    """A Bitbucket result record with a diff whose hunk adds lines 2 and 3 on a.py.

    ``red_lines`` / ``yellow_lines`` are anchorable findings; ``unanchor_lines`` are
    🔴 findings on lines outside every hunk (they must fold into the summary)."""
    diff = ("diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n"
            "@@ -1,2 +1,4 @@\n"
            " ctx\n"          # new line 1 (context)
            "+added2\n"       # new line 2
            "+added3\n"       # new line 3
            " tail\n")        # new line 4 (context)
    findings = []
    for ln in red_lines:
        findings.append({"severity": "red", "file": "a.py", "line": ln,
                         "dimension": "correctness", "observation": "o",
                         "consequence": "c", "suggestion": "s", "snippet": "x"})
    for ln in unanchor_lines:
        findings.append({"severity": "red", "file": "a.py", "line": ln,
                         "dimension": "security", "observation": "o2",
                         "consequence": "c2", "suggestion": "s2", "snippet": "y"})
    for ln in yellow_lines:
        findings.append({"severity": "yellow", "file": "a.py", "line": ln,
                         "dimension": "style", "observation": "o3",
                         "consequence": "c3", "suggestion": "s3", "snippet": "z"})
    return {
        "schema": "code-review-sage-result", "version": 1, "change_id": "BB-ws-r-1",
        "platform": "bitbucket", "repo_identity": "bitbucket.org/ws/r",
        "revision": revision,
        "phase1": {"gate_verdict": "CONCERNS", "design_risk": "low",
                   "criticality": "low", "design_headline": "", "problem": "",
                   "why_it_matters": "", "solution_assessment": ""},
        "counts": {"red": len(red_lines) + len(unanchor_lines),
                   "yellow": len(yellow_lines)},
        "files": [{"path": "a.py", "diff": diff}],
        "findings": findings,
        "deep_reviewed": True, "title": "t", "ship_summary": "s",
    }


class TestBitbucketPublishBuilder(unittest.TestCase):
    """The Bitbucket publish work list: always-on marker summary + 🔴-only inline,
    hunk-header anchoring (added->to), unanchorable 🔴 fold into the summary."""

    def test_summary_always_present_and_marked(self):
        out = P.build_bitbucket_publish(_bb_record(red_lines=(), yellow_lines=()))
        self.assertIsNotNone(out["summary"])
        self.assertEqual(out["summary"]["key"], "design")
        # The summary carries its OWN marker (marker field is authoritative).
        self.assertTrue(out["summary"]["marker"].startswith("[code-review-sage:__summary__#summary#"))
        self.assertIn(out["summary"]["marker"], out["summary"]["body"])
        self.assertEqual(out["inline"], [])

    def test_inline_is_red_only(self):
        out = P.build_bitbucket_publish(_bb_record(red_lines=(2,), yellow_lines=(3,)))
        # One red -> one inline; the yellow does not get its own inline comment.
        self.assertEqual(len(out["inline"]), 1)
        self.assertEqual(out["inline"][0]["key"], "finding:0")

    def test_added_line_anchors_on_to(self):
        out = P.build_bitbucket_publish(_bb_record(red_lines=(2,), yellow_lines=()))
        entry = out["inline"][0]
        self.assertEqual(entry["path"], "a.py")
        self.assertEqual(entry.get("to"), 2)
        self.assertNotIn("from", entry)
        # Every inline comment carries a hidden marker matching its declared one.
        self.assertIn(entry["marker"], entry["body"])

    def test_unanchorable_red_folds_into_summary(self):
        # A red finding on line 999 (outside every hunk) must NOT be dropped; it
        # folds into the summary body.
        out = P.build_bitbucket_publish(
            _bb_record(red_lines=(2,), yellow_lines=(), unanchor_lines=(999,)))
        self.assertEqual(len(out["inline"]), 1)          # only the anchorable one
        self.assertIn("could not be anchored", out["summary"]["body"])
        # Units: summary + one inline.
        self.assertEqual(P.bitbucket_publish_units(out), 2)

    def test_marker_is_line_drift_tolerant(self):
        # The marker hash is over path+rule+body, NOT the line — so the same finding
        # on a different line produces the SAME marker (a fix that only shifts lines
        # must not resurrect a comment).
        a = P.bitbucket_marker("a.py", "correctness", "BODY")
        b = P.bitbucket_marker("a.py", "correctness", "BODY")
        self.assertEqual(a, b)
        # Different body -> different marker.
        c = P.bitbucket_marker("a.py", "correctness", "OTHER")
        self.assertNotEqual(a, c)

    def test_parse_marker_roundtrip(self):
        m = P.bitbucket_marker("dir/f.py", "security", "b")
        self.assertEqual(P.parse_bitbucket_marker(f"text {m} more"), m)
        self.assertEqual(P.parse_bitbucket_marker("no marker here"), "")

    def test_bodies_redacted(self):
        rec = _bb_record(red_lines=(2,), yellow_lines=())
        rec["findings"][0]["observation"] = "XSECRETX"
        with mock.patch("sage_lib.pipeline._redact",
                        lambda s: s.replace("XSECRETX", "[redacted]")):
            out = P.build_bitbucket_publish(rec)
        self.assertNotIn("XSECRETX", out["inline"][0]["body"])


class TestBitbucketAnchor(unittest.TestCase):
    """Hunk-header anchoring: added line -> to, removed line -> from, else None."""

    FILES = [{"path": "a.py", "diff": (
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -10,2 +10,3 @@\n"
        " keep10\n"           # old10 / new10 (context)
        "-removed11\n"        # old11 (removed)
        "+added11\n"          # new11 (added)
        "+added12\n"          # new12 (added)
        " keep13\n")}]        # old12 / new13 (context)

    def test_added_line_is_to(self):
        # new-side line 12 is a pure addition (no old-side counterpart) -> `to`.
        self.assertEqual(P.bitbucket_anchor(self.FILES, "a.py", 12), {"to": 12})

    def test_removed_line_is_from(self):
        # A unified diff lists the removed line before the added one, and old line
        # 11 is a pure removal -> `from`.
        self.assertEqual(P.bitbucket_anchor(self.FILES, "a.py", 11), {"from": 11})

    def test_context_line_anchors_on_to(self):
        # A shown-but-unchanged line anchors on the new side (Bitbucket accepts it).
        self.assertEqual(P.bitbucket_anchor(self.FILES, "a.py", 10), {"to": 10})

    def test_line_outside_hunk_is_none(self):
        self.assertIsNone(P.bitbucket_anchor(self.FILES, "a.py", 999))

    def test_file_not_in_diff_is_none(self):
        self.assertIsNone(P.bitbucket_anchor(self.FILES, "other.py", 11))

    def test_removed_side_from(self):
        files = [{"path": "d.py", "diff": (
            "diff --git a/d.py b/d.py\n--- a/d.py\n+++ b/d.py\n"
            "@@ -5,2 +5,1 @@\n"
            "-gone5\n"          # old line 5 removed
            " keep6\n")}]       # old6/new5 context
        self.assertEqual(P.bitbucket_anchor(files, "d.py", 5), {"from": 5})


if __name__ == "__main__":
    unittest.main()
