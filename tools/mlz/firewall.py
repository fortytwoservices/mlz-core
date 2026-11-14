"""Firewall rules: firewall/ in the platform repo into one config for the rules root.

    firewall/platform.yaml                  rules for every landing zone (from all landing zone ranges)
    firewall/landing-zones/<key>.yaml       one landing zone's rules (from its own range)
    firewall/management.yaml                management's network, the runners' among them (from its range)
    firewall/identity.yaml                  identity's network in every region that builds it (from its range)

A rule names what it reaches, never where it starts: the source is the landing zone's range, from the
vending contract (_generated/vending.yaml), every landing zone range of the hub for platform rules, or the
network's own range, from the platform's config, for management's and identity's.

    outbound:
      - name: card-processor
        description: Card payments. # what the rule is for: required, as a person approves each rule
        to: [api.stripe.com]        # a name (lower case), *.name, an address or range, another landing zone,
                                    # an FQDN tag (WindowsUpdate) or a service tag (AzureMonitor, Storage.NorwayEast)
        ports: [https]              # https, http, a TCP port, or udp:<port>

The firewall policy holds a fixed set of rule collection groups, whatever the number of landing zones:
`platform` for the shared rules, management's and identity's, and PARTITIONS groups for the landing zones. The
`platform` group always holds REQUIRED too: what governance's own policies install on every machine needs to
reach, and RUNNERS, what the pipelines need from the runners' subnets. A landing zone's rules are
collections in group `lz-<nn>`, nn = sha256(key) mod PARTITIONS: the same group for ever, so adding one
landing zone never moves another's rules. Azure caps a policy's rule collection groups, so PARTITIONS
must never change once rules exist.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
from ipaddress import IPv4Network
from pathlib import Path

from .files import read_setting, setting_names
from .merge import ConfigError

PARTITIONS = 30
# A rules file holds `outbound`, a list of rules; a rule has all of these. Every rule is approved by a person (the
# platform repo's code owner), who reads why it is needed in its description.
RULE_KEYS = ("name", "description", "to", "ports")
# A rule may not reach the whole internet, or nearly: name what it needs instead.
WIDEST_RANGE = 8
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

# In the primary hub's platform group, from management's two runner subnets: what the three pipelines reach
# once they run there. GitHub's own names come first, from its meta endpoint (github_actions_domains).
RUNNERS = [
    {
        "name": "azure",
        "description": "mlz-core: Entra ID and Azure Resource Manager, for the pipelines' sign-in, plans and applies.",
        "to": ["AzureActiveDirectory", "AzureResourceManager"],
        "ports": ["https"],
    },
    {
        "name": "terraform",
        "description": "mlz-core: Terraform, its providers and modules.",
        "to": ["registry.terraform.io", "releases.hashicorp.com"],
        "ports": ["https"],
    },
]
RUNNER_SUBNETS = ("github-runners", "runners")
# Management's and identity's own rules, after the required ones and firewall/platform.yaml's.
NETWORK_PRIORITIES = {"management": 300, "identity": 400}
GITHUB_META = "https://api.github.com/meta"


def github_actions_domains(saved: Path | None = None) -> list[str]:
    """The names GitHub's runners reach, from GitHub's meta endpoint (or a saved copy of it): domains.actions,
    the list GitHub keeps between the exact names and the wildcards. GitHub lists a name there at least a week
    before using it, and lists no date, so it is read on every run: a change then plans the runners' rule anew,
    and the daily drift check fails until it is applied. A name one label below a wildcard in the same list
    is left out, so a host GitHub adds under one changes nothing here."""
    try:
        if saved is not None:
            meta = json.loads(saved.read_text())
        else:
            request = urllib.request.Request(GITHUB_META, headers={"Accept": "application/vnd.github+json", "User-Agent": "mlz-core"})
            for attempt in range(3):
                try:
                    with urllib.request.urlopen(request, timeout=30) as response:
                        meta = json.load(response)
                    break
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(5 * (attempt + 1))
        domains = meta["domains"]["actions"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ConfigError(f"GitHub's Actions domains could not be read from {saved or GITHUB_META}: {error}. The runners' rule is never planned without them.") from error
    if not isinstance(domains, list) or not domains or not all(isinstance(d, str) and FQDN.fullmatch(d) for d in domains):
        raise ConfigError(f"{saved or GITHUB_META}: domains.actions is not a list of names; the runners' rule is never planned from it.")
    wildcards = {d[2:].lower() for d in domains if d.startswith("*.")}
    return sorted({d.lower() for d in domains if d.startswith("*.") or d.lower().split(".", 1)[1] not in wildcards})


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
        if not isinstance(rule, dict) or set(rule) != set(RULE_KEYS):
            problems.append(f"{at}: a rule has name, description, to and ports.")
            continue
        if not isinstance(rule["description"], str) or not rule["description"].strip():
            problems.append(f"{at}: description says what the rule is for, for whoever approves it.")
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


def shape(rule: dict, sources: list[str], at: str, zones: dict | None, tier: str, problems: list[str], held: dict | None = None) -> tuple[dict | None, dict[str, dict]]:
    """One rule as the firewall module takes it: an application rule for names and FQDN tags on https or
    http, and network rules for addresses, landing zones, service tags and other ports (one for TCP, one
    for UDP). Returns (application rule or None, {suffix: network rule})."""
    names, tags, addresses = [], [], []
    for target in rule["to"]:
        if not isinstance(target, str):  # YAML true or null would otherwise read as the tags True and None
            problems.append(f"{at}: {target!r} is not a name, an address or range, a landing zone or a tag.")
            continue
        if zones is None and LANDING_ZONE.fullmatch(target):
            # A landing zone, by key, before vending's first contract. It stays an address for the checks
            # (an FQDN tag next to it, a UDP rule's name); build() then writes nothing until the contract exists.
            addresses.append(f"<landing zone {target}>")
            continue
        if zones is not None and target in zones:
            addresses.append(zones[target]["range"])
        elif target in (held or {}):
            problems.append(f"{at}: {target} is decommissioned or removed, and its range is no longer its own; take it out of this rule.")
        elif target == "*" or target.lower() == "internet":  # Azure reads service tags in any case
            problems.append(f"{at}: {target} is the whole internet; name what the rule needs instead.")
        elif zones is not None and LANDING_ZONE.fullmatch(target):
            # A rule goes only to what exists: the file's own landing zone may wait for vending, its destinations not.
            problems.append(f"{at}: {target} is no landing zone in the vending contract (_generated/vending.yaml), nor a name, an address or a tag.")
        elif target in FQDN_TAGS or target.startswith("Office365."):
            tags.append(target)
        elif SERVICE_TAG.fullmatch(target):
            addresses.append(target)  # network rules take service tags as destination addresses
        elif FQDN.fullmatch(target):
            names.append(target.lower())
        else:
            try:
                network = IPv4Network(target)
            except ValueError:
                problems.append(f"{at}: {target!r} is not a name, an address or range, a landing zone or a tag.")
                continue
            if network.prefixlen < WIDEST_RANGE:
                problems.append(f"{at}: {target} is wider than a /{WIDEST_RANGE}, nearly the whole internet; name what the rule needs instead.")
            else:
                addresses.append(str(network))
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


def collections(rules: list[dict], sources: list[str], where: str, owner: str, zones: dict | None, priority: int, tier: str, problems: list[str], held: dict | None = None) -> dict:
    """The owner's rules as at most two collections: <owner>-network and <owner>-application."""
    application, network, made_by = {}, {}, {}
    for index, rule in enumerate(rules):
        app, net = shape(rule, sources, f"{where}: outbound[{index}]", zones, tier, problems, held)
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


def build(repo: Path, platform: dict, vending: dict | None, github: list[str]) -> dict | None:
    """The rules root's config: per hub region, the rule collection groups and what is in them. None, once
    every file is checked, while vending has published no contract yet. github is GitHub's Actions domains
    (github_actions_domains)."""
    folder = repo / "firewall"
    problems: list[str] = []
    # None until vending publishes its first contract: then landing zones are not known yet (see below).
    zones = None if vending is None else (vending.get("landing_zones") or {})
    # Removed or decommissioned landing zones whose ranges are not released yet: their rules no longer apply.
    held = {} if vending is None else (vending.get("held") or {})
    hubs = platform["hubs"]
    networks = platform["networks"]  # management's and identity's, by region, as the platform builds them
    groups: dict[str, dict] = {region: {} for region in hubs}

    shared = read_setting(folder / "platform") or {}
    shared_rules = read_rules("firewall/platform", shared, problems)
    own = {name: read_rules(f"firewall/{name}", read_setting(folder / name) or {}, problems) for name in NETWORK_PRIORITIES}
    if own["identity"] and not networks["identity"]:  # management's is always built, in the primary region
        problems.append("firewall/identity: no hub builds identity's network (identity: true in hubs/<region>.yaml), so its rules would have no source.")
    runners = [{"name": "github-actions", "description": "mlz-core: GitHub Actions, as GitHub's meta endpoint lists it (domains.actions).", "to": github, "ports": ["https"]}, *RUNNERS]
    for region, hub in hubs.items():
        tier = (hub.get("firewall") or {}).get("sku_tier", "Standard")
        built = {name: networks[name][region] for name in NETWORK_PRIORITIES if region in networks[name]}
        # Governance's agent runs on the platform's own machines too: domain controllers, self-hosted runners.
        required = collections(REQUIRED, hub["landing_zone_ranges"] + [net["address_space"] for net in built.values()], "mlz-core", "required", zones, 100, tier, problems)
        parts = [required]
        if "management" in built:
            subnets = [built["management"]["subnets"][subnet] for subnet in RUNNER_SUBNETS]
            parts.append(collections(runners, subnets, "mlz-core", "runners", zones, 110, tier, problems))
        parts.append(collections(shared_rules, hub["landing_zone_ranges"], "firewall/platform", "platform", zones, 200, tier, problems, held))
        parts += [collections(own[name], [net["address_space"]], f"firewall/{name}", name, zones, NETWORK_PRIORITIES[name], tier, problems, held) for name, net in built.items()]
        groups[region]["platform"] = {
            "priority": PLATFORM_PRIORITY,
            "application": {k: v for part in parts for k, v in part["application"].items()},
            "network": {k: v for part in parts for k, v in part["network"].items()},
        }

    keys = setting_names(folder / "landing-zones")
    # A landing zone not known yet has no hub yet either: Basic's limits apply when every hub is Basic.
    tiers = {(hub.get("firewall") or {}).get("sku_tier", "Standard") for hub in hubs.values()}
    unknown_tier = "Basic" if tiers == {"Basic"} else "Standard"
    if zones is None:
        # The platform's first deployment comes before any landing zone, and vending needs the platform's
        # contract first, so refusing these files would stop the hub for good (met rebuilding the canary,
        # 2026-11-11). They are checked like any other, with landing zones by key left unresolved, and go into
        # no group: check.sh leaves the rules stack out of the run until vending's contract exists, so nothing
        # in Azure changes, not even when the contract has gone missing from a platform already deployed.
        for key in keys:
            where = f"firewall/landing-zones/{key}"
            rules = read_rules(where, read_setting(folder / "landing-zones" / key) or {}, problems)
            collections(rules, [f"<landing zone {key}>"], where, key, None, 100, unknown_tier, problems)
        if keys and not problems:
            print(f"mlz firewall: no vending contract yet (_generated/vending.yaml): the rules for {', '.join(keys)} are checked, and wait for it", file=sys.stderr)
        keys = []
    for key in keys:
        where = f"firewall/landing-zones/{key}"
        if key in held:
            # Its rules leave Azure with this run, before its range can be given to another landing zone; the
            # file goes in a pull request of its own (docs/vending.md, decommissioning).
            print(f"mlz firewall: {where}.yaml is for {key}, decommissioned or removed: its rules are no longer applied; delete the file", file=sys.stderr)
            continue
        if key not in zones:
            # Its rules may come before the landing zone (docs/platform.md): checked now, merged, and applied by the
            # first run after vending publishes it. A key vending never publishes waits for ever, named here each run.
            rules = read_rules(where, read_setting(folder / "landing-zones" / key) or {}, problems)
            collections(rules, [f"<landing zone {key}>"], where, key, zones, 100, unknown_tier, problems, held)
            print(f"mlz firewall: {where}.yaml is for {key}, which the vending contract does not list yet: its rules are checked, "
                  "and wait until vending publishes it (a mistyped key waits for ever)", file=sys.stderr)
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
        found = collections(rules, [zones[key]["range"]], where, key, zones, 100 + 10 * place, tier, problems, held)
        group["application"].update(found["application"])
        group["network"].update(found["network"])
    if problems:
        raise ConfigError("firewall rules are not valid:\n  " + "\n  ".join(dict.fromkeys(problems)))
    if zones is None:
        # Checked, but not a config anyone may plan with: without the landing zones it would plan their rules
        # away. The rules stack waits for vending's contract (check.sh).
        return None
    return {
        "root_id": platform["root_id"],
        "connectivity_subscription_id": platform["connectivity_subscription_id"],
        # Each hub's policy, by the names the platform root gives it (tools/mlz/names.py).
        "policy_ids": {region: f"/subscriptions/{platform['connectivity_subscription_id']}/resourceGroups/{hub['names']['resource_group']}"
                                f"/providers/Microsoft.Network/firewallPolicies/{hub['names']['firewall_policy']}" for region, hub in platform["hubs"].items()},
        "policies": {region: {name: group for name, group in found.items() if group["application"] or group["network"]} for region, found in groups.items()},
    }


def vending_contract(zones: dict, plan: dict) -> dict:
    """What the platform needs from vending: each landing zone's region and range, for firewall rules, and
    the ranges of landing zones removed or decommissioned but not yet released, which the platform must not
    build over, and whose rules it no longer applies."""
    live = {key: zone for key, zone in zones.items() if zone["settings"].get("decommission") is not True}
    contract = {
        "landing_zones": {
            key: {"region": zone["settings"]["location"], "range": str(plan[key]["range"])}
            for key, zone in sorted(live.items()) if key in plan
        }
    }
    held = {key: str(plan[key]["range"]) for key in sorted(set(plan) - set(live))}
    if held:  # left out when empty, so a contract without removed landing zones reads as before
        contract["held"] = held
    return contract
