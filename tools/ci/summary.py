"""A plan, as people review it and as the apply checks it.

    python3 summary.py <plan.json> <stack> <summary.md> <changes.txt>

<plan.json> is `terraform show -json` of a saved plan. Exits 3 when it refuses the plan: a plan that deletes
or replaces a firewall policy, whose rule collection groups would go with it (REFUSED, below). Exits 1 when the plan
errored, or when Terraform's own `applyable` disagrees with the list below being empty: a kind of change this file does
not know yet, which must never be skipped or applied by guess. Writes:

- <summary.md>: the counts, the changes by resource type, and every change to or removal of an existing
  resource by name (new resources by type only). Short enough for a pull request comment.
- <changes.txt>: one line per change, `<kind> <address> <fingerprint>`, sorted, outputs as `output.<name>`.
  Whether a stack has changes is only ever this list being empty or not: never Terraform's exit code
  (-detailed-exitcode said 2 for an import, a move or a forget alone, which this list then missed, so they were never
  applied and showed as drift every day), and never Terraform's text. Kinds: add, change, replace, destroy; unknown,
  an update whose every difference is a value unknown until apply (planned, applied, but no drift on its own, as it
  may well end unchanged); import, move and forget, which change only the state, and must be applied to be recorded;
  deferred, when Terraform says the plan is not complete and another run is needed.
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
    ("forget",): "forget",  # a removed block: the object leaves the state and stays in Azure (not in json-format's list)
}
STATE_ONLY = ("import", "move", "forget")
LISTED = 200  # changes to existing resources shown by name
REFUSED = 3  # exit code: the plan does something the pipeline never applies

# A firewall policy holds the rules, which are applied by a stack of their own, planned before the hub
# applies. Deleting or replacing the policy would delete every rule in it while the rules plan says
# nothing changes, so the firewall would deny traffic until the rules are written again. In mlz-core the
# policy is only replaced when its name, resource group or region changes; a release that does that ships
# its own migration step.
FIREWALL_POLICY = re.compile(r"^microsoft\.network/firewallpolicies@", re.IGNORECASE)


def resource_type(address: str) -> str:
    """The resource's type, after the module it is in, without keys:
    module.alz.azapi_resource.policy_assignments["mlz/Deny-Classic-Resources"] -> alz: azapi_resource.policy_assignments"""
    modules = re.findall(r'module\.([^.\[]+)(?:\[[^\]]*\])?\.', address)
    without_modules = re.sub(r'^(module\.[^.\[]+(\[[^\]]*\])?\.)+', "", address)
    name = re.sub(r"\[.*\]$", "", without_modules)
    return f"{modules[-1]}: {name}" if modules else name


# What a plan and its apply may read differently, so an update's fingerprint leaves it out. GitHub returns a repo's
# merge settings only to a token with Administration: write: vending's plan reads them as unset, its apply as they
# are. Vending ignores them once a workload repo is made (vending/terraform/main.tf), but an update for another
# reason still carries them in its planned values. A repo that is made still has them counted.
UNREAD_ON_UPDATE = {
    "github_repository": {
        "allow_merge_commit", "allow_rebase_merge", "allow_squash_merge", "delete_branch_on_merge",
        "merge_commit_message", "merge_commit_title", "squash_merge_commit_message", "squash_merge_commit_title",
    },
}


def fingerprint(change: dict, resource: str | None = None) -> str:
    """What the change does, values included, in a few characters. Sensitive values only ever leave as
    part of the hash. The ID of the resource it starts from counts too: a deletion has no planned values,
    and must not match the deletion of another resource at the same address."""
    what = {key: change.get(key) for key in ("actions", "after", "after_unknown")}
    unread = UNREAD_ON_UPDATE.get(resource or "", set()) if change.get("actions") == ["update"] else set()
    for key in ("after", "after_unknown"):
        if unread and isinstance(what[key], dict):
            what[key] = {name: value for name, value in what[key].items() if name not in unread}
    before = change.get("before")
    what["before_id"] = before.get("id") if isinstance(before, dict) else None
    return hashlib.sha256(json.dumps(what, sort_keys=True).encode()).hexdigest()[:16]


def known_difference(before, after, unknown) -> bool:
    """Whether a value differs from before to after anywhere its after is known (after_unknown marks the rest)."""
    if unknown is True:
        return False
    if isinstance(before, dict) and isinstance(after, dict):
        unknown = unknown if isinstance(unknown, dict) else {}
        return any(known_difference(before.get(key), after.get(key), unknown.get(key)) for key in set(before) | set(after))
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        unknown = unknown if isinstance(unknown, list) else [None] * len(after)
        return any(known_difference(b, a, u) for b, a, u in zip(before, after, unknown))
    return before != after


def unknown_only(change: dict) -> bool:
    """An update whose every difference from before is a value unknown until apply: from the plan alone it may as well
    end unchanged, as a role assignment does when a value it is named from is recomputed (abc, 19 November)."""
    return (change.get("actions") == ["update"] and any_unknown(change.get("after_unknown"))
            and not known_difference(change.get("before"), change.get("after"), change.get("after_unknown")))


def any_unknown(unknown) -> bool:
    """Whether after_unknown marks any value unknown: an update with no difference to show and nothing unknown is a plain
    change (a provider's own normalisation, say), not one waiting on apply."""
    if unknown is True:
        return True
    if isinstance(unknown, dict):
        return any(any_unknown(value) for value in unknown.values())
    if isinstance(unknown, list):
        return any(any_unknown(value) for value in unknown)
    return False


def state_only(change: dict) -> list[tuple[str, str]]:
    """What a change does to the state alone, each as (kind, what it carries): an import, with its ID; a move, from
    its previous address."""
    found = []
    if change["change"].get("importing"):
        found.append(("import", json.dumps(change["change"]["importing"], sort_keys=True)))
    if change.get("previous_address"):
        found.append(("move", change["previous_address"]))
    return found


def main(plan_file: str, stack: str, summary_file: str, changes_file: str) -> int:
    plan = json.load(open(plan_file))
    if plan.get("errored"):
        print(f"::error::{stack}: Terraform says this plan errored; nothing is applied from it.", file=sys.stderr)
        return 1
    # (kind, address, label, fingerprint) per change. An interrupted replacement leaves a deposed object
    # at the same address as the current one: the label tells them apart, and each keeps its own fingerprint.
    records, refused, moves = [], [], {}
    for change in plan.get("resource_changes", []):
        kind = KIND.get(tuple(change["change"]["actions"]))
        label = change["address"] + (f" deposed:{change['deposed']}" if change.get("deposed") else "")
        for state_kind, carried in state_only(change):
            records.append((state_kind, change["address"], label, hashlib.sha256(f"{state_kind} {carried}".encode()).hexdigest()[:16]))
            if state_kind == "move":
                moves[change["address"]] = carried
        if kind:
            kind = "unknown" if kind == "change" and unknown_only(change["change"]) else kind
            records.append((kind, change["address"], label, fingerprint(change["change"], change.get("type"))))
            before = change["change"].get("before")
            if "delete" in change["change"]["actions"] and isinstance(before, dict) and FIREWALL_POLICY.match(str(before.get("type", ""))):
                refused.append((kind, before.get("name") or label, label))
    if plan.get("complete") is False:  # Terraform expects another plan and apply to converge
        records.append(("deferred", "plan", "plan", hashlib.sha256(json.dumps(plan.get("deferred_changes"), sort_keys=True).encode()).hexdigest()[:16]))
    records.sort(key=lambda item: (item[2], item[0]))
    changes = [(kind, label) for kind, _, label, _ in records]
    outputs = []
    for name, change in plan.get("output_changes", {}).items():
        kind = KIND.get(tuple(change["actions"]))
        if kind:
            outputs.append((kind, f"output.{name}", fingerprint(change)))
    outputs.sort(key=lambda item: item[1])

    with open(changes_file, "w") as out:
        out.writelines(f"{kind} {label} {print_}\n" for kind, _, label, print_ in records)
        out.writelines(f"{kind} {name} {print_}\n" for kind, name, print_ in outputs)
    applyable = plan.get("applyable")
    if applyable is not None and bool(applyable) != bool(records or outputs):
        print(f"::error::{stack}: Terraform says this plan is {'' if applyable else 'not '}applyable, but its list of changes is "
              f"{'empty' if applyable else 'not'}: a kind of change tools/ci/summary.py does not know. Nothing is applied from it.", file=sys.stderr)
        return 1

    resources = [(kind, address, label, print_) for kind, address, label, print_ in records if kind not in STATE_ONLY and kind != "deferred"]
    counts = Counter("change" if kind == "unknown" else kind for kind, _, _, _ in resources)
    unknown = sum(1 for kind, _, _, _ in resources if kind == "unknown")
    by_type: dict[str, Counter] = defaultdict(Counter)
    for kind, address, _, _ in resources:
        by_type[resource_type(address)]["change" if kind == "unknown" else kind] += 1
    existing = [(kind, label) for kind, _, label, _ in resources if kind != "add"]
    state = [(kind, label) for kind, _, label, _ in records if kind in STATE_ONLY]

    lines = []
    for kind, name, label in refused:
        verb = "replaces" if kind == "replace" else "deletes"
        lines += [
            f"**Refused:** this plan {verb} the firewall policy `{name}` (`{label}`). Every rule in it would be "
            "deleted with it, and the firewall would deny traffic until the rules are written again. A firewall "
            "policy only changes in place; renaming or moving one needs a migration step from a release.",
            "",
        ]
    if not changes and not outputs:
        lines.append(f"**{stack}**: no changes.")
    elif not resources:
        lines.append(f"**{stack}**: no resource changes.")
    else:
        headline = f"{counts['add']} to add, {counts['change']} to change, {counts['destroy']} to destroy"
        if counts["replace"]:
            headline += f", {counts['replace']} to replace"
        if unknown:
            headline += f"; {unknown} of the changes only to values unknown until apply, which may end unchanged"
        lines += [f"**{stack}**: {headline}.", "", "| Resource type | Add | Change | Replace | Destroy |", "|---|---:|---:|---:|---:|"]
        for name in sorted(by_type):
            c = by_type[name]
            lines.append(f"| `{name}` | {c['add'] or ''} | {c['change'] or ''} | {c['replace'] or ''} | {c['destroy'] or ''} |")
        if existing:
            lines += ["", "**Changes to existing resources:**", ""]
            lines += [f"- {'change, to values unknown until apply' if kind == 'unknown' else kind}: `{address}`" for kind, address in existing[:LISTED]]
            if len(existing) > LISTED:
                lines.append(f"- and {len(existing) - LISTED} more; the full plan is attached to the run.")
    if state:
        lines += ["", "**In the state only, nothing in Azure changes** (applied, so that the state records it):", ""]
        lines += [f"- imports `{label}`" if kind == "import" else f"- moves `{moves.get(label, '?')}` to `{label}`" if kind == "move"
                  else f"- forgets `{label}`: it stays in Azure and leaves the state" for kind, label in state]
    if outputs:
        lines += ["", "**Outputs that change:** " + ", ".join(f"`{name[len('output.'):]}`" for _, name, _ in outputs)]
    if plan.get("complete") is False:
        lines += ["", "**Not complete:** Terraform says another plan and apply are needed after this one to reach what main says."]
    with open(summary_file, "w") as out:
        out.write("\n".join(lines) + "\n")
    return REFUSED if refused else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # each line reaches the job's log before a subprocess writes there (finding 19)
    sys.exit(main(*sys.argv[1:]))
