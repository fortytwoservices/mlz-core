"""apply.py <role> <stack>: plans against the stack's state in the account, as the apply identity, and applies only if
the plan makes exactly the changes in the plan the reviewer saw, to the same values (.mlz/reviewed/changes-<stack>.txt,
from the plan job of the same run). Otherwise it stops: Azure or the state changed in between, and the run must start
again. Its refusals are apply_guard.py's, each with a test: no reviewed plan, a change main no longer says (stand down,
exit 0), a state another run changed, a plan that differs from the reviewed one. Exits as the apply does, 1 on a
refusal; a step that fails exits with its own code. Was apply.sh. Needs TFSTATE_STORAGE_ACCOUNT and TFSTATE_CONTAINER,
and the apply identity signed in (ARM_*).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci import apply_guard, state_version, summary  # noqa: E402
from ci.gh import ConfigError, env  # noqa: E402
from mlz import stacks  # noqa: E402


def apply(role: str, stack: str, run=subprocess.run, say=print) -> int:
    verdict = apply_guard.before(role, stack, say=say)
    if verdict == apply_guard.STAND_DOWN:
        return 0
    if verdict != apply_guard.GO:
        return 1
    work = Path.cwd() / ".mlz"
    (work / "plans").mkdir(parents=True, exist_ok=True)
    terraform = ["terraform", f"-chdir={stacks.root(role, stack)}"]
    run([*terraform, "init", "-reconfigure", "-input=false", "-no-color",
         f"-backend-config=storage_account_name={env('TFSTATE_STORAGE_ACCOUNT')}",
         f"-backend-config=container_name={env('TFSTATE_CONTAINER')}",
         f"-backend-config=key={stack}.tfstate"], check=True)
    tfplan, as_json = work / "plans" / f"{stack}.tfplan", work / "plans" / f"{stack}.json"
    run([*terraform, "plan", "-input=false", "-no-color", *stacks.terraform_vars(role, stack), f"-out={tfplan}"],
        stdout=subprocess.DEVNULL, check=True)
    # The state must still be the one the reviewed plan started from, read after this plan was made. check: a failed
    # read stops the apply.
    now = state_version.version(run([*terraform, "state", "pull"], stdout=subprocess.PIPE, text=True, check=True).stdout)
    if apply_guard.state(stack, now, say=say) != apply_guard.GO:
        return 1
    with open(as_json, "w") as out:
        run([*terraform, "show", "-json", str(tfplan)], stdout=out, check=True)
    refused = work / f"apply-summary-{stack}.md"
    if summary.main(str(as_json), stack, str(refused), str(work / f"apply-changes-{stack}.txt")) != 0:
        say("::error::The plan is refused; nothing is applied.")
        if refused.is_file():
            say(refused.read_text().rstrip("\n"))
        return 1
    if apply_guard.matches(stack, say=say) != apply_guard.GO:
        return 1
    return run([*terraform, "apply", "-input=false", "-no-color", str(tfplan)]).returncode


if __name__ == "__main__":
    try:
        sys.exit(apply(sys.argv[1], sys.argv[2]))
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode or 1)
    except ConfigError as error:
        print(f"::error::{error}", file=sys.stderr)
        sys.exit(1)
