"""Tests for the fail-closed Bitbucket target allowlist (TASK-1.13.2).

``store.allowed_targets(config)`` is the single resolution point for "which
Bitbucket ``(workspace, repo)`` pairs may Sage touch". Unlike
``adapters.allowed_hosts``, it has NO safe default: an unconfigured Sage
resolves the EMPTY set and every later Bitbucket action refuses (fail closed).
These tests lock that contract in.
"""
import unittest

from sage_lib import store


class TestAllowedTargetsFailClosed(unittest.TestCase):
    """The load-bearing regression: unconfigured -> zero targets."""

    def test_empty_config_resolves_zero_targets(self):
        # An operator who has configured nothing gets an empty allowlist, so
        # scope revalidation downstream refuses every target.
        self.assertEqual(store.allowed_targets({}), frozenset())

    def test_missing_key_resolves_zero_targets(self):
        # A config that exists but never mentions bitbucket_repos is still
        # fail-closed -- no key is treated exactly like an empty list.
        self.assertEqual(
            store.allowed_targets({"version": 1, "github_hosts": ["github.com"]}),
            frozenset(),
        )

    def test_empty_list_resolves_zero_targets(self):
        self.assertEqual(store.allowed_targets({"bitbucket_repos": []}), frozenset())

    def test_default_config_is_fail_closed(self):
        # The seeded default must itself be empty -- a fresh install reviews no
        # Bitbucket repo until an operator opts one in.
        self.assertEqual(store.DEFAULT_CONFIG["bitbucket_repos"], [])
        self.assertEqual(store.DEFAULT_BITBUCKET_REPOS, [])
        self.assertEqual(store.allowed_targets(store.DEFAULT_CONFIG), frozenset())


class TestAllowedTargetsConfigured(unittest.TestCase):
    """When configured, the exact (workspace, repo) pairs come back."""

    def test_returns_configured_pairs(self):
        cfg = {
            "bitbucket_repos": [
                {"workspace": "dflds", "repo": "content.hub"},
                {"workspace": "acme", "repo": "widgets"},
            ]
        }
        self.assertEqual(
            store.allowed_targets(cfg),
            frozenset({("dflds", "content.hub"), ("acme", "widgets")}),
        )

    def test_matching_is_case_insensitive(self):
        # Bitbucket slugs are case-insensitive, so a configured DFLDS/Content.Hub
        # normalizes to the lowercased pair (and dedups against a lowercase twin).
        cfg = {
            "bitbucket_repos": [
                {"workspace": "DFLDS", "repo": "Content.Hub"},
                {"workspace": "dflds", "repo": "content.hub"},
            ]
        }
        self.assertEqual(
            store.allowed_targets(cfg), frozenset({("dflds", "content.hub")})
        )


class TestAllowedTargetsMalformedNarrows(unittest.TestCase):
    """Malformed config narrows scope; it never widens it or raises."""

    def test_non_dict_rows_are_skipped(self):
        cfg = {"bitbucket_repos": ["dflds/content.hub", 42, None,
                                    {"workspace": "ok", "repo": "good"}]}
        self.assertEqual(store.allowed_targets(cfg), frozenset({("ok", "good")}))

    def test_partial_rows_are_skipped(self):
        cfg = {"bitbucket_repos": [
            {"workspace": "dflds"},          # no repo
            {"repo": "content.hub"},          # no workspace
            {"workspace": "", "repo": "x"},   # empty workspace
            {"workspace": "y", "repo": "  "}, # blank repo
            {"workspace": "keep", "repo": "me"},
        ]}
        self.assertEqual(store.allowed_targets(cfg), frozenset({("keep", "me")}))

    def test_non_list_value_is_fail_closed(self):
        # A bitbucket_repos that is a string/dict/None is treated as unconfigured.
        for bad in ("dflds/content.hub", {"workspace": "x"}, None, 7):
            with self.subTest(bad=bad):
                self.assertEqual(
                    store.allowed_targets({"bitbucket_repos": bad}), frozenset()
                )


if __name__ == "__main__":
    unittest.main()
