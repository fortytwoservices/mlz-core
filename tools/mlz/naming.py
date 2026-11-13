"""Names from the customer's template: every name mlz-core gives, worked out in one place.

    {type}-{org}-{app}-{purpose}-{env}-{region}-{nn}      mlz-core's default
    rg-acme-pay-net-pd-nwe-01                             a landing zone's network resource group

A template is tokens in braces, dashes, and plain lower-case text. A token that comes out empty drops out with
one dash beside it; only {purpose} may. The type's rules (modules/naming/name-rules.json, from the catalog of
Azure/avm-utl-naming) then decide the rest: no dashes for a type that takes none (stacmepaypdnwe01), lower
case where the type needs it, and a length and characters the type allows. A name that does not fit is
refused, never cut: two long names cut short can come out the same.

customer.yaml's naming section sets the customer's code, the template (and one per type, for the 24-character
ones), and adds to or changes the environments' and regions' short names, and the platform's parts' short names. An
explicit name for one resource wins over all of it, and is checked against the same rules.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .merge import ConfigError

# Every Azure region with Microsoft's code for it (Azure/avm-utl-regions v0.13.0's geo codes). A code is in names, so
# one never changes once used; customer.yaml's naming.regions replaces any.
REGION_CODES = json.loads((Path(__file__).resolve().parents[2] / "modules" / "naming" / "region-codes.json").read_text())
TEMPLATE = "{type}-{org}-{app}-{purpose}-{env}-{region}-{nn}"
TOKENS = ("type", "org", "app", "purpose", "env", "region", "nn")
OPTIONAL = {"purpose"}
# A landing zone's file name to its environment's short name; customer.yaml adds or changes any.
ENVIRONMENTS = {"dev": "dv", "test": "te", "uat": "ut", "sit": "st", "qa": "qa", "preprod": "pp", "sandbox": "sb", "dr": "dr", "prod": "pd"}
# Lengths of the short codes a customer chooses: short enough that the 24-character types still fit.
# A purpose may be a subnet's name (an NSG's), so it takes inner hyphens and up to 20; its type's own length is
# the real limit, and a type without dashes drops them.
LIMITS = {"org": (2, 4), "app": (2, 5), "purpose": (1, 20), "env": (1, 4), "region": (2, 5)}
SHORT = re.compile(r"^[a-z0-9]+$")
PURPOSE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")  # as a subnet's name: hyphens inside, any number
SETTINGS = {"org", "template", "templates", "environments", "regions", "platform"}
# The platform's parts, each named as an app of its own: vnet-acme-hub-pd-nwe-01, rg-acme-mgt-net-pd-nwe-01. Their
# environment is prod, so its code follows naming.environments. customer.yaml's naming.platform changes any.
PLATFORM = {"hub": "hub", "dns": "dns", "management": "mgt", "identity": "idn", "bootstrap": "mlz"}
PLATFORM_ENVIRONMENT = "prod"
RULES = json.loads((Path(__file__).resolve().parents[2] / "modules" / "naming" / "name-rules.json").read_text())["types"]
PART = re.compile(r"\{([a-z]+)\}|(-)|([a-z0-9]+)")


@dataclass
class Naming:
    """A customer's naming, from customer.yaml with mlz-core's defaults filled in."""
    org: str
    template: list[tuple[str, str]]
    templates: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    environments: dict[str, str] = field(default_factory=lambda: dict(ENVIRONMENTS))
    regions: dict[str, str] = field(default_factory=lambda: dict(REGION_CODES))
    platform: dict[str, str] = field(default_factory=lambda: dict(PLATFORM))


def rules_for(kind: str) -> dict:
    if kind not in RULES:
        raise ConfigError(f"{kind!r} is not a type mlz-core names; use {', '.join(sorted(RULES))}.")
    return RULES[kind]


def mapping(given: dict, key: str, problems: list[str]) -> dict:
    """A part of the naming section that maps names to values, or {} with a problem when it is something else."""
    value = given.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        problems.append(f"customer.yaml: naming.{key} maps each name to its value, not {value!r}.")
        return {}
    return value


def parse(template: object, where: str, problems: list[str]) -> list[tuple[str, str]]:
    """The template as (kind, value) parts: ("token", "app"), ("dash", "-") or ("text", "az")."""
    if not isinstance(template, str) or not template:
        problems.append(f"{where}: a template is text such as {TEMPLATE}.")
        return []
    parts, at = [], 0
    while at < len(template):
        found = PART.match(template, at)
        if not found:
            problems.append(f"{where}: {template!r} has {template[at]!r} at {at + 1}; a template is tokens in braces ({', '.join('{' + t + '}' for t in TOKENS)}), dashes and lower-case letters and digits.")
            return []
        token, dash, text = found.groups()
        if token is not None and token not in TOKENS:
            problems.append(f"{where}: {{{token}}} is not a token; use {', '.join('{' + t + '}' for t in TOKENS)}.")
            return []
        parts.append(("token", token) if token is not None else ("dash", "-") if dash else ("text", text))
        at = found.end()
    if parts[0][0] == "dash" or parts[-1][0] == "dash" or any(a[0] == b[0] == "dash" for a, b in zip(parts, parts[1:])):
        problems.append(f"{where}: {template!r} starts or ends with a dash, or has two in a row.")
        return []
    if ("token", "type") not in parts:
        problems.append(f"{where}: {template!r} has no {{type}}: every name starts from its resource's short name.")
        return []
    return parts


def settings(customer: dict, root_id: str) -> Naming:
    """customer.yaml's naming section, checked, with mlz-core's defaults for what it leaves out."""
    given = customer.get("naming")
    problems: list[str] = []
    if given is None:  # absent, or written with nothing under it: mlz-core's defaults
        given = {}
    if not isinstance(given, dict):
        raise ConfigError("customer.yaml: naming is a group of settings (org, template, templates, environments, regions).")
    problems += [f"customer.yaml: naming.{k} is not a setting; use {', '.join(sorted(SETTINGS))}." for k in sorted(set(given) - SETTINGS, key=str)]
    org = given.get("org", root_id)
    if "org" in given:
        short("customer.yaml: naming.org", "org", org, problems)
    elif not short("root ID", "org", org, []):
        problems.append(f"customer.yaml: the root ID {root_id!r} is too long to be the customer's code in names, which is 2 to 4 lower-case "
                        "letters and digits; set naming.org.")
    template = parse(given.get("template", TEMPLATE), "customer.yaml: naming.template", problems)
    templates = {}
    for kind, text in mapping(given, "templates", problems).items():
        if kind not in RULES:
            problems.append(f"customer.yaml: naming.templates.{kind} is not a type mlz-core names; use {', '.join(sorted(RULES))}.")
            continue
        templates[kind] = parse(text, f"customer.yaml: naming.templates.{kind}", problems)
    environments = dict(ENVIRONMENTS)
    for env, code in mapping(given, "environments", problems).items():
        if short(f"customer.yaml: naming.environments.{env}", "env", code, problems):
            environments[str(env)] = code
    regions = dict(REGION_CODES)
    for region, code in mapping(given, "regions", problems).items():
        if region not in REGION_CODES:
            problems.append(f"customer.yaml: naming.regions.{region} is not an Azure region (modules/naming/region-codes.json).")
        elif short(f"customer.yaml: naming.regions.{region}", "region", code, problems):
            regions[region] = code
    platform = dict(PLATFORM)
    for part, code in mapping(given, "platform", problems).items():
        if part not in PLATFORM:
            problems.append(f"customer.yaml: naming.platform.{part} is not a part of the platform; use {', '.join(PLATFORM)}.")
        elif short(f"customer.yaml: naming.platform.{part}", "app", code, problems):
            platform[part] = code
    parts = {}
    for part, code in platform.items():
        if code in parts:
            problems.append(f"customer.yaml: naming.platform gives {part} the short name {code}, which {parts[code]} has too; every part needs its own.")
        parts.setdefault(code, part)
    taken = {}
    for region, code in sorted(regions.items()):
        if code in taken:
            problems.append(f"customer.yaml: naming.regions gives {region} the code {code}, which {taken[code]} has too; every region needs its own.")
        taken.setdefault(code, region)
    if problems:
        raise ConfigError("naming settings are not valid:\n  " + "\n  ".join(problems))
    return Naming(org=org, template=template, templates=templates, environments=environments, regions=regions, platform=platform)


def short(where: str, token: str, value: object, problems: list[str]) -> bool:
    """A short code a customer chooses: lower-case letters and digits (a purpose also inner hyphens), within its
    token's limits."""
    low, high = LIMITS[token]
    shape = PURPOSE if token == "purpose" else SHORT
    if isinstance(value, str) and shape.fullmatch(value) and low <= len(value) <= high:
        return True
    problems.append(f"{where} is {value!r}; it must be {low} to {high} lower-case letters and digits{', with hyphens inside' if token == 'purpose' else ''}.")
    return False


def render(naming: Naming, kind: str, *, app: str, env: str, region: str, purpose: str | None = None, nn: int = 1) -> str:
    """The name of one resource of type `kind`, or a ConfigError saying which rule it breaks and how to fix it.
    env is the environment's long name (prod), region Azure's (norwayeast); both are turned into short codes."""
    problems: list[str] = []
    rules = rules_for(kind)
    short("app", "app", app, problems)
    if purpose:
        short("purpose", "purpose", purpose, problems)
    if env not in naming.environments:
        problems.append(f"the environment {env!r} has no short name; add it under naming.environments in customer.yaml.")
    if region not in naming.regions:
        problems.append(f"the region {region!r} has no short name (modules/naming/region-codes.json).")
    if not (isinstance(nn, int) and 1 <= nn <= 99):
        problems.append(f"the number {nn!r} must be 1 to 99.")
    if problems:
        raise ConfigError(f"no {kind} name: " + "; ".join(problems))
    purpose = (purpose or "") if rules["dashes"] else (purpose or "").replace("-", "")
    values = {"type": rules["slug"], "org": naming.org, "app": app, "purpose": purpose, "env": naming.environments[env],
              "region": naming.regions[region], "nn": f"{nn:02d}"}
    parts = list(naming.templates.get(kind) or naming.template)
    # An empty optional token goes, with the dash before it, or the one after it when it comes first.
    for index in reversed([i for i, (k, v) in enumerate(parts) if k == "token" and v in OPTIONAL and not values[v]]):
        if index > 0 and parts[index - 1][0] == "dash":
            del parts[index - 1:index + 1]
        elif index + 1 < len(parts) and parts[index + 1][0] == "dash":
            del parts[index:index + 2]
        else:
            del parts[index]
    name = "".join(values[v] if k == "token" else ("-" if rules["dashes"] else "") if k == "dash" else v for k, v in parts)
    return check(kind, name.lower() if rules["lowercase"] else name)


def platform_name(naming: Naming, part: str, kind: str, region: str, purpose: str | None = None) -> str:
    """The name of one of the platform's resources: its part (PLATFORM) is the app, its environment prod."""
    if part not in PLATFORM:
        raise ConfigError(f"{part!r} is not a part of the platform; use {', '.join(PLATFORM)}.")
    return render(naming, kind, app=naming.platform[part], env=PLATFORM_ENVIRONMENT, region=region, purpose=purpose)


def check(kind: str, name: str) -> str:
    """The name if Azure takes it for this type, else a ConfigError naming the rule it breaks."""
    rules = rules_for(kind)
    low, high = rules["min_length"] or 1, rules["max_length"]
    if high is not None and len(name) > high:
        raise ConfigError(f"{kind} name {name} is {len(name)} characters, {len(name) - high} more than Azure allows; "
                          f"shorten a short name, give the type a template of its own in customer.yaml, or set this name explicitly.")
    if len(name) < low:
        raise ConfigError(f"{kind} name {name} is {len(name)} characters; Azure needs at least {low}.")
    if "allowed" in rules:  # Unicode letters, decimal digits and a few characters (the catalog's \p{L}\p{Nd})
        allowed = rules["allowed"]
        fits = all(unicodedata.category(c)[0] in allowed["categories"] or unicodedata.category(c) in allowed["categories"] or c in allowed["characters"] for c in name)
        shown = f"letters, digits and {allowed['characters']}"
    else:
        fits, shown = re.fullmatch(rules["pattern"], name) is not None, rules["pattern"]
    if not fits or (rules.get("no_double_hyphen") and "--" in name):
        raise ConfigError(f"{kind} name {name} has characters Azure does not allow there ({shown}{', and no two hyphens in a row' if rules.get('no_double_hyphen') else ''}).")
    if rules.get("no_trailing_period") and name.endswith("."):
        raise ConfigError(f"{kind} name {name} ends with a period, which Azure does not allow.")
    if rules["lowercase"] and name != name.lower():
        raise ConfigError(f"{kind} name {name} must be lower case.")
    return name
