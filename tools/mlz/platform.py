"""The platform role: hubs/<region>.yaml (a file or a folder of chunks) into the platform root's config.

A hub file says what the hub should have; everything else is worked out here. Its subnets are carved
from address_space by one fixed layout, so every hub looks the same and nobody types a subnet:

  first /26             AzureFirewallSubnet
  second /26            AzureFirewallManagementSubnet (Basic firewall only)
  next /27              GatewaySubnet
  second /24, /26       AzureBastionSubnet
  then two /28s         DNS resolver inbound and outbound
  the rest              free for later
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

from .files import read_setting, setting_names
from .merge import ConfigError

FIREWALL_TIERS = ("Standard", "Premium", "Basic")
HUB_KEYS = {"address_space", "landing_zone_ranges", "firewall", "dns_resolver", "bastion"}
REGIONS = ("norwayeast", "norwaywest", "westeurope", "northeurope", "swedencentral")
# Ranges Azure refuses in a virtual network: multicast, broadcast, loopback, link-local, its own DNS.
AZURE_RESERVED = tuple(ipaddress.ip_network(r) for r in ("224.0.0.0/4", "255.255.255.255/32", "127.0.0.0/8", "169.254.0.0/16", "168.63.129.16/32"))


def subnets(space: ipaddress.IPv4Network) -> dict[str, str]:
    """The fixed layout. Needs a /23 or larger."""

    def block(offset: int, prefix: int) -> str:
        return str(ipaddress.ip_network(f"{space.network_address + offset}/{prefix}"))

    return {
        "firewall": block(0, 26),
        "firewall_management": block(64, 26),
        "gateway": block(128, 27),
        "bastion": block(256, 26),
        "dns_inbound": block(320, 28),
        "dns_outbound": block(336, 28),
    }


def cidrs(value, name: str, problems: list[str]) -> list[ipaddress.IPv4Network]:
    items = value if isinstance(value, list) else [value]
    networks = []
    for item in items:
        try:
            network = ipaddress.ip_network(str(item), strict=True)
        except ValueError:
            problems.append(f"{name}: {item!r} is not an address range like 10.100.0.0/22.")
            continue
        if network.version != 4:
            problems.append(f"{name}: {item} is not IPv4.")
            continue
        reserved = next((r for r in AZURE_RESERVED if network.overlaps(r)), None)
        if reserved:
            problems.append(f"{name}: {network} overlaps {reserved}, which Azure does not allow in a virtual network.")
            continue
        networks.append(network)
    return networks


def hub(region: str, settings: dict, problems: list[str]) -> dict:
    where = f"hubs/{region}"
    unknown = sorted(set(settings) - HUB_KEYS)
    problems += [f"{where}: {key!r} is not a hub setting; use {', '.join(sorted(HUB_KEYS))}." for key in unknown]
    if region not in REGIONS:
        problems.append(f"{where}: {region!r} is not a region mlz-core supports yet ({', '.join(REGIONS)}).")

    # Settings that do not depend on the address space first, so every problem is reported at once.
    ranges = cidrs(settings.get("landing_zone_ranges", []), f"{where}: landing_zone_ranges", problems)
    if not ranges:
        problems.append(f"{where}: landing_zone_ranges is required: where landing zone networks get their ranges.")
    tier = settings.get("firewall", "Standard")
    if tier not in FIREWALL_TIERS:
        problems.append(f"{where}: firewall {tier!r} must be one of {', '.join(FIREWALL_TIERS)}.")
    for flag in ("dns_resolver", "bastion"):
        if flag in settings and not isinstance(settings[flag], bool):
            problems.append(f"{where}: {flag} must be true or false, without quotes.")

    value = settings.get("address_space")
    if value in (None, "", []):
        problems.append(f"{where}: address_space is required, for example 10.100.0.0/22.")
        return {}
    if isinstance(value, list):
        problems.append(f"{where}: address_space is one range, not a list.")
        return {}
    space = cidrs(value, f"{where}: address_space", problems)
    if not space:
        return {}
    space = space[0]
    if space.prefixlen > 23:
        problems.append(f"{where}: address_space {space} is too small; a hub needs a /23 or larger, a /22 is usual.")
        return {}

    layout = subnets(space)
    return {
        "location": region,
        "address_space": [str(space)],
        "landing_zone_ranges": [str(r) for r in ranges],
        "firewall": {
            "sku_tier": tier,
            "subnet_address_prefix": layout["firewall"],
            "management_subnet_address_prefix": layout["firewall_management"] if tier == "Basic" else None,
        },
        "dns_resolver": None if settings.get("dns_resolver", True) is False else {
            "inbound_subnet_address_prefix": layout["dns_inbound"],
            "outbound_subnet_address_prefix": layout["dns_outbound"],
        },
        "bastion": {"subnet_address_prefix": layout["bastion"]} if settings.get("bastion", False) else None,
        "gateway_subnet_address_prefix": layout["gateway"],
    }


def build(repo: Path, governance: dict) -> dict:
    """The platform root's config from the platform repo and the governance contract."""
    problems: list[str] = []
    regions = setting_names(repo / "hubs")
    if not regions:
        raise ConfigError(f"{repo / 'hubs'}: no hubs; add hubs/<region>.yaml.")
    hubs = {region: hub(region, read_setting(repo / "hubs" / region) or {}, problems) for region in regions}

    primary = governance["primary_region"]
    if primary not in hubs:
        problems.append(f"hubs: no hub in the primary region {primary}; add hubs/{primary}.yaml.")
    spaces = [(r, ipaddress.ip_network(h["address_space"][0])) for r, h in hubs.items() if h]
    for i, (a, net_a) in enumerate(spaces):
        for b, net_b in spaces[i + 1:]:
            if net_a.overlaps(net_b):
                problems.append(f"hubs/{a} and hubs/{b}: address spaces {net_a} and {net_b} overlap.")
    # Each landing zone range belongs to one region: its firewall routes it, and the allocator fills it.
    pools = [(r, ipaddress.ip_network(p)) for r, h in hubs.items() if h for p in h["landing_zone_ranges"]]
    for i, (a, net_a) in enumerate(pools):
        for b, net_b in pools[i + 1:]:
            if net_a.overlaps(net_b):
                problems.append(f"hubs/{a}{'' if a == b else f' and hubs/{b}'}: landing zone ranges {net_a} and {net_b} overlap.")
    if problems:
        raise ConfigError("platform settings are not valid:\n  " + "\n  ".join(problems))

    for region, settings in hubs.items():
        settings["is_primary"] = region == primary
    return {
        "root_id": governance["root"],
        "primary_region": primary,
        "connectivity_subscription_id": governance["platform_subscriptions"]["connectivity"],
        "workspace_resource_id": governance["monitoring"]["workspace"],
        "hubs": hubs,
    }
