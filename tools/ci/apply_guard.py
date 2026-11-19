"""apply_guard.py: apply.sh's refusals, each a check it makes before it applies, so that an apply never does what the
reviewer did not see. apply.sh stays the Terraform around them.

    apply_guard.py before <role> <stack>     exit 0 to go on, 1 to refuse, 2 to stand down (nothing applied, no failure)
    apply_guard.py state <stack> <version>   the state's version now; exit 1 when it is not the one the plan started from
    apply_guard.py matches <stack>           exit 1 when this plan's changes are not the reviewed plan's

Reads .mlz/reviewed/ (the plan job's files of this run), .mlz/vending/ and, for vending, MLZ_MAIN and GITHUB_SHA.
"""

from __future__ import annotations

import difflib
import filecmp
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tools/: run as a script, as role.yml does

from ci.run_mlz import mlz  # noqa: E402

GO, REFUSE, STAND_DOWN = 0, 1, 2


def work() -> Path:
    return Path.cwd() / ".mlz"


def before(role: str, stack: str, say=print) -> int:
    if not (work() / "reviewed" / f"changes-{stack}.txt").is_file():
        say(f"::error::No reviewed plan for {stack} in this run; nothing is applied.")
        return REFUSE
    # When main has moved on while this run waited for its reviewer, and its latest commit renders this landing zone
    # otherwise than this run did, a newer change, or one taken back, supersedes this one: that commit's run, queued
    # behind this one, plans it and applies what is left to change. So this one stands down rather than apply what main
    # no longer says. MLZ_MAIN is a checkout of main's latest commit (role.yml).
    main = os.environ.get("MLZ_MAIN", "")
    if role != "vending" or not main or not Path(main).is_dir():
        return GO
    if subprocess.run(["git", "-C", main, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() == os.environ.get("GITHUB_SHA", ""):
        return GO
    latest = work() / "main-vending"
    with open(work() / "main-vending.log", "w") as log:
        rendered = subprocess.run(mlz("vending", "--repo", main, "--out", str(latest)), stdout=log, stderr=subprocess.STDOUT).returncode == 0
    ours, theirs = work() / "vending" / f"{stack}.json", latest / f"{stack}.json"
    if rendered and ours.is_file() and theirs.is_file() and filecmp.cmp(ours, theirs, shallow=False):
        return GO
    say(f"::notice::main has moved on, and its latest commit changes {stack} (or no longer checks with this release): that commit's run "
        "plans it and applies what is left to change. Nothing is applied here.")
    return STAND_DOWN


def state(stack: str, now: str, say=print) -> int:
    """The state must still be the one the reviewed plan started from, read after this plan was made: a change before or
    during it shows here, and Terraform refuses to apply a saved plan whose state changed after it. An apply made in
    between (by hand, say) would otherwise let this plan's values, whose fingerprints may still match, undo it."""
    recorded = work() / "reviewed" / f"version-{stack}.txt"
    reviewed = recorded.read_text().strip() if recorded.is_file() else "not recorded"
    if now.strip() == reviewed:
        return GO
    say(f"::error::{stack}'s state changed after this plan was reviewed (it was at {reviewed}, it is at {now.strip()}): another run applied it. "
        "Nothing is applied. Start a new run on main for a fresh plan (gh workflow run mlz.yml --ref main).")
    return REFUSE


def matches(stack: str, say=print) -> int:
    reviewed = work() / "reviewed" / f"changes-{stack}.txt"
    planned = work() / f"apply-changes-{stack}.txt"
    expected, found = reviewed.read_text().splitlines(True), planned.read_text().splitlines(True)
    if expected != found:
        difference = "".join(difflib.unified_diff(expected, found, str(reviewed), str(planned)))
        (work() / f"difference-{stack}.txt").write_text(difference)
        # Re-running this run's jobs compares with the same reviewed plan, so after an apply that failed half way it
        # always fails again: only a new run plans afresh.
        say("::error::The plan differs from the one the reviewer saw; nothing is applied. Start a new run on main for a fresh plan "
            "(Actions, mlz, Run workflow; or gh workflow run mlz.yml --ref main): re-running this run's jobs would compare with the same "
            "reviewed plan again.")
        say(difference.rstrip("\n"))
        return REFUSE
    say(f"The plan matches the reviewed one: {len(expected)} change(s).")
    return GO


def main(args: list[str]) -> int:
    check = args[0]
    if check == "before":
        return before(args[1], args[2])
    if check == "state":
        return state(args[1], args[2])
    if check == "matches":
        return matches(args[1])
    print(f"apply_guard.py: no check {check!r}", file=sys.stderr)
    return REFUSE


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
