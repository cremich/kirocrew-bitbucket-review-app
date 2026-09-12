# Task Progress

**Spec:** `task-1.13 - Port-Code-Review-Sage-from-GitHub-to-Bitbucket-Cloud-spec.md`

**Status:** running

**Elapsed:** 60s

**Tokens:** 0

**Replans:** 0


## Tasks

- ⬜ **Task 1:** Add bitbucket_repos to config defaults in store.py
- ⬜ **Task 2:** Test config defaults
- ⬜ **Task 3:** Add detect_platform Bitbucket branch and bitbucket_pr_ref in adapters.py
- ⬜ **Task 4:** Add identity helpers and allowed_targets in adapters.py
- ⬜ **Task 5:** Add split_unified_diff in adapters.py
- ⬜ **Task 6:** Add parse_bitbucket_payload and route normalize() in adapters.py
- ⬜ **Task 7:** Extend test_adapters.py for the Bitbucket adapter
- ⬜ **Task 8:** Rename build_github_review_payload -> build_review_payload in pipeline.py
- ⬜ **Task 9:** Add Bitbucket branch to build_review_payload in pipeline.py
- ⬜ **Task 10:** Add Bitbucket entries to FETCH_SPECS and POSTING_SPECS in pipeline.py
- ⬜ **Task 11:** Extend test_pipeline.py for Bitbucket payload building
- ⬜ **Task 12:** Rename _draft_confirmed -> _posts_confirmed in review_driver.py
- ⬜ **Task 13:** Add Bitbucket posting and idempotency to review_driver.py
- ⬜ **Task 14:** Extend test_review_driver.py and test_post_comments.py for the poster
- ⬜ **Task 15:** Add hold_for_publish lifecycle in results.py / review_driver.py
- ⬜ **Task 16:** Test hold_for_publish survives the clear sweep
- ⬜ **Task 17:** Add fail-closed target guard and drop GitHub feed pickers in discovery/routes
- ⬜ **Task 18:** Add draft GET, /post, /approve, /request-changes routes and /repos CRUD
- ⬜ **Task 19:** Extend backend/route and discovery tests
- ⬜ **Task 20:** Update app.json manifest
- ⬜ **Task 21:** Run the full test suite as final verification