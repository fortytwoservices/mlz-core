"""The vending role: landing-zones/ into one checked config per landing zone.

Each landing zone is four layers, each saying only what differs from the one above:

  landing-zones/_defaults/all.yaml          every landing zone
  landing-zones/_defaults/<env>.yaml        every landing zone in that environment (optional)
  landing-zones/<workload>/_workload.yaml   one workload, all its environments
  landing-zones/<workload>/<env>.yaml       one landing zone: one subscription, one network, one state

Any of them can be a folder of chunks instead of one file. The landing zone's key is <workload>-<env>.
Address ranges and subnets are not typed by anyone: the IP allocator writes them to _generated/ip-plan.yaml.
Nothing it has given ever moves, and nothing removed is given out again until it is released.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Network, ip_address
from pathlib import Path

from .files import read_setting, setting_names
from .merge import ConfigError, layer
from .yamlout import dump

# Tags filled from a landing zone's own settings, and the ones mlz-core sets itself.
TAG_SETTINGS = {"Owner": "owner", "CostCenter": "cost_center"}
AUTOMATIC_TAGS = {"Environment": "the file name", "Workload": "the folder name", "ManagedBy": "mlz-core"}
KEYS = {"name", "owner", "cost_center", "archetype", "location", "subscription", "budget", "network", "github", "tags"}
SECTIONS = {"budget": {"monthly", "contacts"}, "network": {"size", "subnets"}, "github": {"organization", "repo"}}
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,22}[a-z0-9])?$")
# 20 at most: the longest name built from one, nsg-<workload>-<env>-<subnet>-<region>, must fit Azure's 80.
SUBNET_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$")
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GITHUB_ORGANIZATION = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GITHUB_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
DEFAULT_SIZE = 24
IP_PLAN = "_generated/ip-plan.yaml"
IP_PLAN_HEADER = (
    "Every landing zone's network: its address range and subnets.\n"
    "Written by the IP allocator (python3 -m mlz allocate): do not edit.\n"
    "Nothing here ever moves. A removed landing zone or subnet keeps its block until it is destroyed and\n"
    "released (python3 -m mlz release)."
)


def landing_zones(repo: Path) -> dict[str, dict]:
    """Every landing zone's merged settings, by key, with where it came from."""
    root = repo / "landing-zones"
    if not root.is_dir():
        raise ConfigError(f"{root}: no landing-zones folder.")
    defaults = read_setting(root / "_defaults" / "all") or {}
    found = {}
    for workload in setting_names(root):
        folder = root / workload
        if not folder.is_dir():
            raise ConfigError(f"{folder}.yaml: a landing zone lives in a folder, landing-zones/<workload>/<env>.yaml.")
        workload_settings = read_setting(folder / "_workload") or {}
        for env in setting_names(folder):
            key, where = f"{workload}-{env}", f"landing-zones/{workload}/{env}"
            if key in found:
                raise ConfigError(f"{found[key]['where']} and {where} are both the landing zone {key}; rename one.")
            merged = layer(defaults, read_setting(root / "_defaults" / env) or {})
            merged = layer(merged, workload_settings)
            merged = layer(merged, read_setting(folder / env) or {})
            found[key] = {"workload": workload, "environment": env, "where": where, "settings": merged}
    return found


def section(s: dict, name: str, where: str, problems: list[str], also: set[str] = frozenset()) -> dict:
    """A group of settings (budget, network, github), or {} when it is not set or not valid."""
    value = s.get(name)
    if value is None:
        return {}
    known = SECTIONS[name]
    if not isinstance(value, dict):
        problems.append(f"{where}: {name} is a group of settings ({', '.join(sorted(known))}), not {value!r}.")
        return {}
    problems += [f"{where}: {name}.{k} is not a setting; use {', '.join(sorted(known))}." for k in sorted(set(value) - known - also, key=str)]
    return value


def name_problems(key: str, lz: dict) -> list[str]:
    if NAME.fullmatch(lz["workload"]) and NAME.fullmatch(lz["environment"]):
        return []
    return [f"{lz['where']}: folder and file names are up to 24 lower-case letters, digits and hyphens (the key {key!r} is used in names)."]


def check(key: str, lz: dict, governance: dict, platform: dict, problems: list[str]) -> None:
    """Everything but the network, which wanted_network checks."""
    s, where = lz["settings"], lz["where"]
    problems += name_problems(key, lz)
    problems += [f"{where}: {k!r} is not a landing zone setting; use {', '.join(sorted(KEYS))}." for k in sorted(set(s) - KEYS, key=str)]
    for required in ("owner", "cost_center", "archetype", "subscription", "location"):
        if s.get(required) in (None, ""):
            problems.append(f"{where}: {required} is required (set it here or in a layer above).")
    for single in ("name", "owner", "archetype", "location", "subscription"):
        if s.get(single) is not None and not isinstance(s[single], str):
            problems.append(f"{where}: {single} is one value, not {s[single]!r}.")
    if s.get("cost_center") is not None and (isinstance(s["cost_center"], bool) or not isinstance(s["cost_center"], (str, int))):
        problems.append(f"{where}: cost_center is one value, not {s['cost_center']!r}.")
    owner, archetype, subscription, location = (s.get(k) if isinstance(s.get(k), str) else None for k in ("owner", "archetype", "subscription", "location"))
    if owner and not EMAIL.fullmatch(owner):
        problems.append(f"{where}: owner {owner!r} should be the owning team's email address.")
    archetypes = governance["archetypes"]
    if archetype and archetype not in archetypes:
        problems.append(f"{where}: archetype {archetype!r} must be one of {', '.join(archetypes)}.")
    if subscription and not GUID.fullmatch(subscription.lower()):
        problems.append(f"{where}: subscription must be a subscription ID.")
    if subscription and subscription.lower() in {v.lower() for v in governance["platform_subscriptions"].values()}:
        problems.append(f"{where}: subscription {subscription} is a platform subscription.")
    if location and location not in governance["allowed_regions"]:
        problems.append(f"{where}: location {location} is not one of the allowed regions ({', '.join(governance['allowed_regions'])}).")
    if location and location not in platform["regions"]:
        problems.append(f"{where}: location {location} has no hub to connect to.")

    tags = s.get("tags") or {}
    if not isinstance(tags, dict):
        problems.append(f"{where}: tags maps each extra tag to its value, for example BusinessUnit: Finance.")
        tags = {}
    # Azure tag names ignore case: Owner and owner are one tag.
    from_settings = {t.lower(): setting for t, setting in TAG_SETTINGS.items()}
    automatic = {t.lower(): source for t, source in AUTOMATIC_TAGS.items()}
    seen = {}
    for tag, value in tags.items():
        name = tag.lower()
        if name in seen:
            problems.append(f"{where}: tags {seen[name]} and {tag} are one tag to Azure, which ignores case; keep one.")
        seen[name] = tag
        if name in from_settings:
            problems.append(f"{where}: tags.{tag} comes from {from_settings[name]}; set that instead.")
        elif name in automatic:
            problems.append(f"{where}: tags.{tag} is set from {automatic[name]}; remove it here.")
        elif isinstance(value, (dict, list)) or value is None:
            problems.append(f"{where}: tags.{tag} is one value, not {value!r}.")
    for tag in governance.get("required_tags") or []:
        if tag.lower() not in from_settings | automatic | seen:
            problems.append(f"{where}: governance requires the {tag} tag; set it under tags: here or in a layer above.")

    budget = section(s, "budget", where, problems)
    monthly = budget.get("monthly")
    if monthly is not None and (type(monthly) is not int or monthly <= 0):
        problems.append(f"{where}: budget.monthly is a whole amount, for example 500.")
    contacts = budget.get("contacts")
    if contacts is not None and (not isinstance(contacts, list) or not all(isinstance(c, str) and EMAIL.fullmatch(c) for c in contacts)):
        problems.append(f"{where}: budget.contacts is a list of email addresses, for example [team@example.com].")

    github = section(s, "github", where, problems)
    organization, repo = github.get("organization"), github.get("repo")
    if organization is not None and not (isinstance(organization, str) and GITHUB_ORGANIZATION.fullmatch(organization)):
        problems.append(f"{where}: github.organization {organization!r} is not a GitHub organization name.")
    if repo is not None and not (isinstance(repo, str) and GITHUB_REPO.fullmatch(repo)):
        problems.append(f"{where}: github.repo {repo!r} is a repository name in the organization, for example payments-infra.")
    if repo is not None and organization is None:
        problems.append(f"{where}: github.repo needs github.organization (set it once in _defaults/all.yaml).")


def wanted_network(where: str, s: dict, problems: list[str]) -> tuple[int, dict[str, int]] | None:
    """The network the landing zone asks for: its prefix length and {subnet: prefix length}.
    None when it is not valid; the problems say why."""
    before = len(problems)
    network = section(s, "network", where, problems, also={"address_space"})
    if "address_space" in network:
        problems.append(f"{where}: network.address_space is written by the IP allocator to {IP_PLAN}; remove it here.")
    size = network.get("size", DEFAULT_SIZE)
    if type(size) is not int or not 16 <= size <= 28:
        problems.append(f"{where}: network.size is a prefix length from 16 to 28, for example 24.")
        size = None
    subnets = network.get("subnets")
    if subnets is None:
        subnets = {"default": max(26, size or DEFAULT_SIZE)}  # a quarter of a /24, or all of a smaller range
    elif not isinstance(subnets, dict) or not subnets:
        problems.append(f"{where}: network.subnets maps each subnet's name to its prefix length, for example app: 26.")
        subnets = {}
    for name, length in subnets.items():
        if not SUBNET_NAME.fullmatch(str(name)):
            problems.append(f"{where}: subnet name {name!r} is up to 20 lower-case letters, digits and hyphens.")
        if type(length) is not int or not 8 <= length <= 29:
            problems.append(f"{where}: network.subnets.{name} is a prefix length up to 29 (Azure's smallest subnet), for example 26.")
        elif size and length < size:
            problems.append(f"{where}: network.subnets.{name} is a /{length}, larger than the landing zone's /{size}.")
    if len(problems) > before:
        return None
    if sum(2 ** (32 - length) for length in subnets.values()) > 2 ** (32 - size):
        problems.append(f"{where}: the subnets need more addresses than a /{size} has.")
        return None
    return size, subnets


def ip_plan(repo: Path) -> dict[str, dict]:
    """The IP plan: {key: {range, subnets: {name: block}, removed_subnets: {name: block}}}, as networks.
    removed_subnets are blocks still held after their subnet left the YAML, until released."""
    plan = read_setting(repo / IP_PLAN.removesuffix(".yaml")) or {}
    parsed = {}
    for key, entry in plan.items():
        try:
            if not isinstance(entry, dict) or not {"range", "subnets"} <= set(entry) <= {"range", "subnets", "removed_subnets"}:
                raise ValueError
            parsed[str(key)] = {
                "range": IPv4Network(entry["range"]),
                **{part: {str(name): IPv4Network(block) for name, block in entry.get(part, {}).items()} for part in ("subnets", "removed_subnets")},
            }
        except (AttributeError, TypeError, ValueError):
            raise ConfigError(f"{IP_PLAN}: {key} is not as the IP allocator writes it; restore the file from git.") from None
    return parsed


def plan_problems(plan: dict, zones: dict, wanted: dict, platform: dict) -> list[str]:
    """What no longer holds for ranges and subnets already given. Nothing given is ever moved, so a
    conflict is reported for a person to resolve."""
    problems = []
    hubs = [IPv4Network(r) for region in platform["regions"].values() for r in region["hub_range"]]
    keys = sorted(plan)
    for index, key in enumerate(keys):
        given = plan[key]["range"]
        problems += [f"{IP_PLAN}: {key}'s range {given} overlaps the hub range {h}." for h in hubs if given.overlaps(h)]
        problems += [f"{IP_PLAN}: {key}'s range {given} overlaps {other}'s {plan[other]['range']}." for other in keys[index + 1:] if given.overlaps(plan[other]["range"])]
        blocks = sorted([*plan[key]["subnets"].items(), *plan[key]["removed_subnets"].items()], key=lambda item: item[1])
        problems += [f"{IP_PLAN}: {key}'s subnet {name} {block} is outside its range {given}." for name, block in blocks if not block.subnet_of(given)]
        problems += [f"{IP_PLAN}: {key}'s subnets {a} and {b} overlap." for (a, x), (b, y) in zip(blocks, blocks[1:]) if x.overlaps(y)]
        if key not in zones:
            continue
        location = zones[key]["settings"].get("location")
        region = platform["regions"].get(location) if isinstance(location, str) else None
        pools = [IPv4Network(p) for p in region["landing_zone_ranges"]] if region else []
        if region and not any(given.subnet_of(p) for p in pools):
            problems.append(f"{key}: its range {given} is outside {location}'s landing zone ranges ({', '.join(map(str, pools))}); a range never moves.")
        if wanted.get(key) is None:
            continue
        size, subnets = wanted[key]
        if given.prefixlen != size:
            problems.append(f"{key}: network.size is {size}, but its range {given} was given as a /{given.prefixlen}; a range never changes size.")
        for name, block in plan[key]["subnets"].items():
            if name in subnets and subnets[name] != block.prefixlen:
                problems.append(f"{key}: subnet {name} is a /{subnets[name]} here but was given as {block}; a subnet never changes size, so give the new one another name.")
    return problems


def place(pools: list[IPv4Network], size: int, taken: list[IPv4Network]) -> IPv4Network | None:
    """The first block of this size in the pools that overlaps nothing taken."""
    for pool in pools:
        if pool.prefixlen > size:
            continue  # too small for one block
        for block in pool.subnets(new_prefix=size):
            if not any(block.overlaps(t) for t in taken):
                return block
    return None


def allocate(repo: Path, platform: dict) -> tuple[dict[str, dict], list[str], list[str]]:
    """The IP plan with a range for every new landing zone and a block for every new subnet: the first
    free one, largest first. A subnet removed from the YAML keeps its block, under removed_subnets, until
    it is released; asked for again, it gets that block back. Returns (plan, what changed, landing zones
    removed but still holding a range). Writes nothing; fails with every problem when any landing zone's
    network is not valid."""
    zones, plan, problems = landing_zones(repo), ip_plan(repo), []
    wanted = {key: wanted_network(lz["where"], lz["settings"], problems) for key, lz in zones.items()}
    for key, lz in zones.items():
        problems += name_problems(key, lz)
        location = lz["settings"].get("location")
        if not isinstance(location, str) or location not in platform["regions"]:
            problems.append(f"{lz['where']}: location {location!r} has no hub, so it has no ranges to take from.")
        for name, block in plan.get(key, {}).get("removed_subnets", {}).items():
            length = (wanted[key] or (0, {}))[1].get(name)
            if length is not None and length != block.prefixlen:
                problems.append(f"{key}: subnet {name} was removed and still holds {block}; ask for a /{block.prefixlen} to get it back, or release it first (python3 -m mlz release).")
    problems += plan_problems(plan, zones, wanted, platform)
    if problems:
        raise ConfigError("cannot allocate:\n  " + "\n  ".join(problems))

    taken = [entry["range"] for entry in plan.values()]
    taken += [IPv4Network(r) for region in platform["regions"].values() for r in region["hub_range"]]
    result, changes = {}, []
    # Largest first, so a small range never takes the only room a larger one had.
    for key in sorted(zones, key=lambda k: (wanted[k][0], k)):
        size, subnets = wanted[key]
        entry = plan.get(key)
        if entry is None:
            pools = [IPv4Network(p) for p in platform["regions"][zones[key]["settings"]["location"]]["landing_zone_ranges"]]
            block = place(pools, size, taken)
            if block is None:
                problems.append(f"{key}: no free /{size} left in {', '.join(map(str, pools))}.")
                continue
            taken.append(block)
            entry = {"range": block, "subnets": {}, "removed_subnets": {}}
            changes.append(f"{key}: range {block}")
        used = [*entry["subnets"].values(), *entry["removed_subnets"].values()]
        kept = {name: block for name, block in entry["subnets"].items() if name in subnets}
        removed = dict(entry["removed_subnets"])
        for name, block in entry["subnets"].items():
            if name not in subnets:
                removed[name] = block
                changes.append(f"{key}: subnet {name} {block} removed, held until released")
        for name, length in sorted(((n, l) for n, l in subnets.items() if n not in kept), key=lambda item: (item[1], item[0])):
            if name in removed:
                kept[name] = removed.pop(name)
                changes.append(f"{key}: subnet {name} {kept[name]} given back")
                continue
            block = place([entry["range"]], length, used)
            if block is None:
                problems.append(f"{key}: no free /{length} left in {entry['range']} for subnet {name}.")
                continue
            used.append(block)
            kept[name] = block
            changes.append(f"{key}: subnet {name} {block}")
        by_address = lambda blocks: dict(sorted(blocks.items(), key=lambda item: item[1]))
        result[key] = {"range": entry["range"], "subnets": by_address(kept), "removed_subnets": by_address(removed)}
    if problems:
        raise ConfigError("cannot allocate:\n  " + "\n  ".join(problems))
    retired = sorted(set(plan) - set(zones))
    result.update({key: plan[key] for key in retired})
    return dict(sorted(result.items())), changes, retired


def release(repo: Path, key: str) -> tuple[dict[str, dict], list[str], str]:
    """Frees what a landing zone holds but no longer uses, once that is destroyed: a removed landing
    zone's whole range, or a current one's removed subnets. Returns the plan, the removed landing zones
    still in it, and what was freed."""
    zones, plan = landing_zones(repo), ip_plan(repo)
    if key not in plan:
        raise ConfigError(f"{key} has nothing in {IP_PLAN}.")
    if key in zones:
        removed = plan[key]["removed_subnets"]
        if not removed:
            raise ConfigError(f"{key} is still in {zones[key]['where']} and holds no removed subnets; to release its range, remove it and destroy it first.")
        freed = f"{key}'s removed subnets " + ", ".join(f"{name} {block}" for name, block in removed.items())
        plan[key] = {**plan[key], "removed_subnets": {}}
    else:
        freed = f"{key}'s range {plan[key]['range']}"
        del plan[key]
    return plan, sorted(set(plan) - set(zones)), freed


def render_plan(plan: dict[str, dict], retired: list[str]) -> str:
    def entry(e: dict) -> dict:
        shown = {"range": str(e["range"]), "subnets": {n: str(b) for n, b in e["subnets"].items()}}
        if e.get("removed_subnets"):
            shown["removed_subnets"] = {n: str(b) for n, b in e["removed_subnets"].items()}
        return shown

    note = "Removed from landing-zones/: its range is held until it is destroyed and released."
    return dump({key: entry(e) for key, e in plan.items()}, IP_PLAN_HEADER, {key: note for key in retired}, spaced=True)


def build(repo: Path, governance: dict, platform: dict) -> dict[str, dict]:
    """Each landing zone's config, by key. Fails with every problem when anything is not valid."""
    zones, plan, problems = landing_zones(repo), ip_plan(repo), []
    wanted = {}
    for key, lz in zones.items():
        check(key, lz, governance, platform, problems)
        wanted[key] = wanted_network(lz["where"], lz["settings"], problems)
        if key not in plan:
            problems.append(f"{key}: no address range yet; run the IP allocator (python3 -m mlz allocate).")
        elif wanted[key] and set(plan[key]["subnets"]) != set(wanted[key][1]):
            problems.append(f"{key}: its subnets in {IP_PLAN} are not the ones asked for; run the IP allocator (python3 -m mlz allocate).")
    problems += plan_problems(plan, zones, wanted, platform)
    subscriptions = [str(z["settings"].get("subscription", "")).lower() for z in zones.values()]
    problems += [f"subscription {s} is used by more than one landing zone." for s in sorted({s for s in subscriptions if s and subscriptions.count(s) > 1})]
    if problems:
        raise ConfigError("landing zone settings are not valid:\n  " + "\n  ".join(problems))

    configs = {}
    for key, lz in zones.items():
        s = lz["settings"]
        region = platform["regions"][s["location"]]
        budget, github = s.get("budget") or {}, s.get("github") or {}
        configs[key] = {
            "key": key,
            "root_id": governance["root"],
            "display_name": f"{s.get('name', lz['workload'])} ({lz['environment']})",
            "subscription_id": s["subscription"].lower(),
            "management_group_id": governance["archetypes"][s["archetype"]],
            "location": s["location"],
            "tags": {
                **{str(k): str(v) for k, v in (s.get("tags") or {}).items()},
                "Owner": s["owner"],
                "CostCenter": str(s["cost_center"]),
                "Environment": lz["environment"],
                "Workload": lz["workload"],
                "ManagedBy": "mlz-core",
            },
            # Budget alerts go to the owner unless contacts are given.
            "budget": {"monthly": budget.get("monthly"), "contacts": budget.get("contacts") or [s["owner"]]},
            "network": {
                "address_space": str(plan[key]["range"]),
                "subnets": {name: str(block) for name, block in plan[key]["subnets"].items()},
                "hub_virtual_network_id": region["hub_network"],
                "dns_servers": region["dns_servers"],
                "routes": route_table_routes(region.get("spoke_routes") or []),
            },
            "github": {"organization": github["organization"], "repo": github["repo"]} if github.get("repo") else None,
        }
    return configs


def route_table_routes(routes: list[dict]) -> dict[str, dict]:
    """The contract's routes ({name, to, via}) in the route table module's shape. `via` is an address
    (through that appliance, the firewall) or a next hop type such as Internet."""
    shaped = {}
    for route in routes:
        via = str(route["via"])
        try:
            ip_address(via)
            hop, address = "VirtualAppliance", via
        except ValueError:
            hop, address = via, None
        shaped[route["name"]] = {"name": route["name"], "address_prefix": route["to"], "next_hop_type": hop, "next_hop_in_ip_address": address}
    return shaped
