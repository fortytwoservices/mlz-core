"""apply_guard.py: apply.py's refusals, each a check it makes before it applies, so that an apply never does what the
reviewer did not see; apply.py is the Terraform around them. Each returns GO, REFUSE or, from before alone, STAND_DOWN
(nothing applied, no failure):

    before(role, stack)    before planning: a reviewed plan of this run, and for vending a change main still says
    state(stack, version)  the state's version now must be the one the reviewed plan started from
    matches(stack)         this plan's changes must be the reviewed plan's

Reads .mlz/reviewed/ (the plan job's files of this run), .mlz/vending/ and, for vending, MLZ_MAIN and GITHUB_SHA.
"""

from __future__ import annotations

import difflib
import filecmp
import os
import subprocess
from pathlib import Path

from ci.run_mlz import mlz  # tools/ is on the path: apply.py puts it there

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
        say(f"The state is the one the reviewed plan started from: {reviewed}.")
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

