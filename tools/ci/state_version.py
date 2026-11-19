"""A Terraform state's version, so that an apply refuses a state another run changed after its plan was reviewed.

    python3 state_version.py [<state file>]       (stdin without one: terraform state pull | ...)

Prints "<lineage> <serial>", or "none" for no state, or one with neither resources nor outputs: a backend may write
an empty state for a key it has not seen, which must count as the "none" a plan of a new stack recorded. A state with
outputs alone is a real one (a root may hold only outputs), so its serial counts.
"""

from __future__ import annotations

import json
import sys


def version(text: str) -> str:
    if not text.strip():
        return "none"
    state = json.loads(text)
    if not state.get("resources") and not state.get("outputs"):
        return "none"
    return f"{state.get('lineage')} {state.get('serial')}"


if __name__ == "__main__":
    print(version(open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()))
