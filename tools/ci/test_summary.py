"""Tests for the plan summary and the change list the apply compares."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci.summary import main, resource_type


def change(address: str, *actions: str, after: dict | None = None, before: dict | None = None, deposed: str | None = None) -> dict:
    record = {"address": address, "change": {"actions": list(actions), "before": before, "after": after, "after_unknown": {}}}
    if deposed:
        record["deposed"] = deposed
    return record


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def summarise(self, changes: list[dict], outputs: dict | None = None) -> tuple[str, str]:
        plan = self.dir / "plan.json"
        plan.write_text(json.dumps({"resource_changes": changes, "output_changes": outputs or {}}))
        self.exit_code = main(str(plan), "governance", str(self.dir / "summary.md"), str(self.dir / "changes.txt"))
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

    def test_deleting_another_resource_at_the_same_address_does_not_match(self):
        _, reviewed = self.summarise([change("azapi_resource.x", "delete", before={"id": "/subscriptions/s/a"})])
        _, other = self.summarise([change("azapi_resource.x", "delete", before={"id": "/subscriptions/s/b"})])
        self.assertNotEqual(reviewed, other)

    def test_a_deposed_object_keeps_its_own_line(self):
        _, changes = self.summarise([
            change("azapi_resource.x", "update", after={"v": 1}),
            change("azapi_resource.x", "delete", before={"id": "old"}, deposed="00000001"),
        ])
        lines = [line.rsplit(" ", 1)[0] for line in changes.splitlines()]
        self.assertEqual(lines, ["change azapi_resource.x", "destroy azapi_resource.x deposed:00000001"])
        _, other = self.summarise([
            change("azapi_resource.x", "update", after={"v": 2}),
            change("azapi_resource.x", "delete", before={"id": "old"}, deposed="00000001"),
        ])
        self.assertNotEqual(changes, other)  # the current object's values still count

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
        self.assertRegex(changes, r"^change output\.contract [0-9a-f]{16}\n$")

    def test_outputs_follow_the_resources(self):
        summary, changes = self.summarise(
            [change("azapi_resource.x", "create")],
            {"contract": {"actions": ["update"]}, "added": {"actions": ["create"]}},
        )
        self.assertIn("**Outputs that change:** `added`, `contract`", summary)
        self.assertEqual([line.rsplit(" ", 1)[0] for line in changes.splitlines()], ["add azapi_resource.x", "add output.added", "change output.contract"])

    def test_output_values_count_in_the_change_list(self):
        # The apply rejects a plan whose outputs would get other values than the reviewer saw.
        def output(after, after_unknown=False):
            return {"contract": {"actions": ["update"], "after": after, "after_unknown": after_unknown}}
        _, reviewed = self.summarise([], output({"root": "mlz"}))
        _, same = self.summarise([], output({"root": "mlz"}))
        _, other_value = self.summarise([], output({"root": "other"}))
        _, now_unknown = self.summarise([], output({"root": "mlz"}, after_unknown=True))
        self.assertEqual(reviewed, same)
        self.assertNotEqual(reviewed, other_value)
        self.assertNotEqual(reviewed, now_unknown)

    def test_a_plan_that_replaces_or_deletes_a_firewall_policy_is_refused(self):
        # The rules live inside the policy and are applied by their own stack, planned before the hub applies.
        address = 'module.hub.module.firewall["norwayeast"].module.firewall_policy[0].azapi_resource.this'
        policy = {"id": "/subscriptions/s/firewallPolicies/afwp-mlz-hub-nwe", "name": "afwp-mlz-hub-nwe",
                  "type": "Microsoft.Network/firewallPolicies@2024-05-01"}
        for actions, verb in ((("create", "delete"), "replaces"), (("delete", "create"), "replaces"), (("delete",), "deletes")):
            with self.subTest(actions=actions):
                summary, changes = self.summarise([change(address, *actions, before=policy)])
                self.assertEqual(self.exit_code, 3)
                self.assertTrue(summary.startswith(f"**Refused:** this plan {verb} the firewall policy `afwp-mlz-hub-nwe`"))
                self.assertIn(address, changes)  # the change list is still written, for the run page

    def test_policy_updates_and_rule_group_deletions_are_not_refused(self):
        policy = {"id": "p", "name": "afwp-mlz-hub-nwe", "type": "Microsoft.Network/firewallPolicies@2024-05-01"}
        group = {"id": "g", "name": "lz-07", "type": "Microsoft.Network/firewallPolicies/ruleCollectionGroups@2024-05-01"}
        summary, _ = self.summarise([
            change("module.hub.azapi_resource.policy", "update", before=policy, after=policy),
            change('module.rule_collection_group["norwayeast/lz-07"].azapi_resource.this', "delete", before=group),
        ])
        self.assertEqual(self.exit_code, 0)
        self.assertNotIn("Refused", summary)

    def test_resource_type_keeps_the_innermost_module(self):
        self.assertEqual(resource_type("module.management.module.law.azapi_resource.this"), "law: azapi_resource.this")
        self.assertEqual(resource_type('module.vending.module.vnet["a.b"].azapi_resource.peering["x"]'), "vnet: azapi_resource.peering")


if __name__ == "__main__":
    unittest.main()
