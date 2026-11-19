"""Tests for run_mlz.py: the pipeline's mlz is mlz-core's, whatever the customer's checkout holds. A checkout with a
folder mlz/ and a json.py at its top stands in for a pull request that plants them; PYTHONSAFEPATH is left unset,
as a Python older than 3.11 ignores it."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from ci import run_mlz

CORE = Path(__file__).resolve().parents[2]
RUN_MLZ = CORE / "tools" / "ci" / "run_mlz.py"
PLANTED = "the checkout's own code ran"


class RunMlzTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        self.elsewhere = Path(self.tmp.name) / "elsewhere"  # on PYTHONPATH: what -I must ignore too
        for folder in (self.repo, self.elsewhere):
            (folder / "mlz").mkdir(parents=True)
            (folder / "mlz" / "__init__.py").write_text("")
            (folder / "mlz" / "__main__.py").write_text(f"raise SystemExit({PLANTED!r})\n")
        (self.repo / "json.py").write_text(f"raise SystemExit({PLANTED!r})\n")

    def tearDown(self):
        self.tmp.cleanup()

    def run_in_checkout(self, argv: list[str], pythonpath: Path) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items() if key != "PYTHONSAFEPATH"}
        return subprocess.run(argv, cwd=self.repo, env={**env, "PYTHONPATH": str(pythonpath)}, capture_output=True, text=True)

    def assert_mlz_cores(self, done: subprocess.CompletedProcess):
        self.assertNotIn(PLANTED, done.stdout + done.stderr)
        self.assertEqual((done.returncode, done.stdout.strip()), (0, "[]"), done.stderr)  # mlz batches' answer

    def test_without_it_the_checkout_stands_in_for_mlz(self):
        # What the pipeline ran before: proves the checkout above would be picked up.
        done = self.run_in_checkout([sys.executable, "-m", "mlz", "batches", "--stacks", "[]"], CORE / "tools")
        self.assertIn(PLANTED, done.stderr)

    def test_a_steps_command_runs_mlz_cores_mlz(self):
        # python3 -I .mlz-core/tools/ci/run_mlz.py ..., in role.yml and the shell scripts.
        self.assert_mlz_cores(self.run_in_checkout([sys.executable, "-I", str(RUN_MLZ), "batches", "--stacks", "[]"], self.elsewhere))

    def test_a_scripts_command_runs_mlz_cores_mlz(self):
        # mlz(...), in tools/ci's Python scripts.
        self.assertEqual(run_mlz.mlz("batches")[:3], [sys.executable, "-I", str(RUN_MLZ)])
        self.assert_mlz_cores(self.run_in_checkout(run_mlz.mlz("batches", "--stacks", "[]"), self.elsewhere))

    def test_the_pipeline_runs_mlz_through_it_only(self):
        # Any other way of running mlz in a step or a script would take the checkout's code first again.
        files = [CORE / ".github" / "workflows" / "role.yml", CORE / ".github" / "workflows" / "workload.yml",
                 *sorted((CORE / "tools" / "ci").glob("*.sh")),
                 *[path for path in sorted((CORE / "tools" / "ci").glob("*.py")) if not path.name.startswith("test_")]]
        # A command in a step or a shell script, outside its comments; an argument list in Python, where a message that
        # tells a person what to run is no call.
        shell, python = re.compile(r"-m\s+mlz\b"), re.compile(r"\"-m\",\s*\"mlz\"|runpy")
        found = []
        for path in files:
            if path == RUN_MLZ:
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if python.search(line) if path.suffix == ".py" else shell.search(line.split("#")[0]):
                    found.append(f"{path.relative_to(CORE)}:{number}: {line.strip()}")
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
