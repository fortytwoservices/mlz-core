"""Tests for the order of a step's log (finding 19): a script's own lines reach the job's log before what its
subprocesses write there. In Actions stdout is a pipe, which Python buffers in blocks, while a subprocess such as
terraform writes to the same file descriptor straight away: without line buffering, apply.py's checks were logged after
Terraform's "Apply complete!", though they ran before it."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CI = Path(__file__).resolve().parent

# terraform as apply.py calls it, writing to the job's log (fd 1) as the real one does: an empty plan, an empty state.
FAKE_TERRAFORM = """#!/usr/bin/env bash
case $2 in
  init) echo "Terraform has been successfully initialized!" ;;
  plan) for a in "$@"; do [[ $a == -out=* ]] && : > "${a#-out=}"; done; echo "No changes." ;;
  state) ;;
  show) echo '{"resource_changes": []}' ;;
  apply) echo "Apply complete! Resources: 0 added, 0 changed, 0 destroyed." ;;
esac
"""


class LogOrderTest(unittest.TestCase):
    def test_apply_s_checks_come_before_terraform_s_apply_in_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, bin_ = Path(tmp) / "repo", Path(tmp) / "bin"
            (repo / ".mlz" / "reviewed").mkdir(parents=True)
            (repo / ".mlz" / "reviewed" / "changes-workload.txt").write_text("")
            (repo / ".mlz" / "reviewed" / "version-workload.txt").write_text("none\n")
            bin_.mkdir()
            (bin_ / "terraform").write_text(FAKE_TERRAFORM)
            (bin_ / "terraform").chmod(0o755)
            env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TFSTATE_STORAGE_ACCOUNT": "stacme", "TFSTATE_CONTAINER": "tfstate"}
            # stdout a pipe, as in Actions; stderr joined to it, as the job's log is one stream.
            done = subprocess.run([sys.executable, str(CI / "apply.py"), "workload", "workload"], cwd=repo, env=env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log = done.stdout.splitlines()
        self.assertEqual(done.returncode, 0, done.stdout)
        order = [line for line in log if line.startswith(("Terraform has been", "The state is", "The plan matches", "Apply complete"))]
        self.assertEqual(order, ["Terraform has been successfully initialized!", "The state is the one the reviewed plan started from: none.",
                                 "The plan matches the reviewed one: 0 change(s).", "Apply complete! Resources: 0 added, 0 changed, 0 destroyed."])

    def test_every_script_buffers_its_log_by_line(self):
        # Any script the pipeline runs may start a subprocess after printing: each line goes out before it does.
        missing = [path.name for path in sorted(CI.glob("*.py"))
                   if not path.name.startswith("test_") and 'if __name__ == "__main__":' in (text := path.read_text())
                   and not re.search(r'if __name__ == "__main__":\n    sys\.stdout\.reconfigure\(line_buffering=True\)', text)]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
