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


SCOPES = {
    # A hub file reaches its own hub's object: hub_virtual_networks.<region>.
    "connectivity": Scope("connectivity", "platform/terraform", "connectivity", "hub_virtual_networks.", "hubs.{region}.advanced"),
}
PRIMITIVES = {"string": str, "number": (int, float), "bool": bool}


def check(kind: str, where: str, given: object, problems: list[str]) -> dict:
    """A file's advanced:, checked; {} when absent or with problems (each added to problems)."""
    if given is None:
        return {}
    scope = SCOPES[kind]
    if not isinstance(given, dict):
        problems.append(f"{where}: advanced maps module inputs to their values ({reference(scope)}).")
        return {}
    call = inputs.load_snapshot()[scope.root][scope.call]
    section = inputs.load_maps()[scope.root][scope.call]
    entries = {path: inputs.entry(value) for path, value in (section.get("inputs") or {}).items()}
    found: list[str] = []
    walk(scope, call, section, entries, given, "", where, found)
    problems += found
    return {} if found else given


def walk(scope: Scope, call: dict, section: dict, entries: dict, given: dict, rel: str, where: str, found: list[str]) -> None:
    paths = call["inputs"]
    for key, value in given.items():
        here = f"{rel}.{key}" if rel else str(key)
        path, at = scope.prefix + here, f"{where}: advanced.{here}"
        if path not in paths:
            whole_call = not rel and str(key) in paths
            found.append(f"{at} is an input of the whole module call, not of what this file builds; advanced: here does not reach it."
                         if whole_call else f"{at} is not an input of {inputs.module_label(call)} ({reference(scope)}).")
            continue
        cover = inputs.covering(path, entries)
        if not cover:  # a group whose attributes have entries of their own: go into it
            if isinstance(value, dict):
                walk(scope, call, section, entries, value, here, where, found)
            else:
                found.append(f"{at} is a group of inputs: give its attributes ({reference(scope)}).")
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
        return found
    if kind in PRIMITIVES:
        ok = isinstance(value, PRIMITIVES[kind]) and not (kind == "number" and isinstance(value, bool))
        return [] if ok else [f"{at} must be a {kind}, not {value!r}."]
    return []  # any, or a tuple: the module checks it


def given(root: str, call: str, config: dict | None, places: dict[str, str]) -> dict[str, object]:
    """What a stack's advanced: sets in a module call, by input path: for explain."""
    scope = next((s for s in SCOPES.values() if s.root == root and s.call == call), None)
    if scope is None or not config:
        return {}
    try:
        block = inputs.value_at(config, scope.config, places)
    except KeyError:
        return {}
    return {scope.prefix + path: value for path, value in flat(block).items()}


def flat(value: dict, path: str = "") -> dict[str, object]:
    """Every value at the end of a path, dotted: a mapping's one by one, a list whole."""
    found = {}
    for key, item in value.items():
        here = f"{path}.{key}" if path else str(key)
        found.update(flat(item, here) if isinstance(item, dict) and item else {here: item})
    return found


def where(root: str, call: str, path: str) -> str | None:
    """For an open input, where advanced: sets it: the file and the path in its block; None where no file's reaches it."""
    scope = next((s for s in SCOPES.values() if s.root == root and s.call == call), None)
    if scope is None or not path.startswith(scope.prefix):
        return None
    return f"`advanced.{path.removeprefix(scope.prefix)}` in {inputs.FILES[scope.file]}"


def schema(kind: str) -> dict:
    """The JSON Schema of a file's advanced:: every open input it reaches, nested, typed as the module has it."""
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
    return {"description": f"Module inputs mlz-core leaves at the module's default, as the module names them ({reference(scope)}). "
                           "Anything else is refused with what owns it.", **node_schema(tree, paths)}


def node_schema(tree: dict, paths: dict) -> dict:
    properties = {key: json_type(paths, value) if isinstance(value, str) else node_schema(value, paths) for key, value in tree.items()}
    return {"type": "object", "properties": properties, "additionalProperties": False}


def json_type(paths: dict, path: str, kind: str | None = None) -> dict:
    kind = kind or paths[path]["type"]
    collection = re.fullmatch(r"(list|set|map)\((.*)\)", kind)
    if collection:
        outer, inner = collection.groups()
        element = json_type(paths, path, inner)
        return {"type": "object", "additionalProperties": element} if outer == "map" else {"type": "array", "items": element}
    if kind == "object":
        children = {p.removeprefix(path + "."): p for p in paths if p.startswith(path + ".") and "." not in p.removeprefix(path + ".")}
        return {"type": "object", "properties": {k: json_type(paths, p) for k, p in children.items()}, "additionalProperties": False}
    return {"type": {"string": "string", "number": "number", "bool": "boolean"}[kind]} if kind in ("string", "number", "bool") else {}


def reference(scope: Scope) -> str:
    return f"docs/reference/{inputs.page_name(scope.root)} lists them"


def plain(text: str) -> str:
    return text.replace("`", "")
