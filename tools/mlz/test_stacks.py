"""Tests for the stack table and mlz map: every stack the pipeline plans is in the table, and every settings file of
the starters maps to exactly one stack."""

from __future__ import annotations

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
            self.assertRegex(stacks.render(repo), r"\n  hubs/norwayeast\.yaml +-> NOT READ by any stack\n")  # a folder from before L10


if __name__ == "__main__":
    unittest.main()
