"""Firewall rules: firewall/ in the platform repo into one config for the rules root.

    firewall/platform.yaml                  rules for every landing zone (from all landing zone ranges)
    firewall/landing-zones/<key>.yaml       one landing zone's rules (from its own range)

A rule names what it reaches, never where it starts: the source is the landing zone's range, from the
vending contract (_generated/vending.yaml), or every landing zone range of the hub for platform rules.

    outbound:
      - name: card-processor
        to: [api.stripe.com]        # a name, *.name, an address or range, another landing zone, or a tag
        ports: [https]              # https, http, a TCP port, or udp:<port>

The firewall policy holds a fixed set of rule collection groups, whatever the number of landing zones:
`platform` for the shared rules, and PARTITIONS groups for the landing zones. A landing zone's rules are
collections in group `lz-<nn>`, nn = sha256(key) mod PARTITIONS: the same group for ever, so adding one
landing zone never moves another's rules. Azure caps a policy's rule collection groups, so PARTITIONS
must never change once rules exist.
"""

from __future__ import annotations

import hashlib
import re
from ipaddress import IPv4Network
from pathlib import Path

from .files import read_setting, setting_names
from .merge import ConfigError

PARTITIONS = 30
PLATFORM_PRIORITY = 500
FIRST_PARTITION_PRIORITY = 1000
NAME = re.compile(r"^[a-z0-9]([a-z0-9-]{0,48}[a-z0-9])?$")
FQDN = re.compile(r"^(\*\.)?([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", re.IGNORECASE)
FQDN_TAG = re.compile(r"^[A-Z][A-Za-z0-9]+(\.[A-Za-z0-9]+)*$")  # WindowsUpdate, AzureBackup, ...
WEB_PORTS = {"https": ("Https", 443), "http": ("Http", 80)}


def partition(key: str) -> int:
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % PARTITIONS


def read_rules(where: str, data: dict, problems: list[str]) -> list[dict]:
    if set(data) - {"outbound"}:
        problems.append(f"{where}: {', '.join(sorted(set(data) - {'outbound'}))} is not a setting; rules go under outbound.")
    rules = data.get("outbound") or []
    if not isinstance(rules, list):
        problems.append(f"{where}: outbound is a list of rules, each with name, to and ports.")
        return []
    names = set()
    for index, rule in enumerate(rules):
        at = f"{where}: outbound[{index}]"
        if not isinstance(rule, dict) or set(rule) - {"name", "to", "ports", "description"} or not {"name", "to", "ports"} <= set(rule):
            problems.append(f"{at}: a rule has name, to and ports (and optionally description).")
            continue
        if not isinstance(rule["name"], str) or not NAME.fullmatch(rule["name"]):
            problems.append(f"{at}: name {rule['name']!r} is lower-case letters, digits and hyphens.")
        elif rule["name"] in names:
            problems.append(f"{at}: the name {rule['name']} is used twice in this file.")
        names.add(rule.get("name"))
        for field in ("to", "ports"):
            if not isinstance(rule[field], list) or not rule[field]:
                problems.append(f"{at}: {field} is a list, for example {'[api.example.com]' if field == 'to' else '[https]'}.")
    return [r for r in rules if isinstance(r, dict) and isinstance(r.get("to"), list) and isinstance(r.get("ports"), list)]


def shape(rule: dict, sources: list[str], at: str, zones: dict, problems: list[str]) -> tuple[dict | None, dict[str, dict]]:
    """One rule as the firewall module takes it: an application rule for names and tags on https or http,
    and network rules for addresses, landing zones and other ports (one for TCP, one for UDP). Returns
    (application rule or None, {suffix: network rule})."""
    names, tags, addresses = [], [], []
    for target in rule["to"]:
        target = str(target)
        if target in zones:
            addresses.append(zones[target]["range"])
        elif target.startswith("Office365.") or ("." not in target and FQDN_TAG.fullmatch(target)):
            tags.append(target)
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
        problems.append(f"{at}: a tag such as {tags[0]} takes only https and http; put other targets in a rule of their own.")
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
    network = {}
    # Addresses take every port, web ones as plain TCP; names take only the non-web ports here.
    for suffix, protocol, ports in (("", "TCP", tcp | ({str(n) for _, n in web} if addresses else set())), ("-udp", "UDP", udp)):
        targets = {"destination_addresses": sorted(set(addresses)), "destination_fqdns": sorted(names) if ports - {"80", "443"} or protocol == "UDP" or not web else []}
        if ports and (targets["destination_addresses"] or targets["destination_fqdns"]):
            network[suffix] = {
                "description": description,
                "protocols": [protocol],
                "source_addresses": sources,
                **targets,
                "destination_ports": sorted(ports, key=int),
            }
    return application, network


def collections(rules: list[dict], sources: list[str], where: str, owner: str, zones: dict, priority: int, problems: list[str]) -> dict:
    """The owner's rules as at most two collections: <owner>-network and <owner>-application."""
    application, network = {}, {}
    for index, rule in enumerate(rules):
        app, net = shape(rule, sources, f"{where}: outbound[{index}]", zones, problems)
        if app:
            application[rule["name"]] = app
        for suffix, one in net.items():
            network[rule["name"] + suffix] = one
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
    zones = (vending or {}).get("landing_zones") or {}
    hubs = platform["hubs"]
    groups: dict[str, dict] = {region: {} for region in hubs}

    shared = read_setting(folder / "platform") or {}
    shared_rules = read_rules("firewall/platform", shared, problems)
    for region, hub in hubs.items():
        found = collections(shared_rules, hub["landing_zone_ranges"], "firewall/platform", "platform", zones, 100, problems)
        if found["application"] or found["network"]:
            groups[region]["platform"] = {"priority": PLATFORM_PRIORITY, **found}

    keys = setting_names(folder / "landing-zones")
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
        place = sorted(k for k in keys if k in zones and partition(k) == number).index(key)
        found = collections(rules, [zones[key]["range"]], where, key, zones, 100 + 10 * place, problems)
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
