"""statuses.py: for the drift job, what the daily check's plan of each stack of the group found (plans.py writes it), or
none when the job stopped before planning it. Always run, so a re-run's never leaves an earlier attempt's in place.
Reads STACKS (the group's stacks, space-separated); writes .mlz/status-<stack>.txt. Was role.yml's inline loop.
"""

from __future__ import annotations

import os
from pathlib import Path


def main() -> None:
    work = Path(".mlz")
    work.mkdir(exist_ok=True)
    for stack in os.environ.get("STACKS", "").split():
        status = work / f"status-{stack}.txt"
        if not status.is_file():
            status.write_text("none\n")


if __name__ == "__main__":
    main()
