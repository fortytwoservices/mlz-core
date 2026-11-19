"""Tests for state_version.py: what the apply compares to refuse a state another run changed after the review."""

from __future__ import annotations

import json
import unittest

from ci.state_version import version


class StateVersionTest(unittest.TestCase):
    def test_a_state_with_resources_is_its_lineage_and_serial(self):
        state = {"version": 4, "lineage": "abc-123", "serial": 41, "resources": [{"type": "azapi_resource", "name": "x"}]}
        self.assertEqual(version(json.dumps(state)), "abc-123 41")
        self.assertNotEqual(version(json.dumps({**state, "serial": 42})), version(json.dumps(state)))  # another run applied
        self.assertNotEqual(version(json.dumps({**state, "lineage": "other"})), version(json.dumps(state)))  # a state put back

    def test_no_state_and_an_empty_one_are_both_none(self):
        # A plan of a new stack finds no blob; the apply's backend may have written an empty state for the key.
        self.assertEqual(version(""), "none")
        self.assertEqual(version("  \n"), "none")
        self.assertEqual(version(json.dumps({"version": 4, "lineage": "new", "serial": 1, "resources": []})), "none")


if __name__ == "__main__":
    unittest.main()
