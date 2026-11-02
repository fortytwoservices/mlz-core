#!/usr/bin/env python3
"""Renders a customer's part of the ALZ library from customer.yaml.

The ALZ provider reads management group IDs and archetypes from library files,
and those cannot be templated at plan time. So this script writes them before
terraform init, from Fortytwo's defaults (governance/defaults.yaml) merged with
the customer's customer.yaml:

  config.json                       the merged settings, which the governance root reads
  alz_library_metadata.json
  architecture_definitions/         the management group tree, with Fortytwo's IDs
  archetype_overrides/              Fortytwo's changes to ALZ archetypes
  policy_assignments/               one deny and one inherit assignment per tag

The governance root loads Microsoft's library, then governance/lib, then this
output, in that order.

Merging: a top-level key in customer.yaml replaces the default, except the
mappings management_groups and tags, which are merged key by key, so a customer
can set management_groups.local alone.

It needs Python 3.10 or later (standard library only) and terraform on the
PATH, which converts the YAML.

Usage:
    render_library.py --customer FILE [--defaults FILE] [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GOVERNANCE = Path(__file__).resolve().parent.parent
MERGED_KEY_BY_KEY = ("management_groups", "tags")
# Written into every output folder, so a later run replaces only a folder this script made.
MARKER = ".rendered-by-mlz-core"

# The root ID prefixes management group IDs and resource names; 16 characters keep every generated name
# within its Azure limit (the tightest is the Log Analytics workspace, 63 characters).
ROOT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,14}[a-z0-9]$")
GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
# Platform subscriptions: each goes into the management group of the same name.
PLATFORM_ROLES = ("management", "connectivity", "identity", "security")
REQUIRED_ROLES = ("management", "connectivity")
# Policy assignment names at management group scope are at most 24 characters,
# and "Inherit-Tag-" takes 12 of them.
TAG = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,11}$")
REGION = re.compile(r"^[a-z0-9]+$")

PLACEHOLDER_SCOPE = "/providers/Microsoft.Management/managementGroups/placeholder"
# Fortytwo's definition (governance/lib), deployed at the root: the built-in "Require a tag on
# resource groups" plus exemptions for groups that platforms create themselves.
REQUIRE_TAG_DEFINITION = f"{PLACEHOLDER_SCOPE}/providers/Microsoft.Authorization/policyDefinitions/Deny-RG-Without-Tag"
INHERIT_TAG_DEFINITION = "/providers/Microsoft.Authorization/policyDefinitions/ea3f2387-9b95-492a-a190-fcdc54f7b070"

# What Fortytwo adds to ALZ archetypes, besides the tag assignments.
ROOT_DEFINITIONS = ["Deny-RG-Without-Tag"]
LANDING_ZONE_ADDITIONS = ["Deny-Resource-Locations", "Deny-RSG-Locations"]
OPTIONAL_GROUPS = ("security", "local")
# The archetypes in platform/alz; policy_assignments_to_remove may name only these.
ALZ_ARCHETYPES = (
    "root", "platform", "management", "connectivity", "identity", "security",
    "landing_zones", "corp", "online", "local", "sandbox", "decommissioned",
)


class RenderError(Exception):
    pass


def load_yaml(path: Path) -> dict:
    """Parses YAML with terraform's yamldecode, so the script needs no YAML package."""
    if shutil.which("terraform") is None:
        raise RenderError("terraform is not on the PATH. The script converts YAML with it.")
    expression = f'jsonencode(yamldecode(file("{path.resolve().as_posix()}")))'
    with tempfile.TemporaryDirectory() as empty:
        run = subprocess.run(
            ["terraform", f"-chdir={empty}", "console"],
            input=expression,
            capture_output=True,
            text=True,
        )
    if run.returncode != 0:
        raise RenderError(f"{path}: not valid YAML:\n{run.stderr or run.stdout}")
    # console prints the JSON as an HCL string literal, whose escapes are JSON's.
    data = json.loads(json.loads(run.stdout.strip()))
    if not isinstance(data, dict):
        raise RenderError(f"{path}: expected a mapping at the top level.")
    return data


def merge(defaults: dict, customer: dict) -> dict:
    merged = {**defaults, **customer}
    for key in MERGED_KEY_BY_KEY:
        if isinstance(defaults.get(key), dict) and isinstance(customer.get(key), dict):
            merged[key] = {**defaults[key], **customer[key]}
    return merged


def string_list(value, name: str, problems: list[str]) -> list[str]:
    """The value as a list of strings; null is an empty list. Anything else is a problem, not a guess:
    a bare string would otherwise be read character by character."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f"{name} must be a list of strings, written [a, b] or one '- item' per line.")
        return []
    return value


def mapping(value, name: str, problems: list[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.append(f"{name} must be a mapping.")
        return {}
    return value


def validate(config: dict) -> None:
    problems = []
    for key in ("root_id", "root_display_name", "primary_region", "security_contact"):
        if not config.get(key):
            problems.append(f"{key} is required.")
        elif not isinstance(config[key], str):
            problems.append(f"{key} must be a string.")
    if isinstance(config.get("security_contact"), str) and config["security_contact"] and not EMAIL.fullmatch(config["security_contact"]):
        problems.append(f"security_contact {config['security_contact']!r} is not an email address.")
    subscriptions = mapping(config.get("subscriptions"), "subscriptions", problems)
    problems += [f"subscriptions.{role} is required." for role in REQUIRED_ROLES if role not in subscriptions]
    for role, value in subscriptions.items():
        if role not in PLATFORM_ROLES:
            problems.append(f"subscriptions.{role}: unknown; the platform subscriptions are {', '.join(PLATFORM_ROLES)}.")
        elif not isinstance(value, str) or not GUID.fullmatch(value.lower()):
            problems.append(f"subscriptions.{role} must be a subscription ID, for example 00000000-0000-0000-0000-000000000000.")
    ids = [str(v).lower() for v in subscriptions.values()]
    if len(ids) != len(set(ids)):
        problems.append("subscriptions: each platform subscription must be a different one (a subscription sits in one management group).")
    if isinstance(config.get("root_id"), str) and not ROOT_ID.fullmatch(config["root_id"]):
        problems.append(
            f"root_id {config['root_id']!r}: 2 to 16 lower-case letters, digits and hyphens, "
            "starting and ending with a letter or digit."
        )
    regions = string_list(config.get("allowed_regions"), "allowed_regions", problems)
    if config.get("allowed_regions") in (None, []):
        problems.append("allowed_regions must list at least one region.")
    problems += [f"allowed_regions: {r!r} is not a region name." for r in regions if not REGION.fullmatch(r)]
    if isinstance(config.get("primary_region"), str) and regions and config["primary_region"] not in regions:
        problems.append(f"primary_region {config['primary_region']!r} is not in allowed_regions.")
    groups = mapping(config.get("management_groups"), "management_groups", problems)
    for key, value in groups.items():
        if key not in OPTIONAL_GROUPS:
            problems.append(f"management_groups.{key}: unknown; the optional groups are {', '.join(OPTIONAL_GROUPS)}.")
        elif value is not None and not isinstance(value, bool):
            problems.append(f"management_groups.{key} must be true or false, without quotes; got {value!r}.")
    tags = mapping(config.get("tags"), "tags", problems)
    for kind in ("required", "inherited"):
        for tag in string_list(tags.get(kind), f"tags.{kind}", problems):
            if not TAG.fullmatch(tag):
                problems.append(
                    f"tags.{kind}: {tag!r} must be 1 to 12 letters and digits, starting with a letter "
                    "(the policy assignment name is limited to 24 characters)."
                )
    for pattern in string_list(tags.get("exempt_resource_groups"), "tags.exempt_resource_groups", problems):
        if not 0 < len(pattern) <= 90 or pattern.count("*") > 1:
            problems.append(
                f"tags.exempt_resource_groups: {pattern!r} must be 1 to 90 characters with at most one '*' "
                "(Azure Policy's like condition)."
            )
    removals = mapping(config.get("policy_assignments_to_remove"), "policy_assignments_to_remove", problems)
    for archetype, names in removals.items():
        # The key becomes part of a file name, so only the known archetype names pass.
        if archetype not in ALZ_ARCHETYPES:
            problems.append(
                f"policy_assignments_to_remove.{archetype!s}: not an ALZ archetype; use one of {', '.join(ALZ_ARCHETYPES)}."
            )
            continue
        string_list(names, f"policy_assignments_to_remove.{archetype}", problems)
    if problems:
        raise RenderError("customer settings are not valid:\n  " + "\n  ".join(problems))


def normalize(config: dict) -> dict:
    """The checked settings in the one form Terraform and the ALZ module accept: subscription IDs in lower
    case, and every optional management group as true or false (null and missing mean false)."""
    groups = config.get("management_groups") or {}
    return {
        **config,
        "subscriptions": {role: value.lower() for role, value in (config.get("subscriptions") or {}).items()},
        "management_groups": {name: groups.get(name) is True for name in OPTIONAL_GROUPS},
    }


def prepare_output(out: Path, inputs: list[Path]) -> None:
    """Empties the output folder, refusing any folder this script did not create.

    The folder is deleted and recreated, so it must never be one that holds sources: not an input's
    folder or any folder above one, and not a folder without the marker this script leaves behind."""
    out = out.resolve()
    protected = [GOVERNANCE.parent, *(p.resolve().parent for p in inputs)]
    if any(out == p or out in p.parents for p in protected):
        raise RenderError(f"--out {out} holds source files (it contains an input or this repository); choose an empty folder.")
    if out.exists():
        if not out.is_dir():
            raise RenderError(f"--out {out} is a file; choose a folder.")
        if any(out.iterdir()) and not (out / MARKER).exists():
            raise RenderError(f"--out {out} is not empty and was not created by this script; choose an empty folder.")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("Rendered by mlz-core governance/scripts/render_library.py. The next run replaces this folder.\n")


def management_groups(config: dict) -> list[dict]:
    """The management group tree: the old Fortytwo IDs, plus the optional ALZ groups. The old product's
    "-unusedsub" group is gone: unused subscriptions go to Decommissioned (user, 30 October 2026)."""
    root = config["root_id"]
    optional = config.get("management_groups") or {}
    overridden = override_names(config)

    def archetype(name: str) -> list[str]:
        return [overridden.get(name, name)]

    groups = [
        (root, config["root_display_name"], None, archetype("root")),
        (f"{root}-platform", "Platform", root, archetype("platform")),
        (f"{root}-management", "Management", f"{root}-platform", archetype("management")),
        (f"{root}-connectivity", "Connectivity", f"{root}-platform", archetype("connectivity")),
        (f"{root}-identity", "Identity", f"{root}-platform", archetype("identity")),
        (f"{root}-landing-zones", "Landing Zones", root, archetype("landing_zones")),
        (f"{root}-lz-corp", "Corp", f"{root}-landing-zones", archetype("corp")),
        (f"{root}-lz-online", "Online", f"{root}-landing-zones", archetype("online")),
        (f"{root}-sandboxes", "Sandboxes", root, archetype("sandbox")),
        (f"{root}-decommissioned", "Decommissioned", root, archetype("decommissioned")),
    ]
    if optional.get("security"):
        groups.insert(5, (f"{root}-security", "Security", f"{root}-platform", archetype("security")))
    if optional.get("local"):
        groups.insert(-2, (f"{root}-lz-local", "Local", f"{root}-landing-zones", archetype("local")))
    return [
        {"id": gid, "display_name": name, "parent_id": parent, "exists": False, "archetypes": archetypes}
        for gid, name, parent, archetypes in groups
    ]


def tag_assignments(config: dict) -> dict[str, dict]:
    tags = config.get("tags") or {}
    exempt = list(tags.get("exempt_resource_groups") or [])
    assignments = {}
    for tag in tags.get("required") or []:
        assignments[f"Require-Tag-{tag}"] = assignment(
            f"Require-Tag-{tag}",
            f"Require the {tag} tag on resource groups",
            f"Resource groups must have the {tag} tag, except groups that platforms create themselves.",
            REQUIRE_TAG_DEFINITION,
            {"tagName": {"value": tag}, "exemptResourceGroupNames": {"value": exempt}},
            f"Resource groups {{enforcementMode}} have the {tag} tag.",
        )
    for tag in tags.get("inherited") or []:
        assignments[f"Inherit-Tag-{tag}"] = assignment(
            f"Inherit-Tag-{tag}",
            f"Inherit the {tag} tag from the resource group",
            f"Adds the resource group's {tag} tag to resources that lack it.",
            INHERIT_TAG_DEFINITION,
            {"tagName": {"value": tag}},
            None,
            identity=True,
        )
    return assignments


def assignment(name, display_name, description, definition, parameters, message, identity=False) -> dict:
    body = {
        "type": "Microsoft.Authorization/policyAssignments",
        "apiVersion": "2022-06-01",
        "name": name,
        "location": "${default_location}",
        "dependsOn": [],
        "properties": {
            "description": description,
            "displayName": display_name,
            "policyDefinitionId": definition,
            "enforcementMode": "Default",
            "scope": PLACEHOLDER_SCOPE,
            "notScopes": [],
            "parameters": parameters,
        },
    }
    if message:
        body["properties"]["nonComplianceMessages"] = [{"message": message}]
    if identity:
        body["identity"] = {"type": "SystemAssigned"}
    return body


def override_names(config: dict) -> dict[str, str]:
    """Base archetype -> the name of Fortytwo's override of it."""
    bases = set((config.get("policy_assignments_to_remove") or {}).keys()) | {"root", "landing_zones"}
    return {base: f"fortytwo_{base}" for base in sorted(bases)}


def archetype_overrides(config: dict, added_to_landing_zones: list[str]) -> list[dict]:
    removals = config.get("policy_assignments_to_remove") or {}
    overrides = []
    for base, name in override_names(config).items():
        overrides.append(
            {
                "name": name,
                "base_archetype": base,
                "policy_assignments_to_add": added_to_landing_zones if base == "landing_zones" else [],
                "policy_assignments_to_remove": list(removals.get(base) or []),
                "policy_definitions_to_add": ROOT_DEFINITIONS if base == "root" else [],
                "policy_definitions_to_remove": [],
                "policy_set_definitions_to_add": [],
                "policy_set_definitions_to_remove": [],
                "role_definitions_to_add": [],
                "role_definitions_to_remove": [],
            }
        )
    return overrides


def write_json(out: Path, relative: str, data) -> None:
    """Writes one output file, and refuses any path that would land outside the output folder."""
    root = out.resolve()
    path = (root / relative).resolve()
    if root not in path.parents:
        raise RenderError(f"refusing to write {path}: it is outside --out {root}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def render(customer_file: Path, defaults_file: Path, out: Path) -> dict:
    config = merge(load_yaml(defaults_file), load_yaml(customer_file))
    validate(config)
    root = config["root_id"]

    config = normalize(config)
    tags = tag_assignments(config)
    prepare_output(out, [customer_file, defaults_file])

    write_json(out, "config.json", config)
    write_json(
        out,
        "alz_library_metadata.json",
        {
            "$schema": "https://raw.githubusercontent.com/Azure/Azure-Landing-Zones-Library/main/schemas/library_metadata.json",
            "name": f"{root}-customer",
            "display_name": f"{config['root_display_name']} (rendered)",
            "description": "Rendered from customer.yaml by mlz-core governance/scripts/render_library.py. Do not edit.",
            "dependencies": [],
        },
    )
    write_json(
        out,
        f"architecture_definitions/{root}.alz_architecture_definition.json",
        {"name": root, "management_groups": management_groups(config)},
    )
    for override in archetype_overrides(config, LANDING_ZONE_ADDITIONS + list(tags)):
        write_json(out, f"archetype_overrides/{override['name']}.alz_archetype_override.json", override)
    for name, body in tags.items():
        write_json(out, f"policy_assignments/{name}.alz_policy_assignment.json", body)
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--customer", type=Path, required=True, help="the customer's customer.yaml")
    parser.add_argument("--defaults", type=Path, default=GOVERNANCE / "defaults.yaml")
    parser.add_argument("--out", type=Path, default=GOVERNANCE / ".rendered")
    args = parser.parse_args()
    try:
        config = render(args.customer, args.defaults, args.out)
    except RenderError as error:
        print(f"render_library: {error}", file=sys.stderr)
        return 1
    print(f"render_library: wrote the library for {config['root_id']} to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
