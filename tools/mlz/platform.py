"""The platform role: hubs/<region>.yaml (a file or a folder of chunks) into the platform root's config.

A hub file says what the region's platform should have; everything else is worked out here. Its
platform_address_space, a /21, is laid out by fixed offsets, so every region looks the same, a network
turned on later never moves another, and nobody types a subnet:

  first /22             the hub
    first /26             AzureFirewallSubnet
    second /26            AzureFirewallManagementSubnet (Basic firewall only)
    next /27              GatewaySubnet
    second /24, /26       AzureBastionSubnet
    then two /28s         DNS resolver inbound and outbound
    the rest              free for later
  fifth /24             management's network, in the primary region
    first /25             GitHub-hosted runners (delegated to GitHub where it supports the region)
    next /26              self-hosted runners
    next /27              private endpoints
    last /27              free for later
  sixth /24             identity's network, where the hub file says identity: true
    first /26             domain controllers
  last /23              spare, held for the platform

No landing zone is given an address in it, and the other regions route all of it to this region's firewall.
Management's and identity's networks are spokes of the hub, as landing zones are: everything leaves through
the hub's firewall, and they resolve names through the hub.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

from . import names, naming
from .files import preload, read_setting, setting_names, yaml_files
from .merge import ConfigError
from .naming import REGION_CODES

FIREWALL_TIERS = ("Standard", "Premium", "Basic")
HUB_KEYS = {"platform_address_space", "landing_zone_ranges", "reserved", "firewall", "dns_resolver", "bastion", "identity", "names"}
# The size of a region's platform_address_space, and the hub's share of it: its first /22.
PLATFORM_PREFIX, HUB_PREFIX = 21, 22
# Management's and identity's /24 in it, by offset, and each one's subnets, by offset and size.
NETWORKS = {
    "management": (1024, {"github-runners": (0, 25), "runners": (128, 26), "private-endpoints": (192, 27)}),
    "identity": (1280, {"domain-controllers": (0, 26)}),
}
# The regions mlz-core knows: every Azure region, with the code in its names (tools/mlz/naming.py).
REGIONS = tuple(REGION_CODES)
# Where GitHub's hosted runners can join a subnet of the customer's (GitHub Docs, "About Azure private
# networking for GitHub-hosted runners", supported regions, read 2026-11-13). Elsewhere management's network
# keeps the subnet, undelegated, for self-hosted runners.
GITHUB_RUNNER_REGIONS = (
    "australiaeast", "brazilsouth", "canadacentral", "canadaeast", "centralus", "eastasia", "eastus", "eastus2",
    "francecentral", "japanwest", "koreacentral", "northcentralus", "norwayeast", "southcentralus", "southeastasia",
    "southindia", "swedencentral", "switzerlandnorth", "uksouth", "ukwest", "westus", "westus2", "westus3",
)
# Ranges Azure refuses in a virtual network: multicast, broadcast, loopback, link-local, its own DNS.
AZURE_RESERVED = tuple(ipaddress.ip_network(r) for r in ("224.0.0.0/4", "255.255.255.255/32", "127.0.0.0/8", "169.254.0.0/16", "168.63.129.16/32"))


def subnets(space: ipaddress.IPv4Network) -> dict[str, str]:
    """The hub's fixed layout, in its /22."""

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
    unknown = sorted(set(settings) - HUB_KEYS - {"address_space"})
    problems += [f"{where}: {key!r} is not a hub setting; use {', '.join(sorted(HUB_KEYS))}." for key in unknown]
    if "address_space" in settings:
        problems.append(moved(where, settings["address_space"]))
    if region not in REGIONS:
        problems.append(f"{where}: {region!r} is not an Azure region (modules/naming/region-codes.json names them, for example norwayeast).")

    # Settings that do not depend on the address space first, so every problem is reported at once.
    ranges = cidrs(settings.get("landing_zone_ranges", []), f"{where}: landing_zone_ranges", problems)
    if not ranges:
        problems.append(f"{where}: landing_zone_ranges is required: where landing zone networks get their ranges.")
    reserved = cidrs(settings.get("reserved", []), f"{where}: reserved", problems)
    tier = settings.get("firewall", "Standard")
    if tier not in FIREWALL_TIERS:
        problems.append(f"{where}: firewall {tier!r} must be one of {', '.join(FIREWALL_TIERS)}.")
    for flag in ("dns_resolver", "bastion", "identity"):
        if flag in settings and not isinstance(settings[flag], bool):
            problems.append(f"{where}: {flag} must be true or false, without quotes.")
    # The hub's own names, for a component to keep one the template would not give it (tools/mlz/names.py).
    explicit = names.hub_explicit(where, settings.get("names"), problems)

    value = settings.get("platform_address_space")
    if value in (None, "", []):
        if "address_space" not in settings:  # else the problem above says what to write
            problems.append(f"{where}: platform_address_space is required, a /21 such as 10.100.0.0/21.")
        return {}
    if isinstance(value, list):
        problems.append(f"{where}: platform_address_space is one range, not a list.")
        return {}
    space = cidrs(value, f"{where}: platform_address_space", problems)
    if not space:
        return {}
    space = space[0]
    if space.prefixlen != PLATFORM_PREFIX:
        problems.append(f"{where}: platform_address_space {space} must be a /21: the hub's /22, management's /24, identity's /24 and a spare /23.")
        return {}

    hub_space = next(space.subnets(new_prefix=HUB_PREFIX))
    layout = subnets(hub_space)
    return {
        "location": region,
        "platform_address_space": str(space),
        "address_space": [str(hub_space)],
        "landing_zone_ranges": [str(r) for r in ranges],
        # What the other hubs send to this one's firewall: the platform's range, unless a landing zone
        # range already holds it, and the landing zone ranges.
        "routing_supernet": [str(r) for r in ([] if any(space.subnet_of(r) for r in ranges) else [space]) + ranges],
        "reserved": [str(r) for r in reserved],
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
        "identity": settings.get("identity", False) is True,
        "names": explicit,  # build() fills in the template's for the rest
    }


def spoke(hub: dict, network: str) -> dict:
    """Management's or identity's network in a hub's region. As the hub module has it: the firewall takes
    its subnet's fifth address, and spokes resolve through its DNS proxy, or with a Basic firewall (no
    proxy) through the resolver's inbound endpoint, which also takes its subnet's fifth address."""
    offset, carve = NETWORKS[network]
    base = ipaddress.ip_network(hub["platform_address_space"]).network_address + offset
    firewall = str(ipaddress.ip_network(hub["firewall"]["subnet_address_prefix"])[4])
    resolver = hub["dns_resolver"]
    if hub["firewall"]["sku_tier"] != "Basic":
        dns_servers = [firewall]
    else:
        dns_servers = [str(ipaddress.ip_network(resolver["inbound_subnet_address_prefix"])[4])] if resolver else []
    return {
        "address_space": f"{base}/24",
        "subnets": {name: f"{base + at}/{size}" for name, (at, size) in carve.items()},
        "dns_servers": dns_servers,
        "firewall": firewall,
    }


def moved(where: str, value) -> str:
    """What to write instead of address_space, the hub's own range before v0.3.0."""
    text = f"{where}: address_space is now platform_address_space, a /21 that holds the hub (its first /22), management and identity"
    try:
        hub = ipaddress.ip_network(str(value), strict=True)
    except ValueError:
        return f"{text}."
    if hub.version == 4 and hub.prefixlen == HUB_PREFIX and hub.network_address == hub.supernet(new_prefix=PLATFORM_PREFIX).network_address:
        return f"{text}; platform_address_space: {hub.supernet(new_prefix=PLATFORM_PREFIX)} keeps the hub at {hub}."
    return f"{text}; the hub at {value} is not the first /22 of a /21, so choose a free /21 for the platform."


def build(repo: Path, governance: dict, vending: dict | None = None) -> dict:
    """The platform root's config from the platform repo, the governance contract and, once there, the
    vending contract."""
    problems: list[str] = []
    regions = setting_names(repo / "hubs")
    if not regions:
        raise ConfigError(f"{repo / 'hubs'}: no hubs; add hubs/<region>.yaml.")
    preload(yaml_files(repo / "hubs"))
    hubs = {region: hub(region, read_setting(repo / "hubs" / region) or {}, problems) for region in regions}

    primary = governance["primary_region"]
    if primary not in hubs:
        problems.append(f"hubs: no hub in the primary region {primary}; add hubs/{primary}.yaml.")
    spaces = [(r, ipaddress.ip_network(h["platform_address_space"])) for r, h in hubs.items() if h]
    for i, (a, net_a) in enumerate(spaces):
        for b, net_b in spaces[i + 1:]:
            if net_a.overlaps(net_b):
                problems.append(f"hubs/{a} and hubs/{b}: platform address spaces {net_a} and {net_b} overlap.")
    reserved = [(r, ipaddress.ip_network(n)) for r, h in hubs.items() if h for n in h["reserved"]]
    problems += [
        f"hubs/{a}: platform address space {net} overlaps {n}, which hubs/{b} reserves."
        for a, net in spaces for b, n in reserved if net.overlaps(n)
    ]
    # Each landing zone range belongs to one region: its firewall routes it, and the allocator fills it.
    pools = [(r, ipaddress.ip_network(p)) for r, h in hubs.items() if h for p in h["landing_zone_ranges"]]
    for i, (a, net_a) in enumerate(pools):
        for b, net_b in pools[i + 1:]:
            if net_a.overlaps(net_b):
                problems.append(f"hubs/{a}{'' if a == b else f' and hubs/{b}'}: landing zone ranges {net_a} and {net_b} overlap.")
    # So does each platform range: it may lie in its own region's landing zone ranges, never in another's,
    # whose firewall the other hubs route that range to.
    problems += [
        f"hubs/{a}: platform address space {net} overlaps {pool}, a landing zone range of hubs/{b}."
        for a, net in spaces for b, pool in pools if a != b and net.overlaps(pool)
    ]
    try:
        # customer.yaml's naming, from governance's contract (none in one from before v0.3.8: the defaults).
        settings = naming.settings({"naming": governance.get("naming") or None}, governance["root"])
    except ConfigError as error:
        problems.append(f"_generated/governance.yaml: {error}")
    subscriptions = governance["platform_subscriptions"]
    problems += [
        f"hubs/{region}: identity: true builds identity's network in the identity subscription, and customer.yaml names none; add subscriptions.identity in the governance repo."
        for region, h in hubs.items() if h and h["identity"] and not subscriptions.get("identity")
    ]
    if problems:
        raise ConfigError("platform settings are not valid:\n  " + "\n  ".join(problems))

    # Built by the management and identity stacks: management in the primary region, identity where asked.
    networks = {
        "management": {primary: {**spoke(hubs[primary], "management"), "github_runners": primary in GITHUB_RUNNER_REGIONS}},
        "identity": {region: spoke(h, "identity") for region, h in hubs.items() if h["identity"]},
    }
    # A landing zone given its range before v0.3.0 may lie where one of these is built now, even one removed
    # but not yet released, whose network may still exist; never build over either.
    zones = [(f"{key}'s range {zone['range']}", ipaddress.ip_network(zone["range"])) for key, zone in ((vending or {}).get("landing_zones") or {}).items()]
    zones += [(f"{key}'s range {held}, removed but not yet released", ipaddress.ip_network(held)) for key, held in ((vending or {}).get("held") or {}).items()]
    problems += [
        f"hubs/{region}: {name}'s network {space} would overlap {what} (_generated/vending.yaml), given before the "
        f"platform's range held it; move that landing zone (or release it), or give the region a platform_address_space clear of it."
        for name, regions in networks.items() for region, net in regions.items()
        for space in [ipaddress.ip_network(net["address_space"])] for what, taken in zones if space.overlaps(taken)
    ]
    if problems:
        raise ConfigError("platform settings are not valid:\n  " + "\n  ".join(problems))

    # Every name the platform's roots give, from the customer's template, worked out on every run (tools/mlz/names.py).
    for region, settings_ in hubs.items():
        settings_["is_primary"] = region == primary
        settings_["names"] = named(lambda: names.hub(settings, region, settings_["names"]), problems)
        # The resolver's and Bastion's objects as the hub module takes them, names in: passed as they are, they keep
        # one type with or without the component (a null merged in Terraform would not).
        if settings_["names"] and settings_["dns_resolver"]:
            settings_["dns_resolver"].update({"name": settings_["names"]["dns_resolver"],
                                              "inbound_endpoint_name": settings_["names"]["dns_resolver_inbound_endpoint"],
                                              "outbound_endpoint_name": settings_["names"]["dns_resolver_outbound_endpoint"]})
        if settings_["names"] and settings_["bastion"]:
            settings_["bastion"].update({"name": settings_["names"]["bastion"], "public_ip_name": settings_["names"]["bastion_public_ip"]})
    for network, regions_ in networks.items():
        for region, net in regions_.items():
            net["names"] = named(lambda: names.network(settings, network, region, list(net["subnets"])), problems)
    # Two of a type in one place need names apart: the public IPs of the components a hub has, and the connectivity
    # subscription's groups, a hub's in each region and the DNS zones'.
    for region, settings_ in hubs.items():
        hub_names = settings_["names"]
        if hub_names:
            used = {"firewall_public_ip": hub_names["firewall_public_ip"]}
            if settings_["firewall"]["management_subnet_address_prefix"]:
                used["firewall_management_public_ip"] = hub_names["firewall_management_public_ip"]
            if settings_["bastion"]:
                used["bastion_public_ip"] = hub_names["bastion_public_ip"]
            named(lambda: names.apart(f"hubs/{region}'s public IP addresses", used), problems)
    # Each network's regions share one resource group: a template without {region} would give two regions one name.
    for network, regions_ in networks.items():
        named_ = {region: net["names"] for region, net in regions_.items() if net["names"]}
        for kind, what in (("virtual_network", "networks"), ("route_table", "route tables"), ("github_network_settings", "GitHub network settings")):
            named(lambda: names.apart(f"{network}'s {what} in its regions", {region: n[kind] for region, n in named_.items()}), problems)
        named(lambda: names.apart(f"{network}'s network security groups in its regions",
                                  {f"{region}/{subnet}": nsg for region, n in named_.items() for subnet, nsg in n["network_security_groups"].items()}), problems)
    named(lambda: names.apart("resource groups in the connectivity subscription", {
        **{f"hubs/{region}'s": h["names"]["resource_group"] for region, h in hubs.items() if h["names"]},
        "the DNS zones'": names.dns_resource_group(settings, primary)}), problems)
    shared = named(lambda: {
        "dns_resource_group": names.dns_resource_group(settings, primary),
        "network_resource_groups": {network: names.network_resource_group(settings, network, primary) for network in networks},
        "diagnostic_setting": f"to-{settings.org}-workspace",
    }, problems)
    if problems:
        raise ConfigError("platform settings are not valid:\n  " + "\n  ".join(dict.fromkeys(problems)))
    return {
        "root_id": governance["root"],
        "primary_region": primary,
        "connectivity_subscription_id": subscriptions["connectivity"],
        "management_subscription_id": subscriptions["management"],
        "identity_subscription_id": subscriptions.get("identity"),
        "workspace_resource_id": governance["monitoring"]["workspace"],
        "hubs": hubs,
        "networks": networks,
        "names": shared,
    }


def named(work, problems: list[str]) -> dict:
    """What work() names, or {} with its problem: a name Azure would not take."""
    try:
        return work()
    except ConfigError as error:
        problems.append(f"names: {error}")
        return {}
