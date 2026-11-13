"""What a landing zone ends up with, and where each value comes from.

    python3 -m mlz explain --repo <mlz-vending> <key>

Three parts: every setting its four layers set, with the file that set it and any value it replaced there;
what mlz-core fills in where nothing is set; and what is worked out: the management group (from
governance's contract), the tags, the addresses (from the IP plan) and the hub, DNS servers and routes
(from the platform's contract). Then the landing zone's own check. It covers what mlz-core's loader
decides; what the vending root does the same for every landing zone, such as the NSGs, is in
vending/terraform, not here.
"""

from __future__ import annotations

from pathlib import Path

from . import naming, vending
from .files import SUFFIXES
from .merge import ConfigError

ORDER = ("owner", "cost_center", "archetype", "location", "subscription", "budget", "network", "github", "tags")
REQUIRED = ("owner", "cost_center", "archetype", "subscription", "location")
WIDTH = 44  # a value longer than this puts its source on the next line


def leaves(data: dict, path: tuple = ()) -> dict[tuple, object]:
    """Every value at the end of a path: a group's settings one by one, a list as one value."""
    found = {}
    for key, value in data.items():
        here = (*path, key)
        if isinstance(value, dict) and value:
            found.update(leaves(value, here))
        else:
            found[here] = value
    return found


def file_of(repo: Path, name: str) -> str:
    """A layer's name as the file it is (landing-zones/payments/prod.yaml), or its folder of chunks."""
    for suffix in SUFFIXES:
        if (repo / f"{name}{suffix}").is_file():
            return f"{name}{suffix}"
    return f"{name}/ (chunks)" if (repo / name).is_dir() else name


def show(value) -> str:
    """A value as it is written in YAML."""
    if isinstance(value, list):
        return "[" + ", ".join(show(v) for v in value) + "]"
    if isinstance(value, bool):
        return "true" if value else "false"
    return "null" if value is None else str(value)


def explain(repo: Path, key: str, lz: dict, plan: dict, governance: dict | None, platform: dict | None) -> dict[str, list[tuple[str, str, str]]]:
    """The landing zone's values by part, each as (setting, value, where it comes from)."""
    settings, final = lz["settings"], leaves(lz["settings"])
    # Every value each layer set, in layer order: (layer, file, path, value).
    set_by = [(index, file_of(repo, name), path, value) for index, (name, data) in enumerate(lz["layers"]) for path, value in leaves(data).items()]

    def order(path: tuple):
        return (ORDER.index(path[0]) if path[0] in ORDER else len(ORDER), [str(p) for p in path])

    def dotted(path: tuple) -> str:
        return ".".join(map(str, path))

    layered = []
    for path in sorted(final, key=order):
        mine = [entry for entry in set_by if entry[2] == path]
        last, source = (mine[-1][0], mine[-1][1]) if mine else (len(lz["layers"]), file_of(repo, vending.origin(lz, *map(str, path))))
        # What an earlier layer set here, below it (a group a later layer replaced whole) or above it.
        replaced = [
            f"{file}: {show(value)}" if at == path else f"{file}: {dotted(at)} {show(value)}"
            for index, file, at, value in set_by
            if index < last and (at == path and value != final[path] or at[:len(path)] == path and at != path or path[:len(at)] == at and at != path)
        ]
        layered.append((dotted(path), show(final[path]), source + (f", replacing {'; '.join(replaced)}" if replaced else "")))

    filled = [(k, "not set", "required: set it in one of the layers") for k in REQUIRED if settings.get(k) in (None, "")]
    budget, network = settings.get("budget"), settings.get("network")
    budget, network = budget if isinstance(budget, dict) else {}, network if isinstance(network, dict) else {}
    if budget.get("monthly") is None:
        filled.append(("budget.monthly", "not set", "no budget"))
    if not budget.get("contacts"):  # none, or an empty list: the alerts go to the owner, as vending.build has it
        filled.append(("budget.contacts", show([settings.get("owner")]), "the owner, as no contacts are given"))
    size = network.get("size", vending.DEFAULT_SIZE)
    if "size" not in network:
        filled.append(("network.size", str(vending.DEFAULT_SIZE), "mlz-core's default"))
    if network.get("subnets") is None:
        default = max(26, size) if isinstance(size, int) else 26
        filled.append(("network.subnets.default", str(default), "mlz-core's default: one subnet, a /26 or the whole of a smaller range"))
    if not isinstance(settings.get("github"), dict) or not settings["github"].get("repo"):
        filled.append(("github", "not set", "no repo of its own"))

    worked = []
    archetype, location = settings.get("archetype"), settings.get("location")
    if governance is None:
        worked.append(("management group", "not known yet", "waits for governance's contract (_generated/governance.yaml)"))
    elif vending.decommissioned(lz):
        worked.append(("management group", f"{governance['root']}-decommissioned", "decommission: true; its plan destroys everything vending made"))
    elif isinstance(archetype, str) and archetype in governance.get("archetypes", {}):
        worked.append(("management group", governance["archetypes"][archetype], f"governance's contract, for archetype {archetype}"))
    tags = {"Owner": (settings.get("owner"), "owner"), "CostCenter": (settings.get("cost_center"), "cost_center"),
            "Environment": (lz["environment"], "the file name"), "Workload": (lz["workload"], "the folder name"),
            "ManagedBy": ("mlz-core", "mlz-core")}
    worked += [(f"tag {name}", show(value), f"from {source}") for name, (value, source) in tags.items()]
    entry = plan.get(key)
    if entry is None:
        worked.append(("address range", "not given yet", "the IP allocator gives it on the pull request"))
    else:
        worked.append(("address range", str(entry["range"]), f"the IP plan ({vending.IP_PLAN})"))
        worked += [(f"subnet {name}", str(block), "the IP plan") for name, block in entry["subnets"].items()]
        worked += [(f"subnet {name}", str(block), "the IP plan: removed, held until released") for name, block in entry["removed_subnets"].items()]
    region = (platform or {}).get("regions", {}).get(location) if isinstance(location, str) else None
    if platform is None:
        worked.append(("hub", "not known yet", "waits for the platform's contract (_generated/platform.yaml)"))
    elif region:
        source = f"the platform's contract, for {location}"
        worked.append(("hub network", region["hub_network"], source))
        worked.append(("DNS servers", show(region["dns_servers"]), source))
        worked += [(f"route {r['name']}", f"{r['to']} via {r['via']}", source) for r in region.get("spoke_routes") or []]
    if governance is not None and entry is not None:
        # Names, as vending gives them: the landing zone's own under names:, or the customer's template.
        named = landing_zone_names(key, lz, entry, governance, [])  # problems() reports what stops one
        own = lz["settings"].get("names") if isinstance(lz["settings"].get("names"), dict) else {}
        for item, value in named.items():
            if item in vending.NAMED and value:
                worked.append((f"name {item}", value, f"names: in {file_of(repo, lz['where'])}" if item in own else "the naming template (docs/naming.md)"))
        for subnet, value in (named.get("network_security_groups") or {}).items():
            if value:
                given = subnet in (own.get("network_security_groups") or {}) if isinstance(own.get("network_security_groups"), dict) else False
                worked.append((f"name NSG of {subnet}", value, f"names: in {file_of(repo, lz['where'])}" if given else "the naming template (docs/naming.md)"))
    return {"Set in its layers": layered, "Filled in by mlz-core": filled, "Worked out": worked}


def landing_zone_names(key: str, lz: dict, entry: dict, governance: dict, found: list[str]) -> dict:
    """The names vending gives the landing zone, with what stops any of them in found."""
    try:
        settings = naming.settings({"naming": governance.get("naming") or None}, governance["root"])
    except ConfigError as error:
        found.append(f"_generated/governance.yaml: {error}")
        return {}
    return vending.landing_zone_names(key, lz, settings, sorted(entry["subnets"]), found)


def problems(key: str, lz: dict, plan: dict, governance: dict | None, platform: dict | None) -> list[str]:
    """This landing zone's own problems, as the vending check finds them; the check also compares it with
    the others."""
    found: list[str] = []
    if governance is not None and platform is not None:
        vending.check(key, lz, governance, platform, found)
    else:
        # The rest of check() needs the contracts; a required setting set nowhere needs nothing but the layers.
        found += [f"{lz['where']}: {k} is required (set it here or in a layer above)." for k in REQUIRED if lz["settings"].get(k) in (None, "")]
    wanted = vending.wanted_network(lz, found)
    if key in plan and wanted:
        if set(plan[key]["subnets"]) != set(wanted[1]):
            found.append(f"{key}: its subnets in {vending.IP_PLAN} are not the ones asked for; the IP allocator gives them on the pull request.")
        found += vending.size_problems(key, plan[key], wanted)
    if key in plan:
        if platform is not None:
            # What no longer holds for what it was given: overlaps with the hubs and reserved ranges, subnets
            # outside its range, a range outside its region's pool. Its plan alone, so no other's problems show.
            found += vending.plan_problems({key: plan[key]}, {key: lz}, {key: wanted}, platform)
        given = plan[key]["range"]
        found += [f"{vending.IP_PLAN}: {key}'s range {given} overlaps {other}'s {entry['range']}."
                  for other, entry in sorted(plan.items()) if other != key and given.overlaps(entry["range"])]
        if governance is not None:
            landing_zone_names(key, lz, plan[key], governance, found)
    return list(dict.fromkeys(found))


def clean(complete: bool) -> str:
    """What the check says when it finds nothing: all of it, or what it could check before the contracts came."""
    return ("no problems in this landing zone" + ("" if complete else "; the checks that need the contracts wait for them")
            + " (python3 -m mlz vending checks them all together)")


def render(repo: Path, key: str, lz: dict, parts: dict[str, list[tuple[str, str, str]]], found: list[str], complete: bool = True) -> str:
    rows = [row for part in parts.values() for row in part]
    first = max(len(setting) for setting, _, _ in rows) + 2
    lines = [f"{key} ({file_of(repo, lz['where'])})"]
    for title, part in parts.items():
        if not part:
            continue
        lines += ["", title]
        for setting, value, source in part:
            if len(value) > WIDTH:
                lines += [f"  {setting:<{first}}{value}", f"  {'':<{first}}<- {source}"]
            else:
                lines.append(f"  {setting:<{first}}{value:<{WIDTH}}  <- {source}")
    lines += ["", "Check"]
    lines += [f"  {problem}" for problem in found] or [f"  {clean(complete)}"]
    return "\n".join(line.rstrip() for line in lines) + "\n"
