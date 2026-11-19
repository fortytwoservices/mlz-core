"""advanced:, the module inputs mlz-core leaves at the module's default, set in a customer's file (decision D1).

    # connectivity/norwayeast.yaml
    advanced:                       # module inputs mlz-core leaves alone; names as in the module
      bastion:
        sku: Premium
        tunneling_enabled: true

A file's advanced: reaches the module call of the stack that file feeds, and in it only the inputs its root's input
map (tools/mlz/inputs.py) calls open. Anything else is refused with a message that names its owner: a setting's key,
what mlz-core works out, Fortytwo's reason for a fixed value, or why it is not supported yet. A name is never set here:
names: is its key. Each value is checked against the module's type here, because Terraform does not: it drops an
attribute a module's type does not declare and converts what it can (5 to "5"), silently (docs/advanced.md, verified
with Terraform 1.17.0). Only what is given goes into the config, so a stack without advanced: renders exactly as
before.
"""

from __future__ import annotations

import re
from typing import NamedTuple

from . import inputs


class Scope(NamedTuple):
    file: str  # the settings file kind, as inputs.FILES names it
    root: str
    call: str
    prefix: str  # what an advanced: path is relative to, in the module's input paths
    config: str  # where the loader puts the checked block in the stack's config, with inputs.PLACES
    # Maps of objects the root keys itself, by input path: advanced: names the entry by its key, as the module does
    # (virtual_networks.spoke.subnets.app...); what a page shows for the key; the loader says which keys there are.
    maps: dict = {}
    label: str = ""  # what the block's path starts with, where a file reaches several calls: the call's name


SCOPES = {
    # A hub file reaches its own hub's object: hub_virtual_networks.<region>.
    "connectivity": Scope("connectivity", "platform/terraform", "connectivity", "hub_virtual_networks.", "hubs.{region}.advanced"),
    # A landing zone reaches sub-vending's call: its own inputs, and its objects by the keys vending/terraform gives them.
    "landing-zone": Scope("landing-zone", "vending/terraform", "vending", "", "advanced", {
        "virtual_networks": "spoke", "virtual_networks.subnets": "<subnet>", "network_security_groups": "<subnet>",
        "budgets": "monthly", "budgets.notifications": "<actual_80 or forecast_100>",
    }),
    # customer.yaml reaches both of governance's calls, each under its name: advanced.alz, advanced.management.
    "customer.alz": Scope("customer", "governance/terraform", "alz", "", "advanced.alz", {}, "alz"),
    "customer.management": Scope("customer", "governance/terraform", "management", "", "advanced.management", {}, "management"),
}
# A file whose advanced: reaches several module calls names the call first.
CALLS = {"customer": ("alz", "management")}
PRIMITIVES = {"string": str, "number": (int, float), "bool": bool}


def check(kind: str, where: str, given: object, problems: list[str], keys: dict[str, list[str]] | None = None) -> dict:
    """A file's advanced:, checked; {} when absent or with problems (each added to problems). keys: the keys each of the
    scope's maps has for this stack (a landing zone's subnets), by input path."""
    if given is None:
        return {}
    if kind in CALLS:  # by call: each part checked on its own
        if not isinstance(given, dict):
            problems.append(f"{where}: advanced maps each module call, {' or '.join(CALLS[kind])}, to its inputs ({reference(SCOPES[f'{kind}.{CALLS[kind][0]}'])}).")
            return {}
        problems += [f"{where}: advanced.{name} is not a module call this file reaches; use {' or '.join(CALLS[kind])}." for name in given if name not in CALLS[kind]]
        parts = {name: check(f"{kind}.{name}", where, value, problems, keys) for name, value in given.items() if name in CALLS[kind]}
        return {name: part for name, part in parts.items() if part}
    scope = SCOPES[kind]
    if not isinstance(given, dict):
        problems.append(f"{where}: advanced{'.' + scope.label if scope.label else ''} maps module inputs to their values ({reference(scope)}).")
        return {}
    call = inputs.load_snapshot()[scope.root][scope.call]
    section = inputs.load_maps()[scope.root][scope.call]
    entries = {path: inputs.entry(value) for path, value in (section.get("inputs") or {}).items()}
    found: list[str] = []
    walk(scope, call, section, entries, given, "", scope.label, where, found, keys or {})
    problems += found
    return {} if found else prune(given)


def prune(value):
    """A block without its nulls: null is the module's default, as leaving the input out is (a later layer's null takes
    an earlier one's value away). The roots then never see one: try() does not turn a null into the fallback."""
    if isinstance(value, dict):
        return {key: prune(item) for key, item in value.items() if item is not None}
    return [prune(item) for item in value] if isinstance(value, list) else value


def walk(scope: Scope, call: dict, section: dict, entries: dict, given: dict, rel: str, label: str, where: str,
         found: list[str], keys: dict) -> None:
    """rel: the input path so far, as the module's types have it; label: as written, with the maps' keys."""
    paths = call["inputs"]
    for key, value in given.items():
        here, written = f"{rel}.{key}" if rel else str(key), f"{label}.{key}" if label else str(key)
        path, at = scope.prefix + here, f"{where}: advanced.{written}"
        if "." in str(key):  # Terraform would take bastion.sku as one attribute's name, and drop it
            found.append(f"{at}: write it nested, as the module's objects are ({str(key).split('.')[0]}: then the rest under it), not as one dotted key.")
            continue
        if path not in paths:
            whole_call = not rel and str(key) in paths
            found.append(f"{at} is an input of the whole module call, not of what this file builds; advanced: here does not reach it."
                         if whole_call else f"{at} is not an input of {inputs.module_label(call)} ({reference(scope)}).")
            continue
        cover = inputs.covering(path, entries)
        if not cover:  # a group whose attributes have entries of their own: go into it
            if not isinstance(value, dict):
                found.append(f"{at} is a group of inputs: give its attributes ({reference(scope)}).")
            elif path in scope.maps:  # by the keys the root gives its entries
                allowed = keys.get(path, [])
                for name, item in value.items():
                    if str(name) not in allowed:
                        found.append(f"{at}.{name}: there is no such {scope.maps[path]} here ({', '.join(allowed) or 'none'}).")
                    elif not isinstance(item, dict):
                        found.append(f"{at}.{name} is a group of inputs: give its attributes ({reference(scope)}).")
                    else:
                        walk(scope, call, section, entries, item, here, f"{written}.{name}", where, found, keys)
            else:
                walk(scope, call, section, entries, value, here, written, where, found, keys)
            continue
        item = entries[cover[0]]
        if item["class"] != "open":
            found.append(refusal(at, item, section.get("file")))
        else:
            found += typed(paths, path, value, at)


def refusal(at: str, item: dict, file: str | None) -> str:
    """Why an input is not advanced:'s to set, naming who owns it."""
    kind = item["class"]
    if kind == "setting":
        return f"{at} is the setting {item['key']} in {plain(inputs.FILES[item.get('file', file)])}; set it there."
    if kind == "derived":
        names = [item["names"]] if isinstance(item.get("names"), str) else item.get("names") or []
        hint = f" To name it yourself, set names.{' or names.'.join(names)} in this file." if names else ""
        return f"{at} is worked out by mlz-core ({item['by']}); it cannot be set.{hint}"
    if kind == "fixed":
        return f"{at} is fixed by Fortytwo ({item['why']}). Ask for a change in mlz-core."
    return f"{at} is not supported yet ({item['why']})."


def typed(paths: dict, path: str, value: object, at: str) -> list[str]:
    """Problems with a value for an input path, against the module's type; null leaves an optional input at its default."""
    info = paths[path]
    if value is None:
        return [] if not info["required"] else [f"{at} is required by the module: give it a value."]
    return of_type(paths, path, info["type"], value, at)


def of_type(paths: dict, path: str, kind: str, value: object, at: str) -> list[str]:
    collection = re.fullmatch(r"(list|set|map)\((.*)\)", kind)
    if collection:
        outer, inner = collection.groups()
        if outer == "map":
            if not isinstance(value, dict):
                return [f"{at} must be a mapping ({kind}), not {value!r}."]
            return [p for key, item in value.items() for p in of_type(paths, path, inner, item, f"{at}.{key}")]
        if not isinstance(value, list):
            return [f"{at} must be a list ({kind}), not {value!r}."]
        return [p for index, item in enumerate(value) for p in of_type(paths, path, inner, item, f"{at}[{index}]")]
    if kind == "object":
        if not isinstance(value, dict):
            return [f"{at} must be a mapping of its attributes, not {value!r}."]
        found = []
        for key, item in value.items():
            child = f"{path}.{key}"
            found += typed(paths, child, item, f"{at}.{key}") if child in paths else [f"{at}.{key} is not an attribute of it."]
        # What the module requires of each such object, since nothing of mlz-core's is merged into it.
        found += [f"{at} needs {child.removeprefix(path + '.')}, which the module requires." for child in children(paths, path)
                  if paths[child]["required"] and child.removeprefix(path + ".") not in value]
        return found
    if kind in PRIMITIVES:
        ok = isinstance(value, PRIMITIVES[kind]) and not (kind == "number" and isinstance(value, bool))
        return [] if ok else [f"{at} must be a {kind}, not {value!r}."]
    return []  # any, or a tuple: the module checks it


def scope_of(root: str, call: str) -> Scope | None:
    return next((s for s in SCOPES.values() if s.root == root and s.call == call), None)


def given(root: str, call: str, config: dict | None, places: dict[str, str]) -> list[tuple[str, str, object]]:
    """What a stack's advanced: sets in a module call: (input path, the path as written with the maps' keys, value),
    for explain."""
    scope = scope_of(root, call)
    if scope is None or not config:
        return []
    try:
        block = inputs.value_at(config, scope.config, places)
    except KeyError:
        return []
    return flat(scope, block, "", scope.label)


def flat(scope: Scope, value: dict, rel: str = "", label: str = "") -> list[tuple[str, str, object]]:
    """Every value at the end of a path in a block: an object's one by one, a map of the scope's by its keys, a list or
    any other value whole."""
    found = []
    for key, item in value.items():
        here, written = f"{rel}.{key}" if rel else str(key), f"{label}.{key}" if label else str(key)
        if scope.prefix + here in scope.maps and isinstance(item, dict):
            for name, entry in item.items():
                found += flat(scope, entry, here, f"{written}.{name}") if isinstance(entry, dict) else [(scope.prefix + here, f"{written}.{name}", entry)]
        elif isinstance(item, dict) and item and not open_leaf(scope, here):
            found += flat(scope, item, here, written)
        else:
            found.append((scope.prefix + here, written, item))
    return found


def open_leaf(scope: Scope, rel: str) -> bool:
    """Whether an input has an open entry of its own: its value is given whole (a map of role assignments, say)."""
    section = inputs.load_maps()[scope.root][scope.call]
    return inputs.entry((section.get("inputs") or {}).get(scope.prefix + rel, {})).get("class") == "open"


def written_path(scope: Scope, path: str) -> str:
    """An input path as advanced: writes it: each of the scope's maps with what stands for its keys."""
    parts, done = [scope.label] if scope.label else [], ""
    for part in path.removeprefix(scope.prefix).split("."):
        done = f"{done}.{part}" if done else part
        parts.append(part)
        if scope.prefix + done in scope.maps:
            parts.append(scope.maps[scope.prefix + done])
    return ".".join(parts)


def where(root: str, call: str, path: str) -> str | None:
    """For an open input, where advanced: sets it: the file and the path in its block; None where no file's reaches it."""
    scope = scope_of(root, call)
    if scope is None or not path.startswith(scope.prefix):
        return None
    return f"`advanced.{written_path(scope, path)}` in {inputs.FILES[scope.file]}"


def schema(kind: str) -> dict:
    """The JSON Schema of a file's advanced:: every open input it reaches, nested, typed as the module has it."""
    if kind in CALLS:  # a part per call
        parts = {name: schema(f"{kind}.{name}") for name in CALLS[kind]}
        return {"description": f"Module inputs mlz-core leaves at the module's default, by module call ({', '.join(CALLS[kind])}); "
                               "anything else is refused with what owns it.",
                "type": ["object", "null"], "properties": parts, "additionalProperties": False}
    scope = SCOPES[kind]
    paths = inputs.load_snapshot()[scope.root][scope.call]["inputs"]
    entries = {path: inputs.entry(value) for path, value in (inputs.load_maps()[scope.root][scope.call].get("inputs") or {}).items()}
    tree: dict = {}
    for path in paths:
        if path.startswith(scope.prefix) and path in entries and entries[path]["class"] == "open":
            node = tree
            for part in path.removeprefix(scope.prefix).split(".")[:-1]:
                node = node.setdefault(part, {})
            node[path.split(".")[-1]] = path
    block = node_schema(tree, paths, scope, "")
    return {"description": f"Module inputs mlz-core leaves at the module's default, as the module names them ({reference(scope)}). "
                           "Anything else is refused with what owns it.", **block, "type": ["object", "null"]}  # empty is none


def node_schema(tree: dict, paths: dict, scope: Scope, rel: str) -> dict:
    """A group of the tree; one of the scope's maps takes its entries by any key, each such a group."""
    properties = {}
    for key, value in tree.items():
        here = f"{rel}.{key}" if rel else key
        group = None if isinstance(value, str) else node_schema(value, paths, scope, here)
        if group is not None and scope.prefix + here in scope.maps:
            group = {"type": "object", "description": f"By {scope.maps[scope.prefix + here]}.", "additionalProperties": group}
        properties[key] = nullable(paths, value) if group is None else group
    return {"type": "object", "properties": properties, "additionalProperties": False}


def nullable(paths: dict, path: str) -> dict:
    """An input's schema, taking null too where the module's input is optional: null leaves it at its default."""
    found = json_type(paths, path)
    if paths[path]["required"] or "type" not in found:
        return found
    kind = found["type"]
    return {**found, "type": [*kind, "null"] if isinstance(kind, list) else [kind, "null"]}


def json_type(paths: dict, path: str, kind: str | None = None) -> dict:
    kind = kind or paths[path]["type"]
    collection = re.fullmatch(r"(list|set|map)\((.*)\)", kind)
    if collection:
        outer, inner = collection.groups()
        element = json_type(paths, path, inner)
        return {"type": "object", "additionalProperties": element} if outer == "map" else {"type": "array", "items": element}
    if kind == "object":
        # No required: a schema checks one file, and a layer may give part of an object another completes; the loader
        # checks what the module requires once the layers are merged.
        named = {p.removeprefix(path + "."): p for p in children(paths, path)}
        return {"type": "object", "properties": {k: nullable(paths, p) for k, p in named.items()}, "additionalProperties": False}
    return {"type": {"string": "string", "number": "number", "bool": "boolean"}[kind]} if kind in ("string", "number", "bool") else {}


def children(paths: dict, path: str) -> list[str]:
    """An object input's attributes' paths."""
    return [p for p in paths if p.startswith(path + ".") and "." not in p.removeprefix(path + ".")]


def reference(scope: Scope) -> str:
    return f"docs/reference/{inputs.page_name(scope.root)} lists them"


def plain(text: str) -> str:
    return text.replace("`", "")
