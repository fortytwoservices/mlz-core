"""Writes modules/naming/name-rules.json from Azure/avm-utl-naming's catalog, for the types mlz-core names.

    python3 tools/dev/name_rules.py <data/resource-name-rules.json at the version below>

Only lengths, case, dashes and allowed characters come from the catalog. The short names are CAF's, set here,
since some catalog slugs are unusable (a blob container's is storage-account-blob-service-container). Rules the
catalog's patterns leave out are added here (no_double_hyphen, no_trailing_period). A type whose catalog entry is incomplete or
wrong (virtual machines: no dashes) is left out until checked another way.

Three types mlz-core names have no rule in the catalog (OWN). Data collection rules (the catalog's entry has no
lengths) and GitHub's network settings (not in it): Microsoft publishes none for either: they are not on the resource
naming rules page, the REST API specs give the name no pattern or length, and azurerm only checks that it is not
empty. For those mlz-core sets a rule of its own, narrow enough to hold on any Azure resource it knows of: it may
refuse a name Azure would take, never the other way round. A subscription's alias (not in the catalog either) takes
the rule Azure/avm-ptn-alz-sub-vending documents for it.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

CATALOG = "Azure/avm-utl-naming v0.2.0, data/resource-name-rules.json"
# mlz-core's key: (the catalog's key, the CAF short name, rules the catalog's pattern leaves out)
TYPES = {
    "resource_group": ("resource_group", "rg", {"no_trailing_period": True}),
    "virtual_network": ("virtual_network", "vnet", {}),
    "network_security_group": ("network_security_group", "nsg", {}),
    "route_table": ("route_table", "rt", {}),
    "user_assigned_identity": ("user_assigned_identity", "id", {}),
    "budget": ("budget", "budget", {}),
    "private_endpoint": ("private_endpoint", "pep", {}),
    "network_interface": ("network_interface", "nic", {}),
    "storage_account": ("storage_account", "st", {}),
    "key_vault": ("key_vault_vault", "kv", {"no_double_hyphen": True}),
    "log_analytics_workspace": ("operational_insights_workspace", "log", {}),
    "application_gateway": ("application_gateway", "agw", {}),
    "azure_firewall": ("azure_firewall", "afw", {}),
    "firewall_policy": ("firewall_policy", "afwp", {}),
    "bastion_host": ("bastion_host", "bas", {}),
    "public_ip_address": ("public_ip_address", "pip", {}),
    "dns_resolver": ("dns_resolver", "dnspr", {}),
    "dns_resolver_inbound_endpoint": ("dns_resolver_inbound_endpoint", "in", {}),
    "dns_resolver_outbound_endpoint": ("dns_resolver_outbound_endpoint", "out", {}),
}

CONSERVATIVE = {"min_length": 1, "max_length": 64, "dashes": True, "lowercase": False, "global": False,
                "pattern": r"^[a-zA-Z0-9]([a-zA-Z0-9_.-]*[a-zA-Z0-9])?$"}
# mlz-core's key: (the CAF short name, or mlz-core's where CAF has none; its rule; where the rule comes from).
OWN = {
    "data_collection_rule": ("dcr", CONSERVATIVE, "Microsoft publishes no naming rule for Microsoft.Insights/dataCollectionRules: mlz-core's own"),
    "github_network_settings": ("ghns", CONSERVATIVE, "Microsoft publishes no naming rule for GitHub.Network/networkSettings: mlz-core's own"),
    # A subscription vending makes is named once, as its alias and its display name: the alias's rule, which is the
    # narrower (Azure/avm-ptn-alz-sub-vending's subscription_alias_name: a-z, A-Z, 0-9, - and _, up to 63).
    "subscription": ("sub", {"min_length": 1, "max_length": 63, "dashes": True, "lowercase": False, "global": False,
                             "pattern": r"^[a-zA-Z0-9_-]+$"},
                     "Microsoft.Subscription/aliases: the alias name's rule, as avm-ptn-alz-sub-vending documents it"),
}


def entries(catalog: dict) -> dict[str, dict]:
    found = {}

    def walk(node, key=""):
        if isinstance(node, dict):
            if "slug" in node and "max_length" in node:
                found[key] = node
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk(catalog)
    return found


def main(path: str) -> None:
    catalog = entries(json.loads(Path(path).read_text()))
    types = {}
    for key, (source, slug, extra) in TYPES.items():
        rule = catalog[source]
        allowed = {}
        if r"\p{" in rule["regex"]:
            # Python's re has no \p{}: a class of Unicode letters (L), decimal digits (Nd) and a few characters becomes
            # those categories and characters, checked one by one (tools/mlz/naming.py). Any other use stops here.
            found = re.fullmatch(r"\^\[\\p\{L\}\\p\{Nd\}((?:\\?[^\]\\])*)\]\+\$", rule["regex"])
            if not found:
                raise SystemExit(f"{source}: {rule['regex']} uses \\p{{}} in a way this script does not translate")
            allowed = {"allowed": {"categories": ["L", "Nd"], "characters": found.group(1).replace("\\", "")}}
        types[key] = {"slug": slug, "min_length": rule["min_length"], "max_length": rule["max_length"],
                      "dashes": rule["dashes"], "lowercase": rule["lowercase"], "global": rule["scope"] == "global",
                      **({"pattern": rule["regex"]} if not allowed else allowed), **extra}
    for key, (slug, rule, source) in OWN.items():
        types[key] = {"slug": slug, **rule, "own_rule": source}
    out = Path(__file__).resolve().parents[2] / "modules" / "naming" / "name-rules.json"
    out.write_text(json.dumps({"source": CATALOG, "types": types}, indent=2) + "\n")
    print(f"wrote {out}: {len(types)} types")


if __name__ == "__main__":
    main(sys.argv[1])
