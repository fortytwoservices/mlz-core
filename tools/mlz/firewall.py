"""Firewall rules: firewall/ in the platform repo into one config for the rules root.

    firewall/platform.yaml                  rules for every landing zone (from all landing zone ranges)
    firewall/landing-zones/<key>.yaml       one landing zone's rules (from its own range)

A rule names what it reaches, never where it starts: the source is the landing zone's range, from the
vending contract (_generated/vending.yaml), or every landing zone range of the hub for platform rules.

    outbound:
      - name: card-processor
        to: [api.stripe.com]        # a name (lower case), *.name, an address or range, another landing zone,
                                    # an FQDN tag (WindowsUpdate) or a service tag (AzureMonitor, Storage.NorwayEast)
        ports: [https]              # https, http, a TCP port, or udp:<port>

The firewall policy holds a fixed set of rule collection groups, whatever the number of landing zones:
`platform` for the shared rules, and PARTITIONS groups for the landing zones. The `platform` group always
holds REQUIRED too: what governance's own policies install on every machine needs to reach. A landing zone's rules are
collections in group `lz-<nn>`, nn = sha256(key) mod PARTITIONS: the same group for ever, so adding one
landing zone never moves another's rules. Azure caps a policy's rule collection groups, so PARTITIONS
must never change once rules exist.
"""

from __future__ import annotations

import hashlib
import re
import sys
from ipaddress import IPv4Network
from pathlib import Path

from .files import read_setting, setting_names
from .merge import ConfigError

PARTITIONS = 30
PLATFORM_PRIORITY = 500
FIRST_PARTITION_PRIORITY = 1000
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$")
# A landing zone's key, <workload>-<env>: always a hyphen, never a dot, so never a name the FQDN pattern takes.
LANDING_ZONE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?-[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
FQDN = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE)
# Azure Firewall's FQDN tags, for application rules (learn.microsoft.com/azure/firewall/fqdn-tags), and
# Office365.<product>.<category>. Any other capitalised name is a service tag, for network rules.
FQDN_TAGS = {
    "AppServiceEnvironment", "AzureBackup", "AzureHDInsight", "AzureKubernetesService", "MicrosoftActiveProtectionService",
    "MicrosoftIntune", "Windows365", "WindowsDiagnostics", "WindowsUpdate", "WindowsVirtualDesktop", "citrixHdxPlusForWindows365",
}
# AzureMonitor, Storage.NorwayEast, and AzureCloud.norwaye: only the regional AzureCloud tags are lower case
# after the dot (learn.microsoft.com/azure/virtual-network/service-tags-overview), so Partner.com stays a name.
SERVICE_TAG = re.compile(r"^([A-Z][A-Za-z0-9]+(\.[A-Z][A-Za-z0-9]+)?|AzureCloud\.[a-z0-9]+)$")
WEB_PORTS = {"https": ("Https", 443), "http": ("Http", 80)}

# In every hub's platform group, whatever the customer writes: the Azure Monitor Agent, which governance's
# policies install on every machine, reaches both tags on 443 (learn.microsoft.com/azure/azure-monitor/
# agents/azure-monitor-agent-network-configuration). Without them, machines report nothing.
REQUIRED = [
    {
        "name": "azure-monitor-agent",
        "description": "mlz-core: the Azure Monitor Agent that governance's policies install.",
        "to": ["AzureMonitor", "AzureResourceManager"],
        "ports": ["https"],
    },
]


def partition(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % PARTITIONS


def read_rules(where: str, data: dict, problems: list[str]) -> list[dict]:
    """The rules worth checking further: every one with a string name and list targets and ports. Anything
    else is a problem, never an empty list, so a mistyped file cannot silently remove allow rules."""
    if not isinstance(data, dict):
        problems.append(f"{where}: the file holds outbound, a list of rules.")
        return []
    if set(data) - {"outbound"}:
        problems.append(f"{where}: {', '.join(sorted(map(str, set(data) - {'outbound'})))} is not a setting; rules go under outbound.")
    rules = data.get("outbound")
    if rules is None:  # absent or empty: no rules
        return []
    if not isinstance(rules, list):
        problems.append(f"{where}: outbound is a list of rules, each with name, to and ports.")
        return []
    names, kept = set(), []
    for index, rule in enumerate(rules):
        at = f"{where}: outbound[{index}]"
        if not isinstance(rule, dict) or set(rule) - {"name", "to", "ports", "description"} or not {"name", "to", "ports"} <= set(rule):
            problems.append(f"{at}: a rule has name, to and ports (and optionally description).")
            continue
        name = rule["name"]
        if not isinstance(name, str) or not NAME.fullmatch(name):
            problems.append(f"{at}: name {name!r} is lower-case letters, digits and hyphens.")
        elif name in names:
            problems.append(f"{at}: the name {name} is used twice in this file.")
        lists = True
        for field in ("to", "ports"):
            if not isinstance(rule[field], list) or not rule[field]:
                problems.append(f"{at}: {field} is a list, for example {'[api.example.com]' if field == 'to' else '[https]'}.")
                lists = False
        if isinstance(name, str):  # an invalid string name is reported above, and its targets still checked
            names.add(name)
            if lists:
                kept.append(rule)
    return kept


def shape(rule: dict, sources: list[str], at: str, zones: dict | None, tier: str, problems: list[str]) -> tuple[dict | None, dict[str, dict]]:
    """One rule as the firewall module takes it: an application rule for names and FQDN tags on https or
    http, and network rules for addresses, landing zones, service tags and other ports (one for TCP, one
    for UDP). Returns (application rule or None, {suffix: network rule})."""
    names, tags, addresses = [], [], []
    for target in rule["to"]:
        if not isinstance(target, str):  # YAML true or null would otherwise read as the tags True and None
            problems.append(f"{at}: {target!r} is not a name, an address or range, a landing zone or a tag.")
            continue
        if zones is None and LANDING_ZONE.fullmatch(target):
            continue  # a landing zone, by key, before vending's first contract: resolved once it exists
        if zones is not None and target in zones:
            addresses.append(zones[target]["range"])
        elif target in FQDN_TAGS or target.startswith("Office365."):
            tags.append(target)
        elif SERVICE_TAG.fullmatch(target):
            addresses.append(target)  # network rules take service tags as destination addresses
        elif FQDN.fullmatch(target):
            names.append(target.lower())
        else:
            try:
                addresses.append(str(IPv4Network(target)))
            except ValueError:
                problems.append(f"{at}: {target!r} is not a name, an address or range, a landing zone or a tag.")
    web, tcp, udp = set(), set(), set()
    for port in rule["ports"]:
        text = str(port).lower()
        if text in WEB_PORTS:
            web.add(WEB_PORTS[text])
        elif text.isdigit() and 1 <= int(text) <= 65535 and not isinstance(port, bool):
            tcp.add(text)
        elif text.startswith("udp:") and text[4:].isdigit() and 1 <= int(text[4:]) <= 65535:
            udp.add(text[4:])
        else:
            problems.append(f"{at}: port {port!r} is https, http, a TCP port (443) or udp:<port> (udp:53).")
    if tags and (tcp or udp or addresses):
        problems.append(f"{at}: an FQDN tag such as {tags[0]} takes only https and http; put other targets in a rule of their own.")
    # Names on ports written as numbers become network rules, which match names by DNS resolution: never a
    # wildcard, and not at all on a Basic firewall (learn.microsoft.com/azure/firewall/fqdn-filtering-network-rules,
    # features-by-sku). Both would only fail when Azure applies them.
    if names and (tcp or udp):
        wildcards = [name for name in names if name.startswith("*.")]
        if wildcards:
            problems.append(f"{at}: a wildcard name such as {wildcards[0]} takes only https and http; network rules cannot match wildcards.")
        if tier == "Basic":
            problems.append(f"{at}: a Basic firewall reaches names only on https and http; use addresses for other ports, or a Standard firewall.")
    description = rule.get("description")
    application = None
    if (names or tags) and web:
        application = {
            "description": description,
            "source_addresses": sources,
            "protocols": [{"type": kind, "port": number} for kind, number in sorted(web)],
            "destination_fqdns": sorted(names),
            "destination_fqdn_tags": sorted(tags),
        }
    # Addresses take every port, https and http as plain TCP. Names take only the ports written as numbers:
    # https and http reach a name through the application rule, which checks the name the client asks for,
    # never as TCP to whatever it resolves to. When the two port sets differ, they are two rules.
    by_address = tcp | {str(number) for _, number in web} if addresses else set()
    by_name = tcp if names else set()
    rules = []
    if by_address and by_address == by_name:
        rules.append(("", "TCP", addresses, names, tcp))
    else:
        if by_address:
            rules.append(("", "TCP", addresses, [], by_address))
        if by_name:
            rules.append(("-names" if by_address else "", "TCP", [], names, by_name))
    if udp and (addresses or names):
        rules.append(("-udp", "UDP", addresses, names, udp))
    network = {}
    for suffix, protocol, to_addresses, to_names, ports in rules:
        network[suffix] = {
            "description": description,
            "protocols": [protocol],
            "source_addresses": sources,
            "destination_addresses": sorted(set(to_addresses)),
            "destination_fqdns": sorted(set(to_names)),
            "destination_ports": sorted(ports, key=int),
        }
    return application, network


def collections(rules: list[dict], sources: list[str], where: str, owner: str, zones: dict | None, priority: int, tier: str, problems: list[str]) -> dict:
    """The owner's rules as at most two collections: <owner>-network and <owner>-application."""
    application, network, made_by = {}, {}, {}
    for index, rule in enumerate(rules):
        app, net = shape(rule, sources, f"{where}: outbound[{index}]", zones, tier, problems)
        if app:
            application[rule["name"]] = app
        for suffix, one in net.items():
            # A rule may make <name>, <name>-names and <name>-udp; another rule's name must not be one of those.
            key = rule["name"] + suffix
            if key in made_by:
                problems.append(f"{where}: rules {made_by[key]} and {rule['name']} both make a network rule named {key}; rename one.")
                continue
            made_by[key] = rule["name"]
            network[key] = one
    out = {"application": {}, "network": {}}
    if network:
        out["network"][f"{owner}-network"] = {"priority": priority, "action": "Allow", "rules": network}
    if application:
        out["application"][f"{owner}-application"] = {"priority": priority + 5, "action": "Allow", "rules": application}
    return out


def build(repo: Path, platform: dict, vending: dict | None) -> dict:
    """The rules root's config: per hub region, the rule collection groups and what is in them."""
    folder = repo / "firewall"
    problems: list[str] = []
    # None until vending publishes its first contract: then landing zones are not known yet (see below).
    zones = None if vending is None else (vending.get("landing_zones") or {})
    hubs = platform["hubs"]
    groups: dict[str, dict] = {region: {} for region in hubs}

    shared = read_setting(folder / "platform") or {}
    shared_rules = read_rules("firewall/platform", shared, problems)
    for region, hub in hubs.items():
        tier = (hub.get("firewall") or {}).get("sku_tier", "Standard")
        required = collections(REQUIRED, hub["landing_zone_ranges"], "mlz-core", "required", zones, 100, tier, problems)
        found = collections(shared_rules, hub["landing_zone_ranges"], "firewall/platform", "platform", zones, 200, tier, problems)
        groups[region]["platform"] = {
            "priority": PLATFORM_PRIORITY,
            "application": {**required["application"], **found["application"]},
            "network": {**required["network"], **found["network"]},
        }

    keys = setting_names(folder / "landing-zones")
    if zones is None:
        # The platform's first deployment comes before any landing zone, and vending needs the platform's
        # contract first, so refusing these files would stop the hub for good (met rebuilding the canary,
        # 2026-11-11). They are checked like any other, with landing zones by key left unresolved, and go into
        # no group: check.sh leaves the rules stack out of the run until vending's contract exists, so nothing
        # in Azure changes, not even when the contract has gone missing from a platform already deployed.
        for key in keys:
            where = f"firewall/landing-zones/{key}"
            rules = read_rules(where, read_setting(folder / "landing-zones" / key) or {}, problems)
            collections(rules, ["0.0.0.0/32"], where, key, None, 100, "Standard", problems)
        if keys and not problems:
            print(f"mlz firewall: no vending contract yet (_generated/vending.yaml): the rules for {', '.join(keys)} are checked, and wait for it", file=sys.stderr)
        keys = []
    for key in keys:
        where = f"firewall/landing-zones/{key}"
        if key not in zones:
            problems.append(f"{where}: no landing zone {key} in the vending contract (_generated/vending.yaml).")
            continue
        region = zones[key]["region"]
        if region not in hubs:
            problems.append(f"{where}: {key} is in {region}, which has no hub.")
            continue
        rules = read_rules(where, read_setting(folder / "landing-zones" / key) or {}, problems)
        number = partition(key)
        name = f"lz-{number:02d}"
        group = groups[region].setdefault(name, {"priority": FIRST_PARTITION_PRIORITY + 100 * number, "application": {}, "network": {}})
        # Collections in a group are ordered by key, ten apart; only traffic from that landing zone matches them.
        # Counted within this hub only: the group is this hub's, so another region's landing zones never
        # shift these priorities.
        place = sorted(k for k in keys if k in zones and zones[k]["region"] == region and partition(k) == number).index(key)
        tier = (hubs[region].get("firewall") or {}).get("sku_tier", "Standard")
        found = collections(rules, [zones[key]["range"]], where, key, zones, 100 + 10 * place, tier, problems)
        group["application"].update(found["application"])
        group["network"].update(found["network"])
    if problems:
        raise ConfigError("firewall rules are not valid:\n  " + "\n  ".join(dict.fromkeys(problems)))
    return {
        "root_id": platform["root_id"],
        "connectivity_subscription_id": platform["connectivity_subscription_id"],
        "policies": {region: {name: group for name, group in found.items() if group["application"] or group["network"]} for region, found in groups.items()},
    }


def vending_contract(zones: dict, plan: dict) -> dict:
    """What the platform needs from vending for firewall rules: each landing zone's region and range."""
    return {
        "landing_zones": {
            key: {"region": zone["settings"]["location"], "range": str(plan[key]["range"])}
            for key, zone in sorted(zones.items()) if key in plan
        }
    }
