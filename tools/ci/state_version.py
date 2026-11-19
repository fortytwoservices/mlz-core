"""A Terraform state's version, so that an apply refuses a state another run changed after its plan was reviewed.

    python3 state_version.py [<state file>]       (stdin without one: terraform state pull | ...)

Prints "<lineage> <serial>", or "none" for no state, or an empty one that was never applied (no resources, no outputs,
serial 0 or 1): a backend may write such a state for a key it has not seen, which must count as the "none" a plan of
a new stack recorded. A state with outputs alone is a real one (a root may hold only outputs), and so is an emptied one
(its last resource destroyed, its serial moved on): both keep their lineage and serial. Not verified: the serial a
backend gives its first empty state; were it higher, a new stack's first apply would stop, and a new run plans it.
"""

from __future__ import annotations

import json
import sys


def version(text: str) -> str:
    if not text.strip():
        return "none"
    state = json.loads(text)
    if not state.get("resources") and not state.get("outputs") and int(state.get("serial") or 0) <= 1:
        return "none"
    return f"{state.get('lineage')} {state.get('serial')}"


if __name__ == "__main__":
    print(version(open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()))
