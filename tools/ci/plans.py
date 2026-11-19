"""plans.py <role> <stack> [<stack> ...]: plans a group of stacks one after the other, in one job (tools/mlz/stacks.py's
batches), so a hundred landing zones hold a runner for a few jobs instead of queueing a hundred. For each stack, as one
job per stack did before: the plan (plan.py), its summary on the run page, its plan comment on a pull request
(comment.py), and on the daily check its status for drift.py (.mlz/status-<stack>.txt: the plan's exit code, or none
when the job stopped before planning it). Exits 1 when any plan failed or was refused, or the daily check found drift;
every stack is planned either way. Needs GITHUB_EVENT_NAME, and on a pull request what comment.py needs.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def unknown_only(changes: Path) -> int:
    """How many changes a stack's list holds when every one is an update only to values unknown until apply (summary.py's
    kind unknown), else 0: on the daily check those are no drift on their own, as they may well end unchanged."""
    lines = changes.read_text().splitlines() if changes.is_file() else []
    return len(lines) if lines and all(line.startswith("unknown ") for line in lines) else 0


def plan(role: str, stacks: list[str], say=print) -> int:
    core, work = Path.cwd() / ".mlz-core", Path.cwd() / ".mlz"  # mlz-core's checkout, and what the run works out
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    failed = []
    for stack in stacks:
        status = subprocess.run([sys.executable, str(core / "tools/ci/plan.py"), role, stack]).returncode
        # plan.py's own codes are 0, 2 and 3, each with a summary. Anything else, or one without its summary, stopped early
        # (a failed sign-in, say): a failed plan, never drift, and never a reason to skip the stacks after it.
        summary = work / f"summary-{stack}.md"
        if status not in (0, 2, 3) or not summary.is_file():
            status = 1
        maybe = unknown_only(work / f"changes-{stack}.txt") if status == 2 and event == "schedule" else 0
        if maybe:
            say(f"::notice::{stack}: {maybe} update(s) only to values unknown until apply, which may end unchanged: no drift on their own.")
            with open(os.environ.get("GITHUB_STEP_SUMMARY") or os.devnull, "a") as page:
                page.write(f"{stack}: {maybe} update(s) only to values unknown until apply, which may end unchanged; not counted as drift.\n\n")
            status = 0
        if event == "schedule":
            (work / f"status-{stack}.txt").write_text(f"{status}\n")
        if status != 1:
            with open(os.environ.get("GITHUB_STEP_SUMMARY") or os.devnull, "a") as page:
                page.write(summary.read_text())
        sys.stdout.flush()
        if event == "pull_request":
            commented = subprocess.run([sys.executable, str(core / "tools/ci/comment.py"), stack, str(status), role]).returncode
            if commented:
                say(f"::warning::{stack}: its plan could not be put on the pull request; the run page has it.")
        if status == 1:
            say(f"::error::{stack}: the plan failed.")
            failed.append(stack)
        elif status == 3:
            say(f"::error::{stack}: the plan is refused: it deletes or replaces a firewall policy, and every rule in it would go too. Its plan says which.")
            failed.append(stack)
        elif status == 2 and event == "schedule":
            say(f"::error::{stack}: drift: Azure differs from main. Its plan shows how.")
            failed.append(stack)
    if failed:
        say(f"Planned {len(stacks)} stack(s); not as they should be: {' '.join(failed)}.")
        return 1
    say(f"Planned {len(stacks)} stack(s).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    sys.exit(plan(sys.argv[1], sys.argv[2:], say=lambda text: print(text, flush=True)))
