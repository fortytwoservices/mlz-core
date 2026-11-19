"""Tests for the stack table and mlz map: every stack the pipeline plans is in the table, and every settings file of
the starters maps to exactly one stack."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from mlz import stacks

STARTERS = stacks.CORE / "starter"


class StacksTest(unittest.TestCase):
    def test_the_table_has_the_platform_s_stacks_in_the_order_the_pipeline_applies_them(self):
        check = (stacks.CORE / "tools" / "ci" / "check.sh").read_text()
        planned = re.findall(r"""echo '(\[[^']*\])'""", check)
        full = max((eval(found) for found in planned), key=len)  # the platform's list once vending has published
        self.assertEqual(full, [s.name for s in stacks.STACKS if s.role == "platform"])
        self.assertIn('["governance"]', check)

    def test_a_contract_is_read_from_the_state_of_the_stack_whose_root_outputs_it(self):
        # publish.sh reads the contract from a state blob, <stack>.tfstate: the stack is not always named after its role.
        # (The bootstrap's contract is published by mlz install, from its own state.)
        publish = (stacks.CORE / "tools" / "ci" / "publish.sh").read_text()
        for role in ("governance", "platform"):
            [stack] = [s for s in stacks.STACKS if s.role == role and s.name != "bootstrap" and 'output "contract"' in "".join(
                tf.read_text() for tf in (stacks.CORE / s.root).glob("*.tf"))]
            expected = "*) key=$role.tfstate" if stack.name == role else f"{role}) key={stack.name}.tfstate"
            self.assertIn(expected, publish, role)

    def test_every_root_is_there_and_names_its_modules(self):
        for stack in stacks.STACKS:
            self.assertTrue((stacks.CORE / stack.root).is_dir(), stack.root)
            self.assertTrue(stacks.modules(stacks.CORE / stack.root), stack.root)
        # A pinned module carries its pin, and a local module's own calls follow it.
        platform = stacks.modules(stacks.CORE / "platform" / "terraform")
        self.assertTrue(any(re.fullmatch(r"az-tf-mlz-connectivity-hub v\d+\.\d+\.\d+", m) for m in platform), platform)
        networks = stacks.modules(stacks.CORE / "platform" / "networks" / "terraform")
        self.assertEqual(networks[0], "modules/platform-network (mlz-core)")
        self.assertTrue(networks[1].startswith("  "))

    def test_each_starter_s_map_lists_every_settings_file_once_and_each_feeds_a_stack(self):
        for role in ("governance", "platform", "vending"):
            with self.subTest(role=role):
                repo = STARTERS / f"mlz-{role}"
                printed = stacks.render(repo)
                files = stacks.settings_files(repo)
                self.assertTrue(files)
                for path in files:
                    self.assertEqual(len(re.findall(rf"^  {re.escape(path)} +-> ", printed, re.M)), 1, path)
                    if not path.startswith("examples/"):
                        self.assertIsNotNone(stacks.stack_of(role, path), path)
                self.assertNotIn("NOT READ", printed)
                for stack in (s for s in stacks.STACKS if s.role == role):
                    self.assertIn(f"  {stack.name}: {stack.root}", printed)

    def test_a_vending_file_names_the_landing_zones_it_feeds(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            shutil.copytree(STARTERS / "mlz-vending", repo, dirs_exist_ok=True)
            shutil.copytree(repo / "examples" / "payments", repo / "landing-zones" / "payments")
            (repo / "defaults" / "prod-defaults.yaml").write_text("budget:\n  monthly: 5\n")
            (repo / "_generated").mkdir()
            (repo / "_generated" / "governance.yaml").write_text("{}\n")
            printed = stacks.render(repo)
        for path, feeds in (("defaults/landing-zone-defaults.yaml", "every landing zone"), ("defaults/prod-defaults.yaml", "every landing zone in prod"),
                            ("landing-zones/payments/workload-defaults.yaml", "every landing zone of payments"),
                            ("landing-zones/payments/prod.yaml", "payments-prod"), ("examples/payments/prod.yaml", "example: not read")):
            self.assertRegex(printed, rf"\n  {re.escape(path)} +-> {feeds}\n")
        self.assertIn("  governance.yaml: written by the governance repo's pipeline, after each apply. What governance decided", printed)

    def test_a_file_no_stack_reads_is_said_to_be_so(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            shutil.copytree(STARTERS / "mlz-platform", repo, dirs_exist_ok=True)
            (repo / "hubs").mkdir()
            (repo / "hubs" / "norwayeast.yaml").write_text("bastion: true\n")
            (repo / "firewall-rules" / "payments-prod.yaml").write_text("outbound: []\n")  # belongs in landing-zones/
            printed = stacks.render(repo)
        for path in ("hubs/norwayeast.yaml", "firewall-rules/payments-prod.yaml"):  # a folder from before L10; a misplaced file
            self.assertRegex(printed, rf"\n  {re.escape(path)} +-> NOT READ by any stack\n")

    def test_governance_s_files_are_read_by_their_exact_names(self):
        self.assertEqual(stacks.stack_of("governance", "customer.yaml").name, "governance")
        self.assertIsNone(stacks.stack_of("governance", "customer.yml"))
        self.assertIsNone(stacks.stack_of("governance", "bootstrap.yml"))

    def test_a_landing_zone_file_outside_a_workload_folder_is_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            repo = Path(folder)
            shutil.copytree(STARTERS / "mlz-vending", repo, dirs_exist_ok=True)
            (repo / "landing-zones" / "payments.yaml").write_text("owner: a@b.c\n")
            (repo / "landing-zones" / "web" / "prod").mkdir(parents=True)
            (repo / "landing-zones" / "web" / "prod" / "network.yaml").write_text("network:\n  size: 26\n")
            printed = stacks.render(repo)
        self.assertRegex(printed, r"\n  landing-zones/payments\.yaml +-> NOT READ by any stack\n")
        self.assertRegex(printed, r"\n  landing-zones/web/prod/network\.yaml +-> web-prod\n")  # a chunk of web-prod


class BatchesTest(unittest.TestCase):
    def test_one_stack_per_job_for_a_change_and_groups_on_the_daily_check(self):
        self.assertEqual(stacks.batches(["a", "b"], daily=False), [{"id": "a", "stacks": "a"}, {"id": "b", "stacks": "b"}])
        self.assertEqual(stacks.batches(["a", "b"], daily=True), [{"id": "group-001", "stacks": "a b"}])
        hundred = [f"lz-{n:03d}" for n in range(100)]
        daily = stacks.batches(hundred, daily=True)
        self.assertEqual(len(daily), 100 // stacks.BATCH)  # N stacks, N/20 jobs
        self.assertEqual(" ".join(b["stacks"] for b in daily).split(), hundred)  # every stack once, in order
        self.assertEqual(len(stacks.batches(hundred, daily=False)), 5)  # an upgrade plans everything: groups too
        self.assertEqual(len(stacks.batches(hundred[:20], daily=False)), 20)
        self.assertEqual(stacks.batches([], daily=True), [])


class ChangedTest(unittest.TestCase):
    """What a pull request or a push plans: the stacks whose rendered config differs from the base commit's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.head, self.base = Path(self.tmp.name) / "head", Path(self.tmp.name) / "base"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, root: Path, path: str, text: str) -> None:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text)

    def test_vending_plans_the_landing_zones_whose_config_changed_and_new_ones(self):
        for root in (self.head, self.base):
            self.write(root, ".mlz/vending/a-prod.json", "{}")
            self.write(root, ".mlz/vending/b-prod.json", "{}")
        self.write(self.head, ".mlz/vending/b-prod.json", '{"budget": 5}')
        self.write(self.head, ".mlz/vending/c-dev.json", "{}")  # new: the base had none
        self.assertEqual(stacks.changed("vending", ["a-prod", "b-prod", "c-dev"], self.head, self.base), ["b-prod", "c-dev"])

    def test_the_platform_s_three_stacks_share_one_config_and_the_rules_have_their_own(self):
        for root in (self.head, self.base):
            self.write(root, ".mlz/platform.json", "{}")
            self.write(root, ".mlz/firewall.json", "{}")
        every = [s.name for s in stacks.STACKS if s.role == "platform"]
        self.assertEqual(stacks.changed("platform", every, self.head, self.base), [])
        self.write(self.head, ".mlz/firewall.json", '{"rules": 1}')
        self.assertEqual(stacks.changed("platform", every, self.head, self.base), ["firewall-rules"])
        self.write(self.head, ".mlz/platform.json", '{"hubs": 1}')
        self.assertEqual(stacks.changed("platform", every, self.head, self.base), ["connectivity", "management", "identity", "firewall-rules"])

    def test_governance_reads_its_rendered_library_and_the_bootstrap_s_contract(self):
        for root in (self.head, self.base):
            self.write(root, ".mlz/rendered/archetypes/corp.json", "{}")
            self.write(root, "_generated/bootstrap.yaml", "state: {}\n")
        self.assertEqual(stacks.changed("governance", ["governance"], self.head, self.base), [])
        self.write(self.head, ".mlz/rendered/archetypes/online.json", "{}")  # a file more in the folder
        self.assertEqual(stacks.changed("governance", ["governance"], self.head, self.base), ["governance"])
        self.write(self.base, ".mlz/rendered/archetypes/online.json", "{}")
        self.write(self.head, "_generated/bootstrap.yaml", "state: {account: x}\n")
        self.assertEqual(stacks.changed("governance", ["governance"], self.head, self.base), ["governance"])

    def test_a_failed_check_fails_check_sh_and_prints_no_stacks(self):
        # check.sh works out the stacks in a command substitution, where bash clears errexit: a failed render must
        # still fail the script, not fall through to "nothing to plan".
        import os
        import subprocess
        from mlz.contract import render
        from mlz.test_platform import GOVERNANCE
        repo = self.head
        shutil.copytree(STARTERS / "mlz-platform", repo)
        (repo / ".mlz-core").symlink_to(stacks.CORE)
        (repo / "_generated").mkdir()
        (repo / "_generated" / "governance.yaml").write_text(render("governance", GOVERNANCE))
        (repo / "connectivity" / "norwayeast.yaml").write_text("platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\nfirewall: Gold\n")
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_EVENT_NAME", "PYTHONPATH")}
        done = subprocess.run([str(repo / ".mlz-core" / "tools" / "ci" / "check.sh"), "platform"], cwd=repo, env=env, capture_output=True, text=True)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("firewall 'Gold' must be one of", done.stderr)
        self.assertEqual(done.stdout, "")

    def test_the_difference_lists_each_changed_setting_against_the_base(self):
        self.write(self.head, ".mlz/platform.json", '{"hubs": {"norwayeast": {"firewall": {"sku_tier": "Premium"}, "reserved": ["10.9.0.0/16"]}}}')
        self.assertEqual(stacks.difference("platform", "connectivity", self.head), "")  # no base copy: every stack was planned
        self.write(self.head, ".mlz/base/platform.json", '{"hubs": {"norwayeast": {"firewall": {"sku_tier": "Standard"}}}, "old": 1}')
        self.assertEqual(stacks.difference("platform", "connectivity", self.head).splitlines(), [
            "**connectivity**: 3 settings changed (`.mlz/platform.json` against the base branch)", "",
            "| Setting | Base branch | This pull request |", "|---|---|---|",
            '| `hubs.norwayeast.firewall.sku_tier` | `"Standard"` | `"Premium"` |',
            '| `hubs.norwayeast.reserved` | (none) | `["10.9.0.0/16"]` |',
            "| `old` | `1` | (none) |"])
        self.write(self.head, ".mlz/vending/pay-prod.json", '{"budget": {"monthly": 2000}}')
        self.assertEqual(stacks.difference("vending", "pay-prod", self.head), "**pay-prod** is new: the base branch has no such stack.\n")
        self.write(self.head, ".mlz/base/vending/pay-prod.json", '{"budget": {"monthly": 2000}}')
        self.assertIn("its settings are as on the base branch", stacks.difference("vending", "pay-prod", self.head))
        self.write(self.head, ".mlz/rendered/config.json", '{"root_id": "demo", "naming": {}}')
        self.write(self.head, ".mlz/base/rendered/config.json", '{"root_id": "demo", "naming": {"org": "dm"}}')
        self.assertEqual(stacks.difference("governance", "governance", self.head).splitlines()[4:], ['| `naming.org` | `"dm"` | (none) |'])

    def test_a_pull_request_that_changes_one_value_shows_that_one_difference(self):
        # check.sh on a pull request keeps the base's configs in .mlz/base/; mlz diff then shows the one setting changed.
        import os
        import subprocess
        from mlz.contract import render
        from mlz.test_platform import GOVERNANCE
        repo = self.head
        shutil.copytree(STARTERS / "mlz-platform", repo)
        (repo / "_generated").mkdir()
        (repo / "_generated" / "governance.yaml").write_text(render("governance", GOVERNANCE))
        (repo / ".gitignore").write_text(".mlz/\n.mlz-core\n")
        hub = "platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\nfirewall: Standard\n"
        (repo / "connectivity" / "norwayeast.yaml").write_text(hub)
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_EVENT_NAME", "PYTHONPATH")}
        env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        git = lambda *a: subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *a], cwd=repo, env=env, check=True, capture_output=True)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "base")
        (repo / "connectivity" / "norwayeast.yaml").write_text(hub.replace("Standard", "Premium"))
        git("commit", "-qam", "the change")
        (repo / ".mlz-core").symlink_to(stacks.CORE)
        done = subprocess.run([str(repo / ".mlz-core" / "tools" / "ci" / "check.sh"), "platform"], cwd=repo,
                              env={**env, "GITHUB_EVENT_NAME": "pull_request"}, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout.strip().splitlines()[-1]), ["connectivity", "management", "identity"])
        self.assertTrue((repo / ".mlz/base/platform.json").is_file())
        self.assertEqual(stacks.difference("platform", "connectivity", repo).splitlines()[4:], [
            '| `hubs.norwayeast.firewall.sku_tier` | `"Standard"` | `"Premium"` |'])

    def test_the_first_hub_on_the_starter_is_a_new_stack(self):
        # The base is the platform starter, which renders no config: the base folder is still made, with its marker,
        # so the comment says the stack is new instead of nothing.
        import os
        import subprocess
        from mlz.contract import render
        from mlz.test_platform import GOVERNANCE
        repo = self.head
        shutil.copytree(STARTERS / "mlz-platform", repo)
        (repo / "_generated").mkdir()
        (repo / "_generated" / "governance.yaml").write_text(render("governance", GOVERNANCE))
        (repo / ".gitignore").write_text(".mlz/\n.mlz-core\n")
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_EVENT_NAME", "PYTHONPATH")}
        env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
        git = lambda *a: subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *a], cwd=repo, env=env, check=True, capture_output=True)
        git("init", "-q")
        git("add", "-A")
        git("commit", "-qm", "the starter")
        (repo / "connectivity" / "norwayeast.yaml").write_text("platform_address_space: 10.100.0.0/21\nlanding_zone_ranges: 10.100.0.0/16\n")
        git("commit", "-qam", "the first hub")
        (repo / ".mlz-core").symlink_to(stacks.CORE)
        done = subprocess.run([str(repo / ".mlz-core" / "tools" / "ci" / "check.sh"), "platform"], cwd=repo,
                              env={**env, "GITHUB_EVENT_NAME": "pull_request"}, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue((repo / ".mlz/base/README").is_file())
        self.assertEqual(stacks.difference("platform", "connectivity", repo), "**connectivity** is new: the base branch has no such stack.\n")

    def test_check_sh_names_every_input_the_roots_are_given(self):
        # What vars_for passes a root must be what inputs() compares, or a change to it would plan nothing.
        lib = (stacks.CORE / "tools" / "ci" / "lib.sh").read_text()
        for passed in ("$WORK/rendered", "_generated/bootstrap.yaml", "$WORK/firewall.json", "$WORK/platform.json", "$WORK/vending/$stack.json"):
            self.assertIn(passed, lib)
        # Those (platform.json twice), the stack's own name (network=) and the organization's ID, neither of which a change moves.
        self.assertEqual(len(re.findall(r"-var [a-z_]+=", lib)), 8)


if __name__ == "__main__":
    unittest.main()
