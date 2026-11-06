"""Tests for the plan summary and the change list the apply compares."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci.summary import main, resource_type


def change(address: str, *actions: str, after: dict | None = None) -> dict:
    return {"address": address, "change": {"actions": list(actions), "after": after, "after_unknown": {}}}


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def summarise(self, changes: list[dict]) -> tuple[str, str]:
        plan = self.dir / "plan.json"
        plan.write_text(json.dumps({"resource_changes": changes}))
        main(str(plan), "governance", str(self.dir / "summary.md"), str(self.dir / "changes.txt"))
        return (self.dir / "summary.md").read_text(), (self.dir / "changes.txt").read_text()

    def test_counts_types_and_names_every_change_to_an_existing_resource(self):
        summary, changes = self.summarise([
            change('module.alz.azapi_resource.policy_assignments["mlz/A"]', "create"),
            change('module.alz.azapi_resource.policy_assignments["mlz/B"]', "update"),
            change('module.alz.azapi_resource.policy_assignments["mlz/C"]', "delete", "create"),
            change('azapi_resource.vending_rights["x"]', "delete"),
            change("data.azapi_client_config.current", "read"),
            change("azapi_resource.unchanged", "no-op"),
        ])
        self.assertIn("**governance**: 1 to add, 1 to change, 1 to destroy, 1 to replace.", summary)
        self.assertIn("| `alz: azapi_resource.policy_assignments` | 1 | 1 | 1 |  |", summary)
        self.assertIn('- change: `module.alz.azapi_resource.policy_assignments["mlz/B"]`', summary)
        self.assertIn('- destroy: `azapi_resource.vending_rights["x"]`', summary)
        self.assertNotIn('"mlz/A"', summary)  # new resources are counted, not listed
        self.assertEqual([line.rsplit(" ", 1)[0] for line in changes.splitlines()], [
            'destroy azapi_resource.vending_rights["x"]',
            'add module.alz.azapi_resource.policy_assignments["mlz/A"]',
            'change module.alz.azapi_resource.policy_assignments["mlz/B"]',
            'replace module.alz.azapi_resource.policy_assignments["mlz/C"]',
        ])

    def test_the_change_list_tells_planned_values_and_action_order_apart(self):
        # The apply compares change lists; the same address and kind with other values must not match.
        _, reviewed = self.summarise([change("azapi_resource.x", "update", after={"tags": {"owner": "a"}})])
        _, same = self.summarise([change("azapi_resource.x", "update", after={"tags": {"owner": "a"}})])
        _, other = self.summarise([change("azapi_resource.x", "update", after={"tags": {"owner": "b"}})])
        _, create_first = self.summarise([change("azapi_resource.x", "create", "delete")])
        _, delete_first = self.summarise([change("azapi_resource.x", "delete", "create")])
        self.assertEqual(reviewed, same)
        self.assertNotEqual(reviewed, other)
        self.assertNotEqual(create_first, delete_first)
        self.assertRegex(reviewed, r"^change azapi_resource\.x [0-9a-f]{16}\n$")

    def test_no_changes(self):
        summary, changes = self.summarise([change("azapi_resource.x", "no-op")])
        self.assertEqual(summary, "**governance**: no changes.\n")
        self.assertEqual(changes, "")

    def test_resource_type_keeps_the_innermost_module(self):
        self.assertEqual(resource_type("module.management.module.law.azapi_resource.this"), "law: azapi_resource.this")
        self.assertEqual(resource_type('module.vending.module.vnet["a.b"].azapi_resource.peering["x"]'), "vnet: azapi_resource.peering")


if __name__ == "__main__":
    unittest.main()
