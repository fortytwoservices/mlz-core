"""drift.py: after the daily check's plans, one issue in the repo says what is not as main says. It is opened, or
commented on, while the check fails or any stack's plan differs from main or did not finish, and closed by the first
daily check that finds every stack as main says. Reads CHECK (the check job's result) and, after a check that passed,
.mlz/status-<stack>.txt (plan.py's exit status, from each plan job) for the stacks in STACKS (JSON, from check); a stack
without one never got to plan. Needs GH_TOKEN with issues: write, GH_REPO, RUN_URL and ROLE.

The issue is read and written with gh's own issue commands, as drift.sh did.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

TITLE = "Daily check: Azure differs from main"


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=True).stdout


def report() -> int:
    drifted: list[str] = []
    unplanned: list[str] = []
    check = os.environ.get("CHECK") or ""
    stacks = json.loads(os.environ.get("STACKS") or "[]")
    if check != "success":
        # No stacks to go by: the YAML, the contracts or GitHub's domain list stopped the check, and nothing was planned.
        unplanned.append(f"every stack: the check failed (`{check or 'unknown'}`), so nothing was planned")
        stacks = []
    for stack in stacks:
        path = Path(f".mlz/status-{stack}.txt")
        status = path.read_text().strip() if path.is_file() else "none"
        if status == "2":
            drifted.append(f"`{stack}`")
        elif status == "3":
            unplanned.append(f"`{stack}`, refused: it deletes or replaces a firewall policy")
        elif status != "0":
            unplanned.append(f"`{stack}`, the plan did not finish")

    # Only an issue with exactly this title, opened by this job: a person's issue that mentions it is left alone.
    found = gh("issue", "list", "--state", "open", "--author", "app/github-actions", "--search", f'in:title "{TITLE}"',
               "--json", "number,title", "--jq", f'map(select(.title == "{TITLE}")) | .[0].number // empty').strip()
    today = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    run = os.environ["RUN_URL"]

    if not drifted and not unplanned:
        if found:
            gh("issue", "close", found, "--comment", f"The daily check of {today} found every stack as main says: {run}")
        return 0

    lines = [f"The daily check of {today} found:", ""]
    if drifted:
        lines.append(f"- **Azure differs from main:** {', '.join(drifted)}")
    lines += [f"- **No plan:** {stack}" for stack in unplanned]
    lines += ["", f"The plans and their summaries are on the run page: {run}", "",
              "To bring Azure back to main, run the workflow on main and approve the apply. To keep what Azure has,",
              "change main to match."]
    if os.environ.get("ROLE") == "platform":
        lines += ["A change in GitHub's own list of domains, which the private runners reach, shows here too, in the",
                  "firewall-rules stack."]
    lines += ["", "This issue closes itself when a daily check finds every stack as main says."]
    body = Path(os.environ.get("RUNNER_TEMP") or "/tmp") / "drift.md"
    body.write_text("\n".join(lines) + "\n")

    if found:
        gh("issue", "comment", found, "--body-file", str(body))
    else:
        gh("issue", "create", "--title", TITLE, "--body-file", str(body))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(report())
    except subprocess.CalledProcessError as error:
        print(f"::error::{' '.join(error.cmd[:3])}: {error.stderr.strip() if error.stderr else error}", file=sys.stderr)
        sys.exit(1)
