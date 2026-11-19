"""plan.py <role> <stack>: a read-only plan. The plan identity can only read the state, and the backend writes an empty
state for a key it has not seen, so this plans against a downloaded copy of the state with a local backend, in the
stack's root (tools/mlz/stacks.py). Writes, in .mlz/: plan-<stack>.txt (the full plan), summary-<stack>.md (for
people), changes-<stack>.txt (what the apply must match) and version-<stack>.txt (the state it started from). Exits 0
for no changes, 2 for changes, 3 when summary.py refuses the plan (the summary says why), and 1 when the plan fails;
a step that fails before planning exits with its own code. Was plan.sh. Needs TFSTATE_STORAGE_ACCOUNT and
TFSTATE_CONTAINER, and az signed in.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as plans.py does

from ci import state_version, summary  # noqa: E402
from ci.gh import ConfigError, env  # noqa: E402
from mlz import stacks  # noqa: E402


def plan(role: str, stack: str, run=subprocess.run) -> int:
    work = Path.cwd() / ".mlz"
    root = stacks.root(role, stack)
    key = f"{stack}.tfstate"
    copy = work / "state" / key
    (work / "state").mkdir(parents=True, exist_ok=True)
    (work / "plans").mkdir(parents=True, exist_ok=True)
    blob = ["--auth-mode", "login", "--account-name", env("TFSTATE_STORAGE_ACCOUNT"), "--container-name", env("TFSTATE_CONTAINER"),
            "--name", key, "--only-show-errors"]
    # check: a failed lookup stops the plan instead of planning every resource as new.
    exists = run(["az", "storage", "blob", "exists", *blob, "--query", "exists", "-o", "tsv"], stdout=subprocess.PIPE, text=True, check=True)
    if exists.stdout.strip() == "true":
        run(["az", "storage", "blob", "download", *blob, "--file", str(copy)], stdout=subprocess.DEVNULL, check=True)
    # The state this plan starts from, for the apply to refuse one another run has changed since (apply_guard.py).
    (work / f"version-{stack}.txt").write_text((state_version.version(copy.read_text()) if copy.is_file() else "none") + "\n")
    (root / "backend_override.tf").write_text(f'terraform {{\n  backend "local" {{\n    path = "{copy}"\n  }}\n}}\n')
    terraform = ["terraform", f"-chdir={root}"]
    run([*terraform, "init", "-reconfigure", "-input=false", "-no-color"], stdout=subprocess.DEVNULL, check=True)
    tfplan, as_json = work / "plans" / f"{stack}.tfplan", work / "plans" / f"{stack}.json"
    status = run([*terraform, "plan", "-input=false", "-no-color", "-lock=false", "-detailed-exitcode",
                  *stacks.terraform_vars(role, stack), f"-out={tfplan}"]).returncode
    if status == 1:
        return 1
    with open(work / f"plan-{stack}.txt", "w") as out:
        run([*terraform, "show", "-no-color", str(tfplan)], stdout=out, check=True)
    with open(as_json, "w") as out:
        run([*terraform, "show", "-json", str(tfplan)], stdout=out, check=True)
    checked = summary.main(str(as_json), stack, str(work / f"summary-{stack}.md"), str(work / f"changes-{stack}.txt"))
    as_json.unlink()
    tfplan.unlink()
    if checked == 3:
        return 3
    return status if checked == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(plan(sys.argv[1], sys.argv[2]))
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode or 1)
    except ConfigError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
