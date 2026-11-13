"""JSON Schemas of the customer's YAML, for editors: completion, a description on hover, and a mistyped key
or value marked as it is typed.

    python3 -m mlz schema --out schemas

They are made from the loaders' own key lists and patterns, so a setting the loader takes is never missing
here, and test_schema fails when the committed files in schemas/ are not what this writes. A schema checks
one file on its own. It refuses nothing the loader accepts, but a single value left empty (null): a group
or a list left empty is read as not set, a value is a mistake. What needs several files (a setting required
in some layer, a subscription used twice, ranges that overlap) stays with the loader's check.

Each customer repo's .vscode/settings.json points at the schemas of the release it runs (from the
starter, replaced by mlz upgrade), at raw.githubusercontent.com: mlz-core is public.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from . import firewall, install, platform, vending

CORE = Path(__file__).resolve().parents[2]
DRAFT = "http://json-schema.org/draft-07/schema#"
# The archetypes governance can offer a landing zone (governance/terraform/outputs.tf): local only when
# customer.yaml turns on management_groups.local. Vending checks against the governance contract's.
ARCHETYPES = ("corp", "online", "sandbox", "local")
# An IPv4 range in every form the loader reads (ipaddress.ip_network): 10.0.0.0/22, 10.0.0.0/255.255.252.0,
# or one address, which is a /32. Whether host bits are set is the loader's check.
IPV4 = r"((25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])\.){3}(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
CIDR = rf"^{IPV4}(/(0*([0-9]|[12][0-9]|3[0-2])|{IPV4}))?$"  # a prefix may be zero-padded, as int() reads it


def governance_rules():
    """governance/scripts/render_library.py, which runs as a script of its own, not as part of the mlz package."""
    spec = importlib.util.spec_from_file_location("render_library", CORE / "governance" / "scripts" / "render_library.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pattern(compiled: re.Pattern, ignore_case: bool = False) -> str:
    """A Python pattern as JSON Schema's (ECMAScript), which has no flags: [a-f] also takes A-F where the loader
    lower-cases first."""
    text = compiled.pattern
    if not text.startswith("^"):
        text = "^" + text
    if not text.endswith("$"):
        text += "$"
    return text.replace("a-f", "a-fA-F") if ignore_case else text


def mapping(properties: dict, description: str | None = None, **extra) -> dict:
    """A group of settings: only these keys, none required (a layer or a chunk may set any part of it)."""
    found = {"type": "object", "properties": properties, "additionalProperties": False, **extra}
    return {"description": description, **found} if description else found


def or_empty(schema: dict) -> dict:
    """A group or a list that may also be left empty, which the loaders read as not set."""
    kind = schema["type"]
    return {**schema, "type": [*kind, "null"] if isinstance(kind, list) else [kind, "null"]}


def text(description: str, **extra) -> dict:
    return {"type": "string", "description": description, **extra}


def one_or_more(item: dict, description: str, empty: bool = False) -> dict:
    """One value or a list of them, as the loader reads either; the list may be empty where the loader reads
    that as none."""
    listed = {"type": "array", "items": item} if empty else {"type": "array", "items": item, "minItems": 1}
    return {"description": description, "anyOf": [item, listed]}


def landing_zone() -> dict:
    email = {"type": "string", "pattern": pattern(vending.EMAIL)}
    properties = {
        "owner": text("The owning team's email address. Becomes the Owner tag, and gets the budget alerts unless budget.contacts names others.", pattern=pattern(vending.EMAIL), maxLength=vending.TAG_VALUE_LENGTH),
        "cost_center": {"description": "The cost center. Becomes the CostCenter tag.", "type": ["string", "integer"], "maxLength": vending.TAG_VALUE_LENGTH},
        "archetype": text("The management group the subscription goes into: corp, online or sandbox (local only when governance turns it on).", enum=list(ARCHETYPES)),
        "location": text("The Azure region, one of governance's allowed regions with a hub, for example norwayeast.", pattern="^[a-z0-9]+$", examples=list(platform.REGIONS)),
        "subscription": text("The landing zone's subscription ID, handed to vending before this file is merged.", pattern=pattern(vending.GUID, ignore_case=True)),
        "budget": or_empty(mapping({
            "monthly": {"description": "A whole amount per month, in the subscription's currency. No monthly, no budget.", "type": "integer", "exclusiveMinimum": 0},
            "contacts": or_empty({"description": "Who gets the budget alerts, as email addresses (default: the owner).", "type": "array", "items": email}),
        }, "The landing zone's monthly budget.")),
        "network": or_empty(mapping({
            "size": {"description": "The range's prefix length, from 16 to 28. The IP allocator gives out the block.", "type": "integer", "minimum": 16, "maximum": 28, "default": vending.DEFAULT_SIZE},
            "subnets": or_empty({
                "description": "Each subnet's name to its prefix length, up to 29 (default: one subnet, default, a /26 or the whole of a smaller range).",
                "type": "object",
                "propertyNames": {"pattern": pattern(vending.SUBNET_NAME)},
                "additionalProperties": {"type": "integer", "minimum": 8, "maximum": 29},
            }),
        }, "The spoke network. Its addresses come from the IP allocator, in _generated/ip-plan.yaml, never from here.")),
        "github": or_empty(mapping({
            "organization": text("The GitHub organization of the landing zone's repo. Set it once, in _defaults/all.yaml.", pattern=pattern(vending.GITHUB_ORGANIZATION)),
            "repo": text("The landing zone's repo, whose main branch signs in as its deploy identity.", pattern=pattern(vending.GITHUB_REPO)),
        }, "The landing zone's own repo.")),
        "tags": or_empty({
            "description": "Extra tags. Owner, CostCenter, Environment, Workload and ManagedBy are set for you.",
            "type": "object",
            "maxProperties": vending.EXTRA_TAGS,
            "additionalProperties": {"type": ["string", "number", "boolean"], "maxLength": vending.TAG_VALUE_LENGTH},
        }),
    }
    return {
        "$schema": DRAFT,
        "title": "MLZ landing zone",
        "description": "A layer of landing zone settings: landing-zones/_defaults/all.yaml, _defaults/<env>.yaml, <workload>/_workload.yaml or <workload>/<env>.yaml. Each says only what differs from the one above.",
        **mapping(properties),
    }


def hub() -> dict:
    cidr = {"type": "string", "pattern": CIDR}
    properties = {
        "platform_address_space": text("The region's platform range, a /21: the hub (its first /22, its subnets carved from it), management's /24, identity's /24 and a spare /23. No landing zone gets an address in it.", pattern=CIDR),
        "landing_zone_ranges": one_or_more(cidr, "Where landing zone networks get their address ranges."),
        "reserved": one_or_more(cidr, "Ranges never given to a landing zone: existing networks, on-premises sites, partner links.", empty=True),
        "firewall": text("The hub's Azure Firewall tier.", enum=list(platform.FIREWALL_TIERS), default="Standard"),
        "dns_resolver": {"description": "A DNS resolver for Private Link names, for landing zones and on-premises.", "type": "boolean", "default": True},
        "bastion": {"description": "Azure Bastion, for VMs in any landing zone.", "type": "boolean", "default": False},
        "identity": {"description": "Identity's network in this region, for domain controllers, in the identity subscription. Its range is held either way.", "type": "boolean", "default": False},
    }
    return {
        "$schema": DRAFT,
        "title": "MLZ hub",
        "description": "The platform's networks in one region, the hub first: hubs/<region>.yaml, or the chunks in hubs/<region>/.",
        **mapping(properties),
    }


def firewall_rules() -> dict:
    # 1 to 65535, as the loader reads the digits (int(), so leading zeros pass), on TCP or after udp:.
    number = "0*([1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])"
    port_word = f"^([Hh][Tt][Tt][Pp][Ss]?|{number}|[Uu][Dd][Pp]:{number})$"
    descriptions = {
        "name": text("The rule's name, lower-case letters, digits and hyphens; unique in this file.", pattern=pattern(firewall.NAME)),
        "to": {"description": "Where the traffic goes: names (api.example.com, *.example.com), addresses and ranges, other landing zones by key, FQDN tags (WindowsUpdate) or service tags (AzureMonitor, Storage.NorwayEast).", "type": "array", "items": {"type": "string"}, "minItems": 1},
        "ports": {"description": "https, http, TCP ports (8443) or udp:<port>.", "type": "array", "minItems": 1, "items": {"anyOf": [{"type": "integer", "minimum": 1, "maximum": 65535}, {"type": "string", "pattern": port_word}]}},
        "description": text("What the rule is for."),
    }
    rule = mapping({key: descriptions[key] for key in firewall.RULE_KEYS}, required=list(firewall.RULE_KEYS[:3]))
    return {
        "$schema": DRAFT,
        "title": "MLZ firewall rules",
        "description": "Outbound rules: firewall/platform.yaml for every landing zone, firewall/landing-zones/<key>.yaml for one, firewall/management.yaml and firewall/identity.yaml for the platform's own networks. A rule names only where traffic goes; the source is worked out.",
        **mapping({"outbound": or_empty({"description": "The rules, each with name, to and ports.", "type": "array", "items": rule})}),
    }


def customer() -> dict:
    rules = governance_rules()
    guid = text("A subscription ID.", pattern=pattern(rules.GUID, ignore_case=True))
    strings = or_empty({"type": "array", "items": {"type": "string"}})
    tag_names = or_empty({"type": "array", "items": {"type": "string", "pattern": pattern(rules.TAG)}})
    descriptions = {
        "name": text("The customer's name, for people reading this file."),
        "root_id": text("The prefix of every management group ID and many resource names: 2 to 16 lower-case letters, digits and hyphens.", pattern=pattern(rules.ROOT_ID)),
        "root_display_name": text("The top management group's display name."),
        "primary_region": text("Where the platform's own resources go; one of allowed_regions.", pattern=pattern(rules.REGION)),
        "security_contact": text("Defender for Cloud's security alerts go to this email address.", pattern=pattern(rules.EMAIL)),
        "parent_management_group_id": text("An existing management group to build the tree below, instead of the tenant root."),
        "subscriptions": or_empty(mapping({role: guid for role in rules.PLATFORM_ROLES}, "The platform subscriptions, each moved into the management group of the same name. management and connectivity are required.")),
        "allowed_regions": {"description": "Regions resources and resource groups may be created in.", "type": "array", "items": {"type": "string", "pattern": pattern(rules.REGION)}, "minItems": 1},
        "management_groups": or_empty(mapping({group: {"type": ["boolean", "null"]} for group in rules.OPTIONAL_GROUPS}, "The optional management groups: security under Platform, local under Landing Zones.")),
        "tags": or_empty(mapping({
            "required": {**tag_names, "description": "Tags required on every resource group in the landing zones (deny)."},
            "inherited": {**tag_names, "description": "Tags copied from the resource group to resources that lack them (modify)."},
            "exempt_resource_groups": or_empty({"description": "Resource groups Azure creates itself, where the required tags are not enforced. One * per pattern.", "type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 90, "pattern": "^[^*]*[*]?[^*]*$"}}),
            "in_sandboxes": {"description": "Whether sandboxes must tag their resource groups too.", "type": ["boolean", "null"]},
        }, "Tag policies. A key given here replaces the default's whole list.")),
        "policy_assignments_to_remove": or_empty({"description": "ALZ policy assignments to leave out, by archetype.", "type": "object", "propertyNames": {"enum": list(rules.ALZ_ARCHETYPES)}, "additionalProperties": strings}),
    }
    return {
        "$schema": DRAFT,
        "title": "MLZ customer",
        "description": "Customer-wide settings for governance. Any setting in mlz-core's governance/defaults.yaml can be overridden here.",
        **mapping({key: descriptions[key] for key in rules.SETTINGS}),
    }


def bootstrap() -> dict:
    github = {
        "organization": text("The customer's GitHub organization.", pattern=pattern(vending.GITHUB_ORGANIZATION)),
        "repositories": mapping({role: text(f"The {role} repo's name.") for role in install.ROLES}, "The three repos, by role."),
        "reviewers": {"description": "GitHub users who approve every apply.", "type": "array", "items": {"type": "string"}, "minItems": 1},
        "runner_size": text("The private runner's machine size at GitHub, for mlz install runners.", default="2-core", examples=["2-core", "4-core"]),
    }
    properties = {
        "github": mapping({key: github[key] for key in install.GITHUB_SETTINGS}, "The customer's GitHub organization, repos, reviewers and private runner."),
        "state_operators": or_empty({"description": "People who may run Terraform by hand against the state, by name: their Entra object IDs.", "type": "object", "additionalProperties": text("An Entra object ID.", pattern=pattern(vending.GUID, ignore_case=True))}),
    }
    return {
        "$schema": DRAFT,
        "title": "MLZ bootstrap",
        "description": "The one-time setup of the customer's pipelines, run by Fortytwo with mlz-core's bootstrap.",
        **mapping({key: properties[key] for key in install.BOOTSTRAP_SETTINGS}),
    }


# The file name of each schema, and which files of which repo it is for (the starters' .vscode/settings.json).
SCHEMAS = {
    "customer.schema.json": customer,
    "bootstrap.schema.json": bootstrap,
    "hub.schema.json": hub,
    "firewall.schema.json": firewall_rules,
    "landing-zone.schema.json": landing_zone,
}


def render() -> dict[str, str]:
    """Every schema's file name and its text."""
    return {name: json.dumps(build(), indent=2) + "\n" for name, build in SCHEMAS.items()}


def write(out: Path) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    for name, body in render().items():
        (out / name).write_text(body)
    return sorted(SCHEMAS)
