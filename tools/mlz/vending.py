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

from . import naming
from .files import preload, read_setting, read_yaml, setting_names, yaml_files
from .merge import ConfigError, layer
from .yamlout import dump

# Tags filled from a landing zone's own settings, and the ones mlz-core sets itself.
TAG_SETTINGS = {"Owner": "owner", "CostCenter": "cost_center"}
AUTOMATIC_TAGS = {"Environment": "the file name", "Workload": "the folder name", "ManagedBy": "mlz-core"}
KEYS = {"owner", "cost_center", "archetype", "location", "subscription", "budget", "network", "github", "tags", "decommission", "short_name", "names"}
# What vending names in a landing zone from the customer's template (docs/naming.md), by the config's key: the type,
# and the purpose that tells apart two of one type. An explicit name under names: in the landing zone's file wins.
NAMED = {
    "network_resource_group": ("resource_group", "net"),
    "identity_resource_group": ("resource_group", "id"),
    "virtual_network": ("virtual_network", None),
    "route_table": ("route_table", None),
    "deploy_identity": ("user_assigned_identity", None),
    "budget": ("budget", None),
    # The workload repo's (docs/vending.md): its plan identity, and its Terraform state, in a group of its own that
    # decommissioning keeps, since the team's resources may outlive the landing zone's.
    "plan_identity": ("user_assigned_identity", "plan"),
    "state_resource_group": ("resource_group", "state"),
    "state_account": ("storage_account", "state"),
}
# Recorded beside them: the workload repo vending creates, named by customer.yaml's naming.repository.
REPOSITORY = "repository"
SECTIONS = {"budget": {"monthly", "contacts"}, "network": {"size", "subnets"}, "github": {"organization", "repo", "create", "team"}}
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,22}[a-z0-9])?$")
# 20 at most: the longest name built from one, nsg-<workload>-<env>-<subnet>-<region>, must fit Azure's 80.
SUBNET_NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,18}[a-z0-9])?$")
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GITHUB_ORGANIZATION = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
GITHUB_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
GITHUB_TEAM = re.compile(r"^[a-z0-9]([a-z0-9_-]{0,98}[a-z0-9])?$")  # a team's slug, as in its URL
DEFAULT_SIZE = 24
# Azure: 50 tags on a resource, of which mlz-core sets five; names up to 512, values up to 256.
EXTRA_TAGS, TAG_NAME_LENGTH, TAG_VALUE_LENGTH = 45, 512, 256
TAG_NAME_REFUSED = set('<>%&\\?/')
IP_PLAN = "_generated/ip-plan.yaml"
IP_PLAN_HEADER = (
    "Every landing zone's network: its address range and subnets.\n"
    "Written by the IP allocator (python3 -m mlz allocate): do not edit.\n"
    "Nothing here ever moves. A removed landing zone or subnet keeps its block until it is destroyed and\n"
    "released (python3 -m mlz release)."
)
NAMES = "_generated/names.yaml"
NAMES_HEADER = (
    "The names vending gave each landing zone from the naming template (docs/naming.md), kept as first given.\n"
    "Written by the IP allocator (python3 -m mlz allocate): do not edit.\n"
    "A name here stays when the template or a short name changes. To give a landing zone the template's names\n"
    "again, run python3 -m mlz names --renew <key>; to change one name, set it under names: in its file.\n"
    "A removed landing zone or subnet keeps its names while it holds its addresses in the IP plan."
)


def landing_zones(repo: Path) -> dict[str, dict]:
    """Every landing zone's merged settings, by key, with the file each setting came from."""
    root = repo / "landing-zones"
    if not root.is_dir():
        raise ConfigError(f"{root}: no landing-zones folder.")
    preload(yaml_files(root) + yaml_files(repo / "_generated"))
    read = {}  # each file is read once, however many landing zones share it

    def setting(*parts: str) -> tuple[str, dict]:
        name = "landing-zones/" + "/".join(parts)
        if name not in read:
            read[name] = read_setting(root.joinpath(*parts)) or {}
        return name, read[name]

    found = {}
    for workload in setting_names(root):
        folder = root / workload
        if not folder.is_dir():
            raise ConfigError(f"{folder}.yaml: a landing zone lives in a folder, landing-zones/<workload>/<env>.yaml.")
        for env in setting_names(folder):
            key, where = f"{workload}-{env}", f"landing-zones/{workload}/{env}"
            if key in found:
                raise ConfigError(f"{found[key]['where']} and {where} are both the landing zone {key}; rename one.")
            merged, origins = {}, {}
            layers = [setting("_defaults", "all"), setting("_defaults", env), setting(workload, "_workload"), setting(workload, env)]
            for name, data in layers:
                merged = layer(merged, data)
                record(origins, data, name)
            found[key] = {"workload": workload, "environment": env, "where": where, "settings": merged, "origins": origins, "layers": layers}
    return found


def record(origins: dict, data: dict, name: str, path: tuple = ()) -> None:
    """Notes `name` as the file that set each value in `data`, as a later layer overrides an earlier one."""
    for key, value in data.items():
        here = (*path, key)
        if isinstance(value, dict) and value:
            origins.pop(here, None)
            record(origins, value, name, here)
        else:
            origins[here] = name


def origin(lz: dict, *path: str) -> str:
    """The file that set this setting, for a problem to point at; the landing zone's own when none did."""
    while path:
        if path in lz["origins"]:
            return lz["origins"][path]
        path = path[:-1]
    return lz["where"]


def section(lz: dict, name: str, problems: list[str], also: set[str] = frozenset()) -> dict:
    """A group of settings (budget, network, github), or {} when it is not set or not valid."""
    value = lz["settings"].get(name)
    if value is None:
        return {}
    known = SECTIONS[name]
    if not isinstance(value, dict):
        problems.append(f"{origin(lz, name)}: {name} is a group of settings ({', '.join(sorted(known))}), not {value!r}.")
        return {}
    problems += [f"{origin(lz, name, k)}: {name}.{k} is not a setting; use {', '.join(sorted(known))}." for k in sorted(set(value) - known - also, key=str)]
    return value


def name_problems(key: str, lz: dict) -> list[str]:
    if NAME.fullmatch(lz["workload"]) and NAME.fullmatch(lz["environment"]):
        return []
    return [f"{lz['where']}: folder and file names are up to 24 lower-case letters, digits and hyphens (the key {key!r} is used in names)."]


def check(key: str, lz: dict, governance: dict, platform: dict, problems: list[str]) -> None:
    """Everything but the network, which wanted_network checks. Each problem names the file to fix."""
    s, where = lz["settings"], lz["where"]

    def at(*path: str) -> str:
        return origin(lz, *path)

    problems += name_problems(key, lz)
    problems += [f"{at(k)}: {k!r} is not a landing zone setting; use {', '.join(sorted(KEYS))}." for k in sorted(set(s) - KEYS, key=str)]
    # One landing zone at a time, in its own file: a layer above would retire every landing zone below it. Every
    # layer is looked at, since the landing zone's own file setting it too would hide one above.
    for name, data in lz["layers"][:-1]:
        if "decommission" in data:
            problems.append(f"{name}: decommission retires one landing zone, so it goes in that landing zone's own file, {where}.")
    for name, data in lz["layers"][:-1]:
        if "names" in data:
            problems.append(f"{name}: names are one landing zone's own, so they go in that landing zone's file, {where}.")
    # One short name per workload: a landing zone's file, or a default, setting one would name it apart from the rest.
    for name, data in lz["layers"]:
        if "short_name" in data and name != f"landing-zones/{lz['workload']}/_workload":
            problems.append(f"{name}: short_name is the workload's, so it goes in landing-zones/{lz['workload']}/_workload.yaml.")
    problems += short_name_problems(lz)
    if "decommission" in s and s["decommission"] is not True:
        problems.append(f"{at('decommission')}: decommission is true or not there at all, not {s['decommission']!r}.")
    for required in ("owner", "cost_center", "archetype", "subscription", "location"):
        if s.get(required) in (None, ""):
            problems.append(f"{where}: {required} is required (set it here or in a layer above).")
    for single in ("owner", "archetype", "location", "subscription"):
        if s.get(single) is not None and not isinstance(s[single], str):
            problems.append(f"{at(single)}: {single} is one value, not {s[single]!r}.")
    if s.get("cost_center") is not None and (isinstance(s["cost_center"], bool) or not isinstance(s["cost_center"], (str, int))):
        problems.append(f"{at('cost_center')}: cost_center is one value, not {s['cost_center']!r}.")
    elif len(str(s.get("cost_center", ""))) > TAG_VALUE_LENGTH:
        problems.append(f"{at('cost_center')}: cost_center becomes a tag, so it is at most {TAG_VALUE_LENGTH} characters.")
    owner, archetype, subscription, location = (s.get(k) if isinstance(s.get(k), str) else None for k in ("owner", "archetype", "subscription", "location"))
    if owner and (not EMAIL.fullmatch(owner) or len(owner) > TAG_VALUE_LENGTH):
        problems.append(f"{at('owner')}: owner {owner!r} should be the owning team's email address.")
    archetypes = governance["archetypes"]
    if archetype and archetype not in archetypes:
        problems.append(f"{at('archetype')}: archetype {archetype!r} must be one of {', '.join(archetypes)}.")
    if subscription and not GUID.fullmatch(subscription.lower()):
        problems.append(f"{at('subscription')}: subscription must be a subscription ID.")
    if subscription and subscription.lower() in {v.lower() for v in governance["platform_subscriptions"].values()}:
        problems.append(f"{at('subscription')}: subscription {subscription} is a platform subscription.")
    if location and location not in governance["allowed_regions"]:
        problems.append(f"{at('location')}: location {location} is not one of the allowed regions ({', '.join(governance['allowed_regions'])}).")
    if location and location not in platform["regions"]:
        problems.append(f"{at('location')}: location {location} has no hub to connect to.")

    tags = {} if s.get("tags") is None else s["tags"]
    if not isinstance(tags, dict):
        problems.append(f"{at('tags')}: tags maps each extra tag to its value, for example BusinessUnit: Finance.")
        tags = {}
    if len(tags) > EXTRA_TAGS:
        problems.append(f"{where}: {len(tags)} extra tags; Azure allows {EXTRA_TAGS} beside the five mlz-core sets.")
    # Azure tag names ignore case: Owner and owner are one tag.
    from_settings = {t.lower(): setting for t, setting in TAG_SETTINGS.items()}
    automatic = {t.lower(): source for t, source in AUTOMATIC_TAGS.items()}
    seen = {}
    for tag, value in tags.items():
        name = tag.lower()
        if name in seen:
            problems.append(f"{at('tags', tag)}: tags {seen[name]} and {tag} are one tag to Azure, which ignores case; keep one.")
        seen[name] = tag
        if name in from_settings:
            problems.append(f"{at('tags', tag)}: tags.{tag} comes from {from_settings[name]}; set that instead.")
        elif name in automatic:
            problems.append(f"{at('tags', tag)}: tags.{tag} is set from {automatic[name]}; remove it here.")
        elif isinstance(value, (dict, list)) or value is None:
            problems.append(f"{at('tags', tag)}: tags.{tag} is one value, not {value!r}.")
        elif len(str(value)) > TAG_VALUE_LENGTH:
            problems.append(f"{at('tags', tag)}: tags.{tag} is longer than Azure's {TAG_VALUE_LENGTH} characters.")
        if not tag.strip() or len(tag) > TAG_NAME_LENGTH or TAG_NAME_REFUSED & set(tag):
            problems.append(f"{at('tags', tag)}: tag name {tag[:40]!r} must be 1 to {TAG_NAME_LENGTH} characters, without any of < > % & \\ ? /.")
    for tag in governance.get("required_tags") or []:
        if tag.lower() not in from_settings | automatic | seen:
            problems.append(f"{where}: governance requires the {tag} tag; set it under tags: here or in a layer above.")

    budget = section(lz, "budget", problems)
    monthly = budget.get("monthly")
    if monthly is not None and (type(monthly) is not int or monthly <= 0):
        problems.append(f"{at('budget', 'monthly')}: budget.monthly is a whole amount, for example 500.")
    contacts = budget.get("contacts")
    if contacts is not None and (not isinstance(contacts, list) or not all(isinstance(c, str) and EMAIL.fullmatch(c) for c in contacts)):
        problems.append(f"{at('budget', 'contacts')}: budget.contacts is a list of email addresses, for example [team@example.com].")

    github = section(lz, "github", problems)
    organization, repo = github.get("organization"), github.get("repo")
    if organization is not None and not (isinstance(organization, str) and GITHUB_ORGANIZATION.fullmatch(organization)):
        problems.append(f"{at('github', 'organization')}: github.organization {organization!r} is not a GitHub organization name.")
    if repo is not None and not (isinstance(repo, str) and GITHUB_REPO.fullmatch(repo)):
        problems.append(f"{at('github', 'repo')}: github.repo {repo!r} is a repository name in the organization, for example payments-infra.")
    if repo is not None and organization is None:
        problems.append(f"{at('github', 'repo')}: github.repo needs github.organization (set it once in _defaults/all.yaml).")
    create, team = github.get("create"), github.get("team")
    if create is not None and not isinstance(create, bool):
        problems.append(f"{at('github', 'create')}: github.create is true or false, without quotes.")
    elif create is True and repo is not None:
        problems.append(f"{at('github', 'create')}: github.repo names an existing repo, which vending never creates; "
                        "leave out github.repo to have vending create one, or github.create to use that one.")
    if team is not None and not (isinstance(team, str) and GITHUB_TEAM.fullmatch(team)):
        problems.append(f"{at('github', 'team')}: github.team {team!r} is a GitHub team's slug, as in its URL, for example payments-devs.")


def wanted_network(lz: dict, problems: list[str]) -> tuple[int, dict[str, int]] | None:
    """The network the landing zone asks for: its prefix length and {subnet: prefix length}.
    None when it is not valid; the problems say why."""
    before = len(problems)
    network = section(lz, "network", problems, also={"address_space"})
    if "address_space" in network:
        problems.append(f"{origin(lz, 'network', 'address_space')}: network.address_space is written by the IP allocator to {IP_PLAN}; remove it here.")
    size = network.get("size", DEFAULT_SIZE)
    if type(size) is not int or not 16 <= size <= 28:
        problems.append(f"{origin(lz, 'network', 'size')}: network.size is a prefix length from 16 to 28, for example 24.")
        size = None
    subnets = network.get("subnets")
    if subnets is None:
        subnets = {"default": max(26, size or DEFAULT_SIZE)}  # a quarter of a /24, or all of a smaller range
    elif not isinstance(subnets, dict) or not subnets:
        problems.append(f"{origin(lz, 'network', 'subnets')}: network.subnets maps each subnet's name to its prefix length, for example app: 26.")
        subnets = {}
    for name, length in subnets.items():
        at = origin(lz, "network", "subnets", name)
        if not SUBNET_NAME.fullmatch(str(name)):
            problems.append(f"{at}: subnet name {name!r} is up to 20 lower-case letters, digits and hyphens.")
        if type(length) is not int or not 8 <= length <= 29:
            problems.append(f"{at}: network.subnets.{name} is a prefix length up to 29 (Azure's smallest subnet), for example 26.")
        elif size and length < size:
            problems.append(f"{at}: network.subnets.{name} is a /{length}, larger than the landing zone's /{size}.")
    if len(problems) > before:
        return None
    if sum(2 ** (32 - length) for length in subnets.values()) > 2 ** (32 - size):
        problems.append(f"{lz['where']}: the subnets need more addresses than a /{size} has.")
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


def platform_ranges(platform: dict) -> list[IPv4Network]:
    """Every region's platform range. A contract published before v0.3.0 has only the hub's own range, so
    a vending repo upgraded before the platform's next contract still reads it."""
    return [IPv4Network(r) for region in platform["regions"].values()
            for r in ([region["platform_address_space"]] if "platform_address_space" in region else region.get("hub_range") or [])]


def plan_problems(plan: dict, zones: dict, wanted: dict, platform: dict) -> list[str]:
    """What no longer holds for ranges and subnets already given. Nothing given is ever moved, so a
    conflict is reported for a person to resolve."""
    problems = []
    platforms = platform_ranges(platform)
    reserved = [IPv4Network(r) for region in platform["regions"].values() for r in region.get("reserved") or []]
    keys = sorted(plan)
    for index, key in enumerate(keys):
        given = plan[key]["range"]
        problems += [f"{IP_PLAN}: {key}'s range {given} overlaps the platform's range {p} (the hub, management and identity)." for p in platforms if given.overlaps(p)]
        problems += [f"{IP_PLAN}: {key}'s range {given} overlaps {r}, which the platform reserves." for r in reserved if given.overlaps(r)]
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
        if wanted.get(key) is not None:
            problems += size_problems(key, plan[key], wanted[key])
    return problems


def size_problems(key: str, entry: dict, wanted: tuple[int, dict[str, int]]) -> list[str]:
    """A landing zone asking for another size than it was given: a range or a subnet never changes size."""
    size, subnets = wanted
    given, problems = entry["range"], []
    if given.prefixlen != size:
        problems.append(f"{key}: network.size is {size}, but its range {given} was given as a /{given.prefixlen}; a range never changes size.")
    for name, block in entry["subnets"].items():
        if name in subnets and subnets[name] != block.prefixlen:
            problems.append(f"{key}: subnet {name} is a /{subnets[name]} here but was given as {block}; a subnet never changes size, so give the new one another name.")
    return problems


def place(pools: list[IPv4Network], size: int, taken: list[IPv4Network]) -> IPv4Network | None:
    """The first block of this size in the pools that overlaps nothing taken. Walks the taken ranges in
    address order, jumping past each one, so its cost grows with what is taken, not with the pool."""
    step = 2 ** (32 - size)
    spans = sorted((int(t.network_address), int(t.broadcast_address)) for t in taken)
    for pool in pools:
        if pool.prefixlen > size:
            continue  # too small for one block
        cursor, last = int(pool.network_address), int(pool.broadcast_address)
        for low, high in spans:
            if high < cursor:
                continue
            if cursor + step - 1 < low or cursor > last:
                break  # the block at the cursor ends before this taken range begins
            cursor = (high // step + 1) * step  # the first aligned block after it
        if cursor + step - 1 <= last:
            return IPv4Network((cursor, size))
    return None


def allocate(repo: Path, platform: dict) -> tuple[dict[str, dict], list[str], list[str]]:
    """The IP plan with a range for every new landing zone and a block for every new subnet: the first
    free one, largest first. A subnet removed from the YAML keeps its block, under removed_subnets, until
    it is released; asked for again, it gets that block back. Returns (plan, what changed, landing zones
    removed but still holding a range). Writes nothing; fails with every problem when any landing zone's
    network is not valid."""
    zones, plan, problems = landing_zones(repo), ip_plan(repo), []
    wanted = {key: wanted_network(lz, problems) for key, lz in zones.items()}
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
        raise ConfigError("cannot allocate:\n  " + "\n  ".join(dict.fromkeys(problems)))

    taken = [entry["range"] for entry in plan.values()]
    taken += platform_ranges(platform) + [IPv4Network(r) for region in platform["regions"].values() for r in region.get("reserved") or []]
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
        raise ConfigError("cannot allocate:\n  " + "\n  ".join(dict.fromkeys(problems)))
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
        problems = []
        wanted = wanted_network(zones[key], problems)
        if wanted is None:
            raise ConfigError(f"fix {key}'s network before releasing anything:\n  " + "\n  ".join(problems))
        back = sorted(set(removed) & set(wanted[1]))
        if back:
            raise ConfigError(f"{key} asks for {', '.join(back)} again; run the IP allocator to give the block back instead of releasing it.")
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
        wanted[key] = wanted_network(lz, problems)
        if key not in plan:
            problems.append(f"{key}: no address range yet; run the IP allocator (python3 -m mlz allocate).")
        elif wanted[key] and set(plan[key]["subnets"]) != set(wanted[key][1]):
            problems.append(f"{key}: its subnets in {IP_PLAN} are not the ones asked for; run the IP allocator (python3 -m mlz allocate).")
    problems += plan_problems(plan, zones, wanted, platform)
    # One short name per workload: two workloads with the same one would read alike in every name.
    shorts = {}
    for lz in zones.values():
        if isinstance(short_name(lz), str):  # check() reports one that is not
            shorts.setdefault(short_name(lz), set()).add(lz["workload"])
    problems += [f"landing-zones: workloads {', '.join(sorted(w))} share the short name {k}; give each its own short_name." for k, w in sorted(shorts.items()) if len(w) > 1]
    # Nor one of the platform's: a landing zone named hub would name its network as the hub's is named.
    given = (governance.get("naming") or {}).get("platform") if isinstance(governance.get("naming"), dict) else None
    parts = {**naming.PLATFORM, **(given if isinstance(given, dict) else {})}
    problems += [f"landing-zones: workload{'s' if len(w) > 1 else ''} {', '.join(sorted(w))} {'have' if len(w) > 1 else 'has'} the short name {k}, "
                 f"which names the platform's {part} (customer.yaml's naming.platform); give {'each' if len(w) > 1 else 'it'} another short_name."
                 for k, w in sorted(shorts.items()) for part, code in parts.items() if code == k]
    # Names as recorded when first given, under any the landing zone's file sets: the template is not read here,
    # so a changed one renames nothing until the IP allocator records it for a landing zone (names --renew).
    recorded, names = recorded_names(repo), {}
    for key, lz in zones.items():
        if key not in plan:
            continue
        if key not in recorded:
            problems.append(f"{key}: no names yet; run the IP allocator (python3 -m mlz allocate).")
            continue
        names[key], _ = landing_zone_names(key, lz, sorted(plan[key]["subnets"]), recorded[key], None, problems)
    subscriptions = [str(z["settings"].get("subscription", "")).lower() for z in zones.values()]
    problems += [f"subscription {s} is used by more than one landing zone." for s in sorted({s for s in subscriptions if s and subscriptions.count(s) > 1})]
    if problems:
        raise ConfigError("landing zone settings are not valid:\n  " + "\n  ".join(dict.fromkeys(problems)))

    configs = {}
    for key, lz in zones.items():
        s = lz["settings"]
        region = platform["regions"][s["location"]]
        budget, github = s.get("budget") or {}, s.get("github") or {}
        configs[key] = {
            "key": key,
            "root_id": governance["root"],
            "subscription_id": s["subscription"].lower(),
            # Decommissioned: ALZ's Decommissioned group, where its policies deny new resources.
            "management_group_id": f"{governance['root']}-decommissioned" if decommissioned(lz) else governance["archetypes"][s["archetype"]],
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
            "github": github_config(lz, names[key]),
            "decommission": decommissioned(lz),
            "names": names[key],
        }
    return configs


def short_name_problems(lz: dict) -> list[str]:
    """What is wrong with the workload's short name: one it sets, or the workload's own name when it sets none."""
    s = lz["settings"]
    if "short_name" in s and not (isinstance(s["short_name"], str) and naming.SHORT.fullmatch(s["short_name"]) and 2 <= len(s["short_name"]) <= 5):
        return [f"{origin(lz, 'short_name')}: short_name is {s['short_name']!r}; it must be 2 to 5 lower-case letters and digits."]
    if "short_name" not in s and not (naming.SHORT.fullmatch(lz["workload"]) and 2 <= len(lz["workload"]) <= 5):
        return [f"landing-zones/{lz['workload']}/_workload.yaml: short_name is needed, since the workload's name {lz['workload']!r} "
                "is not 2 to 5 lower-case letters and digits; it goes in every name of the workload's landing zones (docs/naming.md)."]
    return []


def creates_repo(lz: dict) -> bool:
    """Whether vending makes the landing zone's workload repo: it does for every landing zone once github.organization
    is set, unless github.repo names an existing one, or github.create is false."""
    github = lz["settings"].get("github")
    return isinstance(github, dict) and bool(github.get("organization")) and github.get("repo") is None and github.get("create") is not False


def github_config(lz: dict, names: dict) -> dict | None:
    """The landing zone's repo for the root: the one vending makes, or the existing one github.repo names; else None."""
    github = lz["settings"].get("github") or {}
    if not github.get("organization") or (github.get("repo") is None and not creates_repo(lz)):
        return None
    return {"organization": github["organization"], "repo": github.get("repo") or names[REPOSITORY], "create": creates_repo(lz), "team": github.get("team")}


def short_name(lz: dict) -> str:
    """The workload's short name in names: short_name, or the workload's own name when it fits."""
    return lz["settings"].get("short_name") or lz["workload"]


def landing_zone_names(key: str, lz: dict, subnets: list[str], kept: dict, settings: naming.Naming | None,
                       problems: list[str]) -> tuple[dict, dict]:
    """Every name vending gives in the landing zone, and the record's entry for it (_generated/names.yaml).
    A name under names: in its file wins; then the one kept in the record; else, given settings, the template's.
    The entry holds the template's names, kept or new, for whatever has no name of its own. Subnets and peerings,
    named inside their network, keep their own pattern (docs/naming.md)."""
    where, s = lz["where"], lz["settings"]
    explicit = s.get("names")
    if explicit is not None and not isinstance(explicit, dict):
        problems.append(f"{where}: names maps each name to set ({', '.join([*NAMED, 'network_security_groups'])}) to the name.")
        explicit = {}
    explicit = explicit or {}
    problems += [f"{where}: names.{k} is not a name vending gives; use {', '.join([*NAMED, 'network_security_groups'])}." for k in sorted(set(explicit) - set(NAMED) - {"network_security_groups"}, key=str)]
    nsgs = explicit.get("network_security_groups")
    nsgs = {} if nsgs is None else nsgs  # only absent or empty means none: false or [] is reported
    if not isinstance(nsgs, dict):
        problems.append(f"{where}: names.network_security_groups maps a subnet to its NSG's name.")
        nsgs = {}
    problems += [f"{where}: names.network_security_groups.{k} is not one of its subnets ({', '.join(subnets)})." for k in sorted(set(nsgs) - set(subnets), key=str)]
    app, env = short_name(lz), lz["environment"]
    # check() says what the short name or the location must be: no name is right without them.
    fits = isinstance(app, str) and naming.SHORT.fullmatch(app) is not None and 2 <= len(app) <= 5 and isinstance(s.get("location"), str)
    template = settings is not None and fits
    entry: dict = {}

    def one(item: str, kind: str, purpose: str | None, given: dict, held: dict) -> str | None:
        try:
            if item in given:
                if not isinstance(given[item], str):
                    raise ConfigError(f"{given[item]!r} is not a name")
                return naming.check(kind, given[item])
            if item in held:
                return held[item]
            if template and env not in settings.environments:  # once, however many names need it
                problems.append(f"{where}: the environment {env!r} has no short name for names; add it under naming.environments in customer.yaml.")
            elif template and kind == REPOSITORY:  # a GitHub repo: long names, from naming.repository
                return naming.repository_name(settings, lz["workload"], env)
            elif template:
                return naming.render(settings, kind, app=app, env=env, region=s["location"], purpose=purpose)
            elif settings is None and fits:
                problems.append(f"{key}: {NAMES} has no name for {item} yet; run the IP allocator (python3 -m mlz allocate).")
            elif settings is not None:  # the template needs both; check() says the same, so the messages are one
                problems.extend(short_name_problems(lz) or [f"{where}: location is required (set it here or in a layer above)."])
            return None
        except ConfigError as error:
            problems.append(f"{where}: {error}")
            return None

    names = {}
    for item, (kind, purpose) in NAMED.items():
        names[item] = one(item, kind, purpose, explicit, kept)
        if item not in explicit and names[item] is not None:
            entry[item] = names[item]
    # The workload repo, when vending makes it (an existing one, github.repo, is the landing zone's own to name).
    if creates_repo(lz):
        names[REPOSITORY] = one(REPOSITORY, REPOSITORY, None, {}, kept)
        if names[REPOSITORY] is not None:
            entry[REPOSITORY] = names[REPOSITORY]
    held = kept.get("network_security_groups") or {}
    names["network_security_groups"] = {}
    for subnet in subnets:
        names["network_security_groups"][subnet] = one(subnet, "network_security_group", subnet, nsgs, held)
        if subnet not in nsgs and names["network_security_groups"][subnet] is not None:
            entry.setdefault("network_security_groups", {})[subnet] = names["network_security_groups"][subnet]
    # Two of one type side by side need names apart, which a template without {purpose} would not give: both
    # resource groups in one subscription, or every NSG in one resource group. Azure ignores case in both.
    for what, items in (("resource groups", [(n, names[n]) for n in ("network_resource_group", "identity_resource_group", "state_resource_group")]),
                        ("identities", [(n, names[n]) for n in ("deploy_identity", "plan_identity")]),
                        ("NSGs", [(f"network_security_groups.{s}", v) for s, v in names["network_security_groups"].items()])):
        seen: dict[str, str] = {}
        for item, value in items:
            if value is not None and value.lower() in seen:
                problems.append(f"{where}: {seen[value.lower()]} and {item} would both be {value}, and Azure needs its {what} named apart; "
                                "put {purpose} in the template, or set one under names:.")
            elif value is not None:
                seen[value.lower()] = item
    names["subnet"] = "snet-%s"
    names["peering_to_hub"], names["peering_from_hub"] = f"peer-{key}-to-hub", f"peer-hub-to-{key}"
    return names, entry


def recorded_names(repo: Path, source: Path | None = None) -> dict[str, dict]:
    """_generated/names.yaml, or another copy of it (source): {key: {name: value, network_security_groups: {subnet:
    value}}}, each checked."""
    recorded = (read_yaml(source) if source else read_setting(repo / NAMES.removesuffix(".yaml"))) or {}
    parsed = {}
    for key, entry in recorded.items():
        try:
            if not isinstance(entry, dict) or set(entry) - set(NAMED) - {REPOSITORY, "network_security_groups"}:
                raise ValueError  # the allocator writes {} for a landing zone without names, never null or a list
            # In the order the allocator writes them, whatever order the file was read in.
            parsed[str(key)] = {item: naming.check(kind, entry[item]) for item, (kind, _) in NAMED.items() if item in entry}
            if REPOSITORY in entry:
                if not (isinstance(entry[REPOSITORY], str) and naming.GITHUB_REPOSITORY.fullmatch(entry[REPOSITORY])):
                    raise ValueError
                parsed[str(key)][REPOSITORY] = entry[REPOSITORY]
            if "network_security_groups" in entry:
                if not isinstance(entry["network_security_groups"], dict) or not entry["network_security_groups"]:
                    raise ValueError  # the allocator leaves the key out rather than write it empty
                parsed[str(key)]["network_security_groups"] = {str(subnet): naming.check("network_security_group", value)
                                                                for subnet, value in sorted(entry["network_security_groups"].items())}
        except (AttributeError, TypeError, ValueError, ConfigError):
            raise ConfigError(f"{NAMES}: {key} is not as the IP allocator writes it; restore the file from git.") from None
    return parsed


def flat(entry: dict) -> dict[str, str]:
    """A record's entry as one level: network_security_groups.<subnet> for each NSG."""
    return {**{k: v for k, v in entry.items() if k != "network_security_groups"},
            **{f"network_security_groups.{s}": v for s, v in (entry.get("network_security_groups") or {}).items()}}


def give_names(repo: Path, plan: dict[str, dict], governance: dict, renew: set[str] = frozenset(),
               base: dict[str, dict] | None = None) -> tuple[dict[str, dict], list[str]]:
    """The names record for every landing zone with a range in plan: what it had, and the template's name for anything
    new. A landing zone in renew gets the template's names again for everything it has now. base is the record on a pull request's
    base branch: a name is kept only once it is there, where it can be deployed from, so one this pull request gave
    on an earlier run follows its YAML as it is now. Returns (record, what changed); fails with every problem when a
    name cannot be given."""
    zones, recorded = landing_zones(repo), recorded_names(repo)
    for key in sorted(renew):
        if key not in zones:
            raise ConfigError(f"no landing zone {key}; the landing zones are {', '.join(sorted(zones)) or 'none yet'}.")
        if key not in plan:
            raise ConfigError(f"{key} has no address range yet, so no names to renew; the IP allocator gives both on its pull request.")
    try:
        # customer.yaml's naming, from governance's contract (none in one from before v0.3.8: the defaults).
        settings = naming.settings({"naming": governance.get("naming") or None}, governance["root"])
    except ConfigError as error:
        raise ConfigError(f"_generated/governance.yaml: {error}") from None
    def kept(key: str) -> dict:
        entry = recorded.get(key, {})
        if key in renew:  # what it has now gets the template's names; a removed subnet's NSG keeps its own while held
            removed = plan[key]["removed_subnets"]
            entry = {"network_security_groups": {subnet: name for subnet, name in (entry.get("network_security_groups") or {}).items() if subnet in removed}}
        if base is None:
            return entry
        on_base = base.get(key, {})
        return {**{item: value for item, value in entry.items() if item in NAMED and item in on_base},
                "network_security_groups": {subnet: value for subnet, value in (entry.get("network_security_groups") or {}).items()
                                            if subnet in (on_base.get("network_security_groups") or {})}}

    # A removed landing zone or subnet keeps its names while it holds its addresses: asked for again, before it
    # is released, it is the same resource, under the same name.
    held = held_names({key: kept(key) for key in recorded}, plan)
    record, changes, problems = {}, [], []
    for key in sorted(plan):
        if key not in zones:
            if key in held:
                record[key] = held[key]
            continue
        before = flat(recorded.get(key, {}))
        _, record[key] = landing_zone_names(key, zones[key], sorted(plan[key]["subnets"]), held.get(key, {}), settings, problems)
        removed = {subnet: name for subnet, name in (held.get(key, {}).get("network_security_groups") or {}).items()
                   if subnet in plan[key]["removed_subnets"]}
        if removed:
            record[key]["network_security_groups"] = dict(sorted({**record[key].get("network_security_groups", {}), **removed}.items()))
        changes += [f"{key}: {item} {value}" if item not in before else f"{key}: {item} {before[item]}, now {value}"
                    for item, value in flat(record[key]).items() if before.get(item) != value]
    if problems:
        raise ConfigError("cannot name:\n  " + "\n  ".join(dict.fromkeys(problems)))
    return record, changes


def held_names(record: dict[str, dict], plan: dict[str, dict]) -> dict[str, dict]:
    """The record without the names whose addresses are released: a landing zone's while plan holds its range, a
    subnet's NSG while plan holds its block, given or removed."""
    held = {}
    for key, entry in record.items():
        if key not in plan:
            continue
        blocks = set(plan[key]["subnets"]) | set(plan[key]["removed_subnets"])
        held[key] = {item: value for item, value in entry.items() if item != "network_security_groups"}
        nsgs = {subnet: value for subnet, value in (entry.get("network_security_groups") or {}).items() if subnet in blocks}
        if nsgs:
            held[key]["network_security_groups"] = nsgs
    return held


def render_names(record: dict[str, dict]) -> str:
    return dump(record, NAMES_HEADER, spaced=True)


def decommissioned(lz: dict) -> bool:
    """decommission: true in the landing zone's file: its plan destroys everything vending made and moves the
    subscription to Decommissioned. Its range stays held until it is released."""
    return lz["settings"].get("decommission") is True


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
