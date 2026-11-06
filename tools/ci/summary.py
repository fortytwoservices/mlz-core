"""A plan, as people review it and as the apply checks it.

    python3 summary.py <plan.json> <stack> <summary.md> <changes.txt>

<plan.json> is `terraform show -json` of a saved plan. Writes:

- <summary.md>: the counts, the changes by resource type, and every change to or removal of an existing
  resource by name (new resources by type only). Short enough for a pull request comment.
- <changes.txt>: one line per change, `<kind> <address> <fingerprint>`, sorted, outputs as `output.<name>`.
  The fingerprint covers the change's actions in order and its planned values (known and unknown), so the
  apply, which compares its own plan with the reviewed one line by line and stops if they differ, also
  stops when the same resource would get different values from what the reviewer saw. A stack whose list
  is empty does not apply. Outputs count: a contract that changes on its own (a new mlz-core release) must
  still be applied to the state, which is where the contract is published from.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict

KIND = {
    ("create",): "add",
    ("update",): "change",
    ("delete",): "destroy",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
}
LISTED = 200  # changes to existing resources shown by name


def resource_type(address: str) -> str:
    """The resource's type, after the module it is in, without keys:
    module.alz.azapi_resource.policy_assignments["mlz/Deny-Classic-Resources"] -> alz: azapi_resource.policy_assignments"""
    modules = re.findall(r'module\.([^.\[]+)(?:\[[^\]]*\])?\.', address)
    without_modules = re.sub(r'^(module\.[^.\[]+(\[[^\]]*\])?\.)+', "", address)
    name = re.sub(r"\[.*\]$", "", without_modules)
    return f"{modules[-1]}: {name}" if modules else name


def fingerprint(change: dict) -> str:
    """What the change does, values included, in a few characters. Sensitive values only ever leave as
    part of the hash."""
    what = {key: change.get(key) for key in ("actions", "after", "after_unknown")}
    return hashlib.sha256(json.dumps(what, sort_keys=True).encode()).hexdigest()[:16]


def main(plan_file: str, stack: str, summary_file: str, changes_file: str) -> None:
    plan = json.load(open(plan_file))
    changes, prints = [], {}
    for change in plan.get("resource_changes", []):
        kind = KIND.get(tuple(change["change"]["actions"]))
        if kind:
            changes.append((kind, change["address"]))
            prints[change["address"]] = fingerprint(change["change"])
    outputs = []
    for name, change in plan.get("output_changes", {}).items():
        kind = KIND.get(tuple(change["actions"]))
        if kind:
            outputs.append((kind, f"output.{name}"))
            prints[f"output.{name}"] = fingerprint(change)
    changes.sort(key=lambda item: (item[1], item[0]))
    outputs.sort(key=lambda item: item[1])

    with open(changes_file, "w") as out:
        out.writelines(f"{kind} {address} {prints[address]}\n" for kind, address in changes + outputs)

    counts = Counter(kind for kind, _ in changes)
    by_type: dict[str, Counter] = defaultdict(Counter)
    for kind, address in changes:
        by_type[resource_type(address)][kind] += 1
    existing = [(kind, address) for kind, address in changes if kind != "add"]

    lines = []
    if not changes and not outputs:
        lines.append(f"**{stack}**: no changes.")
    elif not changes:
        lines.append(f"**{stack}**: no resource changes.")
    else:
        headline = f"{counts['add']} to add, {counts['change']} to change, {counts['destroy']} to destroy"
        if counts["replace"]:
            headline += f", {counts['replace']} to replace"
        lines += [f"**{stack}**: {headline}.", "", "| Resource type | Add | Change | Replace | Destroy |", "|---|---:|---:|---:|---:|"]
        for name in sorted(by_type):
            c = by_type[name]
            lines.append(f"| `{name}` | {c['add'] or ''} | {c['change'] or ''} | {c['replace'] or ''} | {c['destroy'] or ''} |")
        if existing:
            lines += ["", "**Changes to existing resources:**", ""]
            lines += [f"- {kind}: `{address}`" for kind, address in existing[:LISTED]]
            if len(existing) > LISTED:
                lines.append(f"- and {len(existing) - LISTED} more; the full plan is attached to the run.")
    if outputs:
        lines += ["", "**Outputs that change:** " + ", ".join(f"`{address[len('output.'):]}`" for _, address in outputs)]
    with open(summary_file, "w") as out:
        out.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main(*sys.argv[1:])
