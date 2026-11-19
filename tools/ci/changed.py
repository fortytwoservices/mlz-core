"""changed.py: the changed job's step, after every plan job: the stacks whose reviewed plan makes changes, which the
apply jobs take, one each (the platform's together, in order), and which plan job planned each, for its apply to
download only that one. Reads STACKS and BATCHES (JSON, from check), ROLE, and .mlz/reviewed/changes-<stack>.txt;
writes stacks= and batch_of= to GITHUB_OUTPUT, and a line to GITHUB_STEP_SUMMARY. Was role.yml's inline Python.
"""

from __future__ import annotations

import json
import os
import pathlib


def changes(stack: str) -> bool:
    """A missing list counts as changes: the apply then stops on it."""
    path = pathlib.Path(f".mlz/reviewed/changes-{stack}.txt")
    return not path.exists() or path.read_text().strip() != ""


def main() -> None:
    stacks = json.loads(os.environ["STACKS"])
    batch_of = {stack: batch["id"] for batch in json.loads(os.environ["BATCHES"]) for stack in batch["stacks"].split()}
    changed = [stack for stack in stacks if changes(stack)]
    unchanged = [stack for stack in stacks if stack not in changed]
    jobs = [" ".join(changed)] if os.environ["ROLE"] == "platform" and changed else changed
    with open(os.environ["GITHUB_OUTPUT"], "a") as out:
        out.write(f"stacks={json.dumps(jobs)}\n")
        out.write(f"batch_of={json.dumps(batch_of)}\n")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as out:
        out.write(f"To apply: {', '.join(changed) or 'nothing'}. No changes, so no approval: {', '.join(unchanged) or 'none'}.\n")


if __name__ == "__main__":
    main()
