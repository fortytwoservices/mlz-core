"""Tests for the plan summary and the change list the apply compares."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci.summary import main, resource_type, unknown_only

PLANS = Path(__file__).resolve().parent / "fixtures" / "plans"  # real plans, Terraform 1.17.0, one per case (L15)


def change(address: str, *actions: str, after: dict | None = None, before: dict | None = None, deposed: str | None = None,
           type: str | None = None) -> dict:
    record = {"address": address, "change": {"actions": list(actions), "before": before, "after": after, "after_unknown": {}}}
    if deposed:
        record["deposed"] = deposed
    if type:
        record["type"] = type
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

    def test_a_repos_merge_settings_do_not_count_in_its_update(self):
        # Vending's plan token reads a repo's merge settings as unset and its apply token as they are: an update for
        # another reason, a new description here, must still match between the two.
        repo = 'github_repository.workload["made"]'
        unset = {"description": "new", "allow_squash_merge": False, "delete_branch_on_merge": False, "squash_merge_commit_title": None}
        real = {"description": "new", "allow_squash_merge": True, "delete_branch_on_merge": True, "squash_merge_commit_title": "COMMIT_OR_PR_TITLE"}
        _, plan = self.summarise([change(repo, "update", after=unset, type="github_repository")])
        _, apply = self.summarise([change(repo, "update", after=real, type="github_repository")])
        self.assertEqual(plan, apply)
        # The values vending does manage still count, and a repo that is made has its merge settings counted.
        _, other = self.summarise([change(repo, "update", after={**real, "description": "other"}, type="github_repository")])
        self.assertNotEqual(plan, other)
        _, made_unset = self.summarise([change(repo, "create", after=unset, type="github_repository")])
        _, made_real = self.summarise([change(repo, "create", after=real, type="github_repository")])
        self.assertNotEqual(made_unset, made_real)
        # Other resources keep every value.
        _, x = self.summarise([change("azapi_resource.x", "update", after={"delete_branch_on_merge": False}, type="azapi_resource")])
        _, y = self.summarise([change("azapi_resource.x", "update", after={"delete_branch_on_merge": True}, type="azapi_resource")])
        self.assertNotEqual(x, y)

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

    def real(self, name: str) -> tuple[int, list[str], str]:
        """A real plan's exit code, its change list's kinds and addresses, and its summary."""
        code = main(str(PLANS / f"{name}.json"), name, str(self.dir / "summary.md"), str(self.dir / "changes.txt"))
        lines = [" ".join(line.split()[:2]) for line in (self.dir / "changes.txt").read_text().splitlines()]
        return code, lines, (self.dir / "summary.md").read_text()

    def test_real_plans_have_changes_exactly_when_terraform_says_applyable(self):
        # Each list against what the plan holds, and Terraform's applyable agreeing with the list being non-empty.
        expected = {
            "g-noop": [],
            "a-import": ["import random_id.imported"],  # -detailed-exitcode said 2, and the old list was empty: never applied
            "b-moved": ["move terraform_data.a2"],  # the same
            "c-removed": ["forget terraform_data.a"],  # the same; "forget" is missing from json-format's list of actions
            "d-drift-ignored": [],  # drift on an ignored attribute changes nothing
            "d2-drift-managed": ["change tfcoremock_simple_resource.d"],
            "e-output": ["change output.o"],
            "f-unknown-only": ["unknown terraform_data.a"],
        }
        for name, lines in expected.items():
            code, found, _ = self.real(name)
            self.assertEqual((code, found), (0, lines), name)
            self.assertEqual(json.loads((PLANS / f"{name}.json").read_text())["applyable"], bool(lines), name)

    def test_state_only_changes_say_so(self):
        self.assertIn("- imports `random_id.imported`", self.real("a-import")[2])
        self.assertIn("- moves `terraform_data.a` to `terraform_data.a2`", self.real("b-moved")[2])
        summary = self.real("c-removed")[2]
        self.assertIn("**In the state only, nothing in Azure changes**", summary)
        self.assertIn("- forgets `terraform_data.a`: it stays in Azure and leaves the state", summary)
        self.assertIn("**c-removed**: no resource changes.", summary)

    def test_an_update_only_to_unknown_values(self):
        summary = self.real("f-unknown-only")[2]
        self.assertIn("1 of the changes only to values unknown until apply, which may end unchanged", summary)
        self.assertIn("- change, to values unknown until apply: `terraform_data.a`", summary)
        # A known difference beside the unknown ones is a change; so is an update with nothing to show and nothing unknown.
        self.assertFalse(unknown_only({"actions": ["update"], "before": {"a": 1, "b": 2}, "after": {"a": 1, "b": 3}, "after_unknown": {"c": True}}))
        self.assertTrue(unknown_only({"actions": ["update"], "before": {"a": 1, "c": [1]}, "after": {"a": 1}, "after_unknown": {"c": True}}))
        self.assertFalse(unknown_only({"actions": ["update"], "before": {"a": 1}, "after": {"a": 1}, "after_unknown": {}}))
        # A map's key removed is a known difference, even where its value was null; so is a boolean becoming a number.
        self.assertFalse(unknown_only({"actions": ["update"], "before": {"tags": {"k": None}, "c": 1}, "after": {"tags": {}}, "after_unknown": {"c": True}}))
        self.assertFalse(unknown_only({"actions": ["update"], "before": {"f": True, "c": 1}, "after": {"f": 1}, "after_unknown": {"c": True}}))
        self.assertTrue(unknown_only({"actions": ["update"], "before": {"n": 1, "c": 1}, "after": {"n": 1.0}, "after_unknown": {"c": True}}))
        # A map's key added with a value unknown until apply: it is only in after_unknown, and the key is a known change.
        self.assertFalse(unknown_only({"actions": ["update"], "before": {"input": {}}, "after": {"input": {}}, "after_unknown": {"input": {"new_key": True}}}))

    def test_a_moved_object_left_deposed_keeps_its_previous_address(self):
        moved = {"address": "azapi_resource.x", "previous_address": "azapi_resource.old", "deposed": "a1b2",
                 "change": {"actions": ["no-op"], "before": {}, "after": {}, "after_unknown": {}}}
        summary, changes = self.summarise([moved])
        self.assertIn("- moves `azapi_resource.old` to `azapi_resource.x deposed:a1b2`", summary)
        self.assertTrue(changes.startswith("move azapi_resource.x deposed:a1b2 "))

    def test_a_long_list_of_state_only_changes_is_cut_short(self):
        # A module moved wholesale: the summary stays short enough for a pull request comment, as the rest of it does.
        moved = [{"address": f"azapi_resource.x[{n}]", "previous_address": f"azapi_resource.old[{n}]",
                  "change": {"actions": ["no-op"], "before": {}, "after": {}, "after_unknown": {}}} for n in range(250)]
        summary, changes = self.summarise(moved)
        self.assertEqual(len(changes.splitlines()), 250)  # the list the apply compares keeps every one
        self.assertEqual(summary.count("\n- moves "), 200)
        self.assertIn("- and 50 more; the full plan is attached to the run.", summary)

    def test_a_plan_terraform_disagrees_with_fails(self):
        # applyable, with nothing on the list: a kind of change this file does not know. Never skipped, never guessed.
        plan = json.loads((PLANS / "g-noop.json").read_text())
        plan["applyable"] = True
        (self.dir / "odd.json").write_text(json.dumps(plan))
        self.assertEqual(main(str(self.dir / "odd.json"), "odd", str(self.dir / "s.md"), str(self.dir / "c.txt")), 1)
        plan.update(applyable=False, errored=True)
        (self.dir / "odd.json").write_text(json.dumps(plan))
        self.assertEqual(main(str(self.dir / "odd.json"), "odd", str(self.dir / "s.md"), str(self.dir / "c.txt")), 1)

    def test_an_incomplete_plan_is_a_change_and_says_another_run_is_needed(self):
        plan = json.loads((PLANS / "g-noop.json").read_text())
        plan.update(complete=False, applyable=True, deferred_changes=[{"reason": "provider_config_unknown"}])
        (self.dir / "deferred.json").write_text(json.dumps(plan))
        self.assertEqual(main(str(self.dir / "deferred.json"), "x", str(self.dir / "s.md"), str(self.dir / "c.txt")), 0)
        self.assertTrue((self.dir / "c.txt").read_text().startswith("deferred plan "))
        self.assertIn("**Not complete:** Terraform says another plan and apply are needed", (self.dir / "s.md").read_text())

    def test_resource_type_keeps_the_innermost_module(self):
        self.assertEqual(resource_type("module.management.module.law.azapi_resource.this"), "law: azapi_resource.this")
        self.assertEqual(resource_type('module.vending.module.vnet["a.b"].azapi_resource.peering["x"]'), "vnet: azapi_resource.peering")


if __name__ == "__main__":
    unittest.main()
