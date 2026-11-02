"""The vending role: landing-zones/ into one checked config per landing zone.

Each landing zone is four layers, each saying only what differs from the one above:

  landing-zones/_defaults/all.yaml          every landing zone
  landing-zones/_defaults/<env>.yaml        every landing zone in that environment (optional)
  landing-zones/<workload>/_workload.yaml   one workload, all its environments
  landing-zones/<workload>/<env>.yaml       one landing zone: one subscription, one network, one state

Any of them can be a folder of chunks instead of one file. The landing zone's key is <workload>-<env>.
Address ranges are not typed by anyone: the IP allocator writes them to _generated/ip-plan.yaml.
"""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

from .files import read_setting, setting_names
from .merge import ConfigError, layer

ARCHETYPES = {"corp": "lz-corp", "online": "lz-online", "sandbox": "sandboxes"}
KEYS = {"name", "owner", "cost_center", "archetype", "location", "subscription", "budget", "network", "github", "tags"}
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,22}[a-z0-9])?$")
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
IP_PLAN = "_generated/ip-plan.yaml"


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
            merged = layer(defaults, read_setting(root / "_defaults" / env) or {})
            merged = layer(merged, workload_settings)
            merged = layer(merged, read_setting(folder / env) or {})
            found[f"{workload}-{env}"] = {"workload": workload, "environment": env, "settings": merged}
    return found


def ip_plan(repo: Path) -> dict[str, str]:
    plan = read_setting(repo / IP_PLAN.removesuffix(".yaml")) or {}
    return {str(k): str(v) for k, v in plan.items()}


def check(key: str, lz: dict, governance: dict, platform: dict, problems: list[str]) -> None:
    s, where = lz["settings"], f"landing-zones/{lz['workload']}/{lz['environment']}"
    if not NAME.match(lz["workload"]) or not NAME.match(lz["environment"]):
        problems.append(f"{where}: folder and file names are lower-case letters, digits and hyphens (the key {key!r} is used in names).")
    problems += [f"{where}: {k!r} is not a landing zone setting; use {', '.join(sorted(KEYS))}." for k in sorted(set(s) - KEYS)]
    for required in ("owner", "cost_center", "archetype", "subscription", "location"):
        if s.get(required) in (None, ""):
            problems.append(f"{where}: {required} is required (set it here or in a layer above).")
    if s.get("owner") and not EMAIL.match(str(s["owner"])):
        problems.append(f"{where}: owner {s['owner']!r} should be the owning team's email address.")
    if s.get("archetype") and s["archetype"] not in ARCHETYPES:
        problems.append(f"{where}: archetype {s['archetype']!r} must be one of {', '.join(ARCHETYPES)}.")
    if s.get("subscription") and not GUID.match(str(s["subscription"]).lower()):
        problems.append(f"{where}: subscription must be a subscription ID.")
    if s.get("subscription") and str(s["subscription"]).lower() in {v.lower() for v in governance["subscriptions"].values()}:
        problems.append(f"{where}: subscription {s['subscription']} is a platform subscription.")
    location = s.get("location")
    if location and location not in governance["allowed_regions"]:
        problems.append(f"{where}: location {location} is not one of the allowed regions.")
    if location and location not in platform["connectivity"]["regions"]:
        problems.append(f"{where}: location {location} has no hub to connect to.")
    budget = s.get("budget") or {}
    if not isinstance(budget.get("monthly", 0), int) or budget.get("monthly", 0) < 0:
        problems.append(f"{where}: budget.monthly must be a whole amount, for example 500.")
    for contact in budget.get("contacts") or []:
        if not EMAIL.match(str(contact)):
            problems.append(f"{where}: budget contact {contact!r} is not an email address.")
    network = s.get("network") or {}
    if not isinstance(network.get("size", 24), int) or not 16 <= network.get("size", 24) <= 28:
        problems.append(f"{where}: network.size is a prefix length from 16 to 28, for example 24.")
    if "address_space" in network:
        problems.append(f"{where}: network.address_space is written by the IP allocator to {IP_PLAN}; remove it here.")


def subnets(space: ipaddress.IPv4Network, wanted: dict) -> dict[str, str]:
    """The landing zone's subnets, carved from its range in name order: {name: prefix length}."""
    carved, cursor = {}, int(space.network_address)
    for name, size in sorted(wanted.items(), key=lambda item: (item[1], item[0])):
        block = ipaddress.ip_network(f"{ipaddress.ip_address(cursor)}/{size}")
        if block.network_address != ipaddress.ip_address(cursor) or not block.subnet_of(space):
            raise ConfigError(f"subnets {wanted} do not fit in {space}.")
        carved[name] = str(block)
        cursor = int(block.broadcast_address) + 1
    return carved


def build(repo: Path, governance: dict, platform: dict) -> dict[str, dict]:
    """Each landing zone's config, by key. Fails with every problem when anything is not valid."""
    zones, plan, problems = landing_zones(repo), ip_plan(repo), []
    for key, lz in zones.items():
        check(key, lz, governance, platform, problems)
        if key not in plan:
            problems.append(f"{key}: no address range yet; run the IP allocator (python3 -m mlz allocate).")
    subscriptions = [str(z["settings"].get("subscription", "")).lower() for z in zones.values()]
    problems += [f"subscription {s} is used by more than one landing zone." for s in sorted({s for s in subscriptions if s and subscriptions.count(s) > 1})]
    if problems:
        raise ConfigError("landing zone settings are not valid:\n  " + "\n  ".join(problems))

    configs = {}
    for key, lz in zones.items():
        s = lz["settings"]
        region = platform["connectivity"]["regions"][s["location"]]
        space = ipaddress.ip_network(plan[key])
        configs[key] = {
            "key": key,
            "root_id": governance["root_id"],
            "display_name": f"{s.get('name', lz['workload'])} ({lz['environment']})",
            "subscription_id": str(s["subscription"]).lower(),
            "management_group_id": f"{governance['root_id']}-{ARCHETYPES[s['archetype']]}",
            "location": s["location"],
            "tags": {
                **{str(k): str(v) for k, v in (s.get("tags") or {}).items()},
                "Owner": str(s["owner"]),
                "CostCenter": str(s["cost_center"]),
                "Environment": lz["environment"],
                "Workload": lz["workload"],
                "ManagedBy": "mlz-core",
            },
            "budget": {"monthly": (s.get("budget") or {}).get("monthly"), "contacts": (s.get("budget") or {}).get("contacts", [])},
            "network": {
                "address_space": str(space),
                "subnets": subnets(space, (s.get("network") or {}).get("subnets") or {"default": 26}),
                "hub_virtual_network_id": region["hub_virtual_network_id"],
                "dns_servers": region["dns_servers"],
                "routes": region["spoke_default_routes"],
            },
            "github": s.get("github"),
        }
    return configs


def allocate(repo: Path, platform: dict) -> dict[str, str]:
    """Gives every landing zone without a range the first free block of its size, and returns the new
    entries. Ranges never move once given; a landing zone that is removed frees its range."""
    zones, plan = landing_zones(repo), ip_plan(repo)
    taken = [ipaddress.ip_network(v) for v in plan.values()]
    for spaces in platform["hub_address_spaces"].values():
        taken += [ipaddress.ip_network(r) for r in spaces]
    new = {}
    for key in sorted(k for k in zones if k not in plan):
        s = zones[key]["settings"]
        region = platform["connectivity"]["regions"].get(s.get("location"))
        if region is None:
            raise ConfigError(f"{key}: location {s.get('location')!r} has no hub, so it has no ranges to take from.")
        size = (s.get("network") or {}).get("size", 24)
        block = next(
            (b for supernet in region["routing_supernet"] for b in ipaddress.ip_network(supernet).subnets(new_prefix=size)
             if not any(b.overlaps(t) for t in taken)),
            None,
        )
        if block is None:
            raise ConfigError(f"{key}: no free /{size} left in {', '.join(region['routing_supernet'])}.")
        taken.append(block)
        new[key] = str(block)
    return new
