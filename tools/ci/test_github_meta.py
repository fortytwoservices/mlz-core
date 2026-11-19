"""Tests for github_meta.py, against a gh that answers as GitHub does: none of them reaches the network."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci import github_meta

OK = "HTTP/2.0 200 OK\nContent-Type: application/json\nX-Ratelimit-Remaining: 999\n\n{\"domains\": {\"actions\": [\"github.com\"]}}"
# Resets at 14:00 UTC on 19 November 2026.
LIMITED = ("HTTP/2.0 403 Forbidden\nX-Ratelimit-Limit: 1000\nX-Ratelimit-Remaining: 0\nX-Ratelimit-Reset: 1795096800\n\n"
           "{\"message\": \"API rate limit exceeded for installation.\"}")


class GitHubMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / ".mlz" / "github-meta.json"
        self.said: list[str] = []

    def tearDown(self):
        self.tmp.cleanup()

    def fetch(self, stdout, code=0, stderr=""):
        self.calls = []

        def run(command, **kwargs):
            self.calls.append(command)
            return mock.Mock(returncode=code, stdout=stdout, stderr=stderr)
        return github_meta.fetch(self.out, run=run, say=self.said.append)

    def test_the_meta_is_saved_for_the_check(self):
        self.assertEqual(self.fetch(OK), 0)
        self.assertEqual(self.out.read_text(), '{"domains": {"actions": ["github.com"]}}')
        self.assertEqual(self.calls, [["gh", "api", "--include", "meta"]])  # gh, with the job's GH_TOKEN: no token handled here

    def test_a_rate_limit_used_up_says_until_when_and_is_not_retried(self):
        self.assertEqual(self.fetch(LIMITED, code=1, stderr="gh: API rate limit exceeded for installation. (HTTP 403)"), 1)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.said, ["::error::GitHub's API rate limit for this repository's token is used up until 14:00 UTC: GitHub's meta "
                                     "(api.github.com/meta), which the platform's check needs, could not be read. Run the workflow again then."])
        self.assertFalse(self.out.exists())

    def test_any_other_failure_gives_github_s_answer(self):
        self.assertEqual(self.fetch("HTTP/2.0 502 Bad Gateway\n\n", code=1, stderr="gh: Bad Gateway (HTTP 502)"), 1)
        self.assertEqual(self.said, ["::error::GitHub's meta (api.github.com/meta) could not be read: gh: Bad Gateway (HTTP 502)"])

    def test_a_403_that_is_not_the_rate_limit_is_not_called_one(self):
        self.assertIsNone(github_meta.rate_limited(403, {"x-ratelimit-remaining": "57"}))
        self.assertEqual(github_meta.rate_limited(429, {"x-ratelimit-remaining": "0"}), "it resets (within the hour)")

    def test_crlf_and_mixed_case_headers(self):
        status, headers, body = github_meta.parse("HTTP/1.1 200 OK\r\nX-RateLimit-Remaining: 5\r\n\r\n{}")
        self.assertEqual((status, headers, body), (200, {"x-ratelimit-remaining": "5"}, "{}"))


if __name__ == "__main__":
    unittest.main()
