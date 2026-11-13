"""Every name the platform, governance and the bootstrap give, from the customer's template (docs/naming.md).

Each part of the platform is an app of its own, in prod (naming.PLATFORM): rg-acme-dns-pd-nwe-01,
vnet-acme-hub-pd-nwe-01, id-acme-mlz-govplan-pd-nwe-01. The loaders put each root's names in its config, all from
here: a name two roots share (the DNS zones' resource group, management's network and its resource group) is
worked out once, so they always agree. Unlike a landing zone's, these are worked out on every run, and change only
with a reviewed change to customer.yaml (or a hub file's or bootstrap.yaml's names:).
"""

from __future__ import annotations

from .merge import ConfigError
from .naming import Naming, check, platform_name

# Governance's, in management's subscription. The data collection rules, by the key governance gives them.
DATA_COLLECTION_RULES = {"change_tracking": "changetracking", "vm_insights": "vminsights", "defender_sql": "defendersql"}
# The hub module's components, in each region: the type, and the purpose that tells two of a type apart.
HUB = {
    "resource_group": ("resource_group", None),
    "virtual_network": ("virtual_network", None),
    "firewall": ("azure_firewall", None),
    "firewall_policy": ("firewall_policy", None),
    "firewall_public_ip": ("public_ip_address", "afw"),
    "firewall_management_public_ip": ("public_ip_address", "afwmgmt"),
    "firewall_route_table": ("route_table", "afw"),
    "bastion": ("bastion_host", None),
    "bastion_public_ip": ("public_ip_address", "bas"),
    "dns_resolver": ("dns_resolver", None),
    "dns_resolver_inbound_endpoint": ("dns_resolver_inbound_endpoint", None),
    "dns_resolver_outbound_endpoint": ("dns_resolver_outbound_endpoint", None),
}
# The bootstrap's pipeline identities: a role and a kind, as one purpose.
ROLES = {"governance": "gov", "platform": "plat", "vending": "vend"}
# What bootstrap.yaml's names: may set: the bootstrap's own resources that stand alone.
BOOTSTRAP_NAMED = {"resource_group": "resource_group", "storage_account": "storage_account"}


def dns_resource_group(naming: Naming, primary: str) -> str:
    """The Private Link DNS zones' group: the platform makes it, governance's policy and the bootstrap point at it."""
    return platform_name(naming, "dns", "resource_group", primary)


def network_resource_group(naming: Naming, network: str, primary: str) -> str:
    """Management's or identity's network's group, one for every region: the bootstrap makes it in the primary region."""
    return platform_name(naming, network, "resource_group", primary, "net")


def network(naming: Naming, network: str, region: str, subnets: list[str]) -> dict:
    """Management's or identity's network in one region. Subnets and peerings are named inside it, as before."""
    return {
        "virtual_network": platform_name(naming, network, "virtual_network", region),
        "route_table": platform_name(naming, network, "route_table", region),
        "github_network_settings": platform_name(naming, network, "github_network_settings", region),
        "network_security_groups": {subnet: platform_name(naming, network, "network_security_group", region, subnet) for subnet in subnets},
        "subnet": "snet-%s",
        "peering_to_hub": f"peer-{network}-to-hub",
        "peering_from_hub": f"peer-hub-to-{network}",  # never a landing zone's: a landing zone key has a hyphen
    }


def governance(naming: Naming, primary: str) -> dict:
    """Governance's: management's monitoring, and the DNS zones' group its policy points at."""
    return {
        "management": {
            "resource_group": platform_name(naming, "management", "resource_group", primary),
            "workspace": platform_name(naming, "management", "log_analytics_workspace", primary),
            "ama_identity": platform_name(naming, "management", "user_assigned_identity", primary, "ama"),
            "data_collection_rules": {key: platform_name(naming, "management", "data_collection_rule", primary, purpose)
                                      for key, purpose in DATA_COLLECTION_RULES.items()},
        },
        "dns_resource_group": dns_resource_group(naming, primary),
    }


def hub(naming: Naming, region: str, explicit: dict | None = None) -> dict:
    """One region's hub: every component the hub module makes, from the template or the hub file's names:."""
    explicit = explicit or {}
    return {key: explicit[key] if key in explicit else platform_name(naming, "hub", kind, region, purpose)
            for key, (kind, purpose) in HUB.items()}


def hub_explicit(where: str, given: object, problems: list[str]) -> dict:
    """A hub file's names:, checked: each a component the hub module makes, each a name Azure takes for its type."""
    if given is None:
        return {}
    if not isinstance(given, dict):
        problems.append(f"{where}: names maps a hub component ({', '.join(HUB)}) to its name.")
        return {}
    checked = {}
    for key, value in given.items():
        if key not in HUB:
            problems.append(f"{where}: names.{key} is not a hub component; use {', '.join(HUB)}.")
            continue
        try:
            if not isinstance(value, str):
                raise ConfigError(f"{value!r} is not a name")
            checked[key] = check(HUB[key][0], value)
        except ConfigError as error:
            problems.append(f"{where}: names.{key}: {error}")
    return checked


def bootstrap(naming: Naming, primary: str, explicit: dict | None = None) -> dict:
    """The bootstrap's own, in the primary region, and the names of the platform's it points at: the networks'
    groups it makes, and management's network and the DNS zones' group, where the state account's endpoint goes."""
    explicit = explicit or {}

    def own(key: str, kind: str, purpose: str | None) -> str:
        return explicit[key] if key in explicit else platform_name(naming, "bootstrap", kind, primary, purpose)

    return {
        "resource_group": own("resource_group", "resource_group", None),
        "storage_account": own("storage_account", "storage_account", "state"),
        "identities": {f"{role}-{kind}": platform_name(naming, "bootstrap", "user_assigned_identity", primary, f"{short}{kind}")
                       for role, short in ROLES.items() for kind in ("plan", "apply")},
        "state_endpoint": platform_name(naming, "bootstrap", "private_endpoint", primary, "state"),
        "state_endpoint_nic": platform_name(naming, "bootstrap", "network_interface", primary, "state"),
        "network_resource_groups": {name: network_resource_group(naming, name, primary) for name in ("management", "identity")},
        "management_network": platform_name(naming, "management", "virtual_network", primary),
        "state_endpoint_subnet": network(naming, "management", primary, [])["subnet"] % "private-endpoints",
        "dns_resource_group": dns_resource_group(naming, primary),
    }


def bootstrap_explicit(given: object) -> dict:
    """bootstrap.yaml's names:, checked; ConfigError with every problem."""
    if given is None:
        return {}
    if not isinstance(given, dict):
        raise ConfigError(f"bootstrap.yaml: names maps {' or '.join(BOOTSTRAP_NAMED)} to its name.")
    problems, checked = [], {}
    for key, value in given.items():
        if key not in BOOTSTRAP_NAMED:
            problems.append(f"bootstrap.yaml: names.{key} is not one the bootstrap takes; use {', '.join(BOOTSTRAP_NAMED)}.")
            continue
        try:
            if not isinstance(value, str):
                raise ConfigError(f"{value!r} is not a name")
            checked[key] = check(BOOTSTRAP_NAMED[key], value)
        except ConfigError as error:
            problems.append(f"bootstrap.yaml: names.{key}: {error}")
    if problems:
        raise ConfigError("\n  ".join(problems))
    return checked
