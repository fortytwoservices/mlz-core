"""Tests for the plan summary and the change list the apply compares."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci.summary import main, resource_type


def change(address: str, *actions: str) -> dict:
    return {"address": address, "change": {"actions": list(actions)}}


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def summarise(self, changes: list[dict], outputs: dict | None = None) -> tuple[str, str]:
        plan = self.dir / "plan.json"
        plan.write_text(json.dumps({"resource_changes": changes, "output_changes": outputs or {}}))
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
        self.assertEqual(changes.splitlines(), [
            'destroy azapi_resource.vending_rights["x"]',
            'add module.alz.azapi_resource.policy_assignments["mlz/A"]',
            'change module.alz.azapi_resource.policy_assignments["mlz/B"]',
            'replace module.alz.azapi_resource.policy_assignments["mlz/C"]',
        ])

    def test_no_changes(self):
        summary, changes = self.summarise([change("azapi_resource.x", "no-op")])
        self.assertEqual(summary, "**governance**: no changes.\n")
        self.assertEqual(changes, "")

    def test_outputs_alone_are_changes(self):
        # A contract that changes with no resource change must still apply, or it is never published.
        summary, changes = self.summarise(
            [change("azapi_resource.x", "no-op")],
            {"contract": {"actions": ["update"]}, "handover": {"actions": ["no-op"]}},
        )
        self.assertEqual(summary, "**governance**: no resource changes.\n\n**Outputs that change:** `contract`\n")
        self.assertEqual(changes, "change output.contract\n")

    def test_outputs_follow_the_resources(self):
        summary, changes = self.summarise(
            [change("azapi_resource.x", "create")],
            {"contract": {"actions": ["update"]}, "added": {"actions": ["create"]}},
        )
        self.assertIn("**Outputs that change:** `added`, `contract`", summary)
        self.assertEqual(changes.splitlines(), ["add azapi_resource.x", "add output.added", "change output.contract"])

    def test_resource_type_keeps_the_innermost_module(self):
        self.assertEqual(resource_type("module.management.module.law.azapi_resource.this"), "law: azapi_resource.this")
        self.assertEqual(resource_type('module.vending.module.vnet["a.b"].azapi_resource.peering["x"]'), "vnet: azapi_resource.peering")


if __name__ == "__main__":
    unittest.main()
