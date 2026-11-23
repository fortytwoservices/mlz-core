"""Tests for mlz check (tools/mlz/__main__.py): every check a repo's pipeline makes before it plans, which
tools/ci/check.py runs and a person runs with ./mlz check."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from mlz import inputs, upgrade
from mlz.__main__ import main

STARTERS = inputs.CORE / "starter"


class CheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, repo: Path, *flags: str, actions: bool = False) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), mock.patch.dict("os.environ", {"GITHUB_ACTIONS": "true" if actions else ""}):
            code = main(["check", "--repo", str(repo), *flags])
        return code, out.getvalue(), err.getvalue()

    def starter(self, role: str) -> Path:
        repo = self.dir / f"mlz-{role}"
        shutil.copytree(STARTERS / f"mlz-{role}", repo)
        return repo

    def test_the_governance_starter_renders_and_plans_governance(self):
        code, out, err = self.check(self.starter("governance"), "--out", str(self.dir / "work"), "--json")
        self.assertEqual((code, json.loads(out)), (0, ["governance"]))
        self.assertTrue((self.dir / "work" / "rendered" / "config.json").is_file())
        self.assertIn("mlz governance: wrote the library for demo", err)  # every step's own line goes to stderr

    def test_the_platform_starter_waits_for_its_first_hub(self):
        code, out, err = self.check(self.starter("platform"), "--json", actions=True)
        self.assertEqual((code, json.loads(out)), (0, []))
        self.assertIn("::notice::No connectivity file has a setting yet", err)

    def test_the_vending_starter_waits_for_the_contracts(self):
        code, out, err = self.check(self.starter("vending"))
        self.assertEqual(code, 0)
        self.assertEqual(out, "mlz check: mlz-vending checks; its stacks: none yet\n")
        self.assertIn("mlz check: No governance contract yet (_generated/governance.yaml)", err)  # no annotation off GitHub

    def test_a_platform_with_its_hub_and_vending_s_contract_plans_every_stack_in_order(self):
        from mlz.contract import render
        from mlz.test_firewall import GITHUB, VENDING
        from mlz.test_platform import GOVERNANCE
        repo = self.starter("platform")
        (repo / "_generated").mkdir()
        (repo / "_generated" / "governance.yaml").write_text(render("governance", GOVERNANCE))
        # The hub's own space clear of the landing zones' ranges in VENDING (10.100.4.0/24, 10.100.5.0/24).
        (repo / "connectivity" / "norwayeast.yaml").write_text("platform_address_space: 10.100.16.0/21\nlanding_zone_ranges: 10.100.0.0/16\n")
        meta = self.dir / "meta.json"  # a saved api.github.com/meta: no fetch
        meta.write_text(json.dumps({"domains": {"actions": GITHUB}}))
        code, out, err = self.check(repo, "--json", "--github-meta", str(meta))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), ["connectivity", "management", "identity"])  # the rules wait for vending
        (repo / "_generated" / "vending.yaml").write_text(render("vending", VENDING))
        code, out, err = self.check(repo, "--json", "--github-meta", str(meta), "--out", str(self.dir / "work"))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out), ["connectivity", "management", "identity", "firewall-rules"])
        self.assertTrue((self.dir / "work" / "firewall.json").is_file())

    def test_code_owners_other_than_the_installer_s_fail_every_role_s_check(self):
        from mlz import owners
        repo = self.starter("vending")
        (repo / ".github" / "CODEOWNERS").write_text(owners.render("acme", "platform"))
        self.assertEqual(self.check(repo, "--json")[0], 0)
        # A handover line, as releases before 19 November allowed (decision D8 took it out).
        (repo / ".github" / "CODEOWNERS").write_text(owners.render("acme", "platform") + "/landing-zones/payments/ @acme/payments\n")
        code, out, err = self.check(repo, "--json", actions=True)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("::error::.github/CODEOWNERS is not what mlz install repos writes", err)
        self.assertIn("Remove `/landing-zones/payments/ @acme/payments`.", err)

    def test_in_the_governance_repo_the_owner_is_bootstrap_yaml_s_platform_team(self):
        from mlz import owners
        repo = self.starter("governance")  # its bootstrap.yaml: demo-org, platform
        (repo / ".github" / "CODEOWNERS").write_text(owners.render("demo-org", "platform"))
        self.assertEqual(self.check(repo, "--json")[0], 0)
        (repo / ".github" / "CODEOWNERS").write_text(owners.render("demo-org", "network"))
        code, _, err = self.check(repo)
        self.assertEqual(code, 1)
        self.assertIn("names @demo-org/network as the owner, but bootstrap.yaml's platform team is @demo-org/platform", err)

    def test_a_bootstrap_yaml_mlz_cannot_read_fails_the_check_and_says_why(self):
        repo = self.starter("governance")
        (repo / "bootstrap.yaml").write_text("github: [not, a, mapping]\n")
        code, out, err = self.check(repo, "--json")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("mlz check: bootstrap.yaml: github is not a mapping of the organization's settings", err)

    def test_a_governance_repo_with_customer_yaml_is_refused_with_the_new_name_and_the_command(self):
        # governance.yaml's name before v0.16.0, refused before anything renders (no compatibility code, decision D5).
        repo = self.starter("governance")
        (repo / "governance.yaml").rename(repo / "customer.yaml")
        code, out, err = self.check(repo, "--json", actions=True)
        self.assertEqual((code, out), (1, ""))
        self.assertIn(f"::error::{repo / 'customer.yaml'}: this file is called governance.yaml now; rename it, in the governance repo and in "
                      "the folder mlz install reads: git mv customer.yaml governance.yaml (docs/governance.md).", err)
        self.assertNotIn("mlz governance:", err)

    def test_a_failed_check_fails_and_prints_no_stacks(self):
        repo = self.starter("governance")
        (repo / "governance.yaml").write_text((repo / "governance.yaml").read_text() + "\nroot_id: NOT VALID\n")
        code, out, err = self.check(repo, "--json")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("mlz governance:", err)

    def test_without_out_nothing_is_left_behind(self):
        repo = self.starter("governance")
        before = sorted(p.name for p in repo.rglob("*"))
        self.assertEqual(self.check(repo)[0], 0)
        self.assertEqual(sorted(p.name for p in repo.rglob("*")), before)

    def test_a_folder_without_the_workflow_is_named(self):
        with self.assertRaises(SystemExit) as caught:
            self.check(self.dir)
        self.assertIn("mlz.yml is not there; run mlz in a governance, platform or vending repo", str(caught.exception))


class WrapperTest(unittest.TestCase):
    """./mlz, which the starters carry and mlz upgrade replaces: one file, run as mlz-core's command line at the release
    the repo's workflow calls, from the copy it names."""

    def test_the_starters_carry_one_executable_wrapper(self):
        paths = [STARTERS / f"mlz-{role}" / upgrade.WRAPPER for role in ("governance", "platform", "vending")]
        self.assertEqual(len({p.read_text() for p in paths}), 1)
        self.assertTrue(all(os.access(p, os.X_OK) for p in paths))
        tracked = subprocess.run(["git", "-C", str(inputs.CORE), "ls-files", "-s", "--", *[str(p) for p in paths]], capture_output=True, text=True).stdout
        self.assertEqual([line.split()[0] for line in tracked.splitlines()], ["100755"] * 3)  # the mode the starters give

    def test_it_runs_the_release_the_workflow_calls_from_the_copy_it_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, cache = Path(tmp) / "mlz-governance", Path(tmp) / "cache"
            shutil.copytree(STARTERS / "mlz-governance", repo)
            workflow = repo / upgrade.WORKFLOW
            workflow.write_text(upgrade.PIN.sub("uses: acme/mlz-core/.github/workflows/role.yml@v9.8.7", workflow.read_text()))
            # That release, as the wrapper leaves it once fetched: this checkout's tools.
            (cache / "mlz-core" / "acme" / "mlz-core").mkdir(parents=True)
            (cache / "mlz-core" / "acme" / "mlz-core" / "v9.8.7").symlink_to(inputs.CORE)
            # Neither a file in the repo nor the environment can stand in for the release.
            (repo / "mlz.py").write_text("raise SystemExit('the repo\\'s own mlz.py ran')\n")
            (Path(tmp) / "elsewhere" / "mlz").mkdir(parents=True)
            (Path(tmp) / "elsewhere" / "mlz" / "__main__.py").write_text("raise SystemExit('PYTHONPATH\\'s mlz ran')\n")
            run = subprocess.run([str(repo / upgrade.WRAPPER), "check", "--json"], cwd=repo, capture_output=True, text=True,
                                 env={**os.environ, "XDG_CACHE_HOME": str(cache), "PYTHONPATH": str(Path(tmp) / "elsewhere")})
            self.assertEqual((run.returncode, run.stdout), (0, '["governance"]\n'), run.stderr)

    def test_every_python_it_starts_is_isolated(self):
        # Even before it is in the repo, while it fetches: a sitecustomize.py or PYTHONPATH must not run in its place.
        text = (STARTERS / "mlz-governance" / upgrade.WRAPPER).read_text()
        calls = [line for line in text.splitlines() if re.search(r"\bpython3 ", line) and not line.lstrip().startswith("#") and "command -v" not in line]
        self.assertEqual(len(calls), 2, calls)  # the rename into the cache, and mlz itself
        self.assertTrue(all("python3 -I " in line for line in calls), calls)

    def test_without_the_workflow_it_says_where_to_run_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(STARTERS / "mlz-governance" / upgrade.WRAPPER, tmp)
            run = subprocess.run([str(Path(tmp) / upgrade.WRAPPER), "map"], capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertIn("is not there; ./mlz runs in a governance, platform or vending repo.", run.stderr)


if __name__ == "__main__":
    unittest.main()
