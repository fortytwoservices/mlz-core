"""Who owns each input of every module call mlz-core makes: the input maps, and the reference pages made from them.

    python3 -m mlz reference --out docs/reference     the pages, one per root, from the maps and the snapshot
    python3 -m mlz inputs --refresh                    the snapshot, from each root's modules at their pins
    python3 -m mlz inputs --check                      fail when the snapshot is not what --refresh would write (CI)

Beside each Terraform root is `inputs.yaml`, its input map: for each module call the root makes (and each call of a
module of mlz-core's own that the root calls), every input of the module, by its path, in exactly one class:

  setting   from a key in the customer's YAML, which `key` names (`file` says which file, if not the call's)
  derived   worked out by mlz-core: from the layout, the allocator, a contract or the naming; `by` says how, and
            `names` the key under names: that overrides a name
  fixed     Fortytwo's decision for every customer: `value` and `why`
  open      mlz-core does not set it, so the module's default applies
  closed    not supported yet: `why`

A path is an input and its attributes, dotted (`hub_virtual_networks.firewall.sku_tier`); a map, list or set of
objects is passed through, so its elements' attributes follow its own name. An entry classifies its path and
everything below it, so a group of inputs with one owner takes one line.

What each module declares comes from a snapshot, module-variables.json beside this file, so the tests need neither
the network nor Terraform: `--refresh` downloads the modules (terraform get) and reads their variables, and CI's
`--check` fails when a pin moved and the snapshot did not. The tests (test_inputs.py) then fail on an input without
exactly one entry, a setting no loader accepts, and a page in docs/reference/ that is not what this writes.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
SNAPSHOT = Path(__file__).with_name("module-variables.json")


# --- What a module declares: its variables, read from its .tf files ---------------------------------------------
#
# Enough of HCL to read `variable` blocks: their type, whether they have a default, and the defaults of optional
# attributes. A tokenizer that knows comments, strings with templates, and heredocs, so a brace in any of them is
# never counted.

TOKEN = re.compile(r"""
    (?P<newline>\n)
  | (?P<space>[ \t\r]+)
  | (?P<comment>\#[^\n]*|//[^\n]*|/\*.*?\*/)
  | (?P<heredoc><<-?(?P<marker>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\n)
  | (?P<quote>")
  | (?P<number>[0-9]+(\.[0-9]+)?([eE][+-]?[0-9]+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_-]*)
  | (?P<symbol>\.\.\.|==|!=|<=|>=|=>|&&|\|\||[{}()\[\]=,:.?!<>+\-*/%])
""", re.VERBOSE | re.DOTALL)


class Token(tuple):
    kind = property(lambda self: self[0])
    text = property(lambda self: self[1])


def string_end(text: str, i: int) -> int:
    """The index after the closing quote of the string whose opening quote is at i - 1."""
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
        elif c == '"':
            return i + 1
        elif text.startswith(("${", "%{"), i) and not text.startswith(("$${", "%%{"), i - 1):
            i = template_end(text, i + 2)
        else:
            i += 1
    raise ValueError("a string is not closed")


def template_end(text: str, i: int) -> int:
    """The index after the } that closes a ${ or %{ opened just before i."""
    depth = 1
    while i < len(text):
        c = text[i]
        if c == '"':
            i = string_end(text, i + 1)
            continue
        depth += {"{": 1, "}": -1}.get(c, 0)
        i += 1
        if depth == 0:
            return i
    raise ValueError("a template is not closed")


def tokens(text: str) -> list[Token]:
    found, i = [], 0
    while i < len(text):
        match = TOKEN.match(text, i)
        if not match:
            raise ValueError(f"cannot read {text[i:i + 20]!r}")
        kind = match.lastgroup if match.lastgroup != "marker" else "heredoc"
        if kind == "quote":
            end = string_end(text, match.end())
            found.append(Token(("string", text[i:end])))
            i = end
            continue
        if kind == "heredoc":
            marker = match["marker"]
            end = re.compile(rf"^[ \t]*{marker}[ \t]*$", re.MULTILINE).search(text, match.end())
            if not end:
                raise ValueError(f"heredoc {marker} is not closed")
            found.append(Token(("string", text[i:end.end()])))
            i = end.end()
            continue
        if kind == "newline":
            found.append(Token(("newline", "\n")))
        elif kind not in ("space", "comment"):
            found.append(Token((kind, match[0])))
        i = match.end()
    return found


OPEN, CLOSE = {"(": ")", "[": "]", "{": "}"}, {")", "]", "}"}


def expression(toks: list[Token], i: int, stop: set[str]) -> tuple[list[Token], int]:
    """The tokens of one expression from i, up to a token in stop (or a newline) outside brackets."""
    depth, start = 0, i
    while i < len(toks):
        kind, text = toks[i]
        if depth == 0 and (text in stop or kind == "newline"):
            break
        if text in OPEN:
            depth += 1
        elif text in CLOSE:
            if depth == 0:
                break
            depth -= 1
        i += 1
    return [t for t in toks[start:i] if t.kind != "newline"], i


def source(toks: list[Token]) -> str:
    """An expression's tokens as HCL, spaced as one line; a heredoc keeps its lines, which the snapshot's JSON escapes."""
    out = ""
    for kind, text in toks:
        if out and not (out.endswith(("(", "[", "!", ".")) or text in (")", "]", ",", ".", ":") or out.endswith("{") and text == "}"):
            out += " "
        out += text
    return out


def parse_type(toks: list[Token], i: int = 0) -> tuple[dict, int]:
    """A type constraint from toks[i]: {"type": name, "element": ...} for collections, {"type": "object",
    "attributes": {name: {"type": ..., "optional": bool, "default": text}}}, or {"type": primitive}."""
    kind, text = toks[i]
    if text in ("list", "set", "map") and toks[i + 1].text == "(":
        element, i = parse_type(toks, i + 2)
        return {"type": text, "element": element}, expect(toks, i, ")")
    if text == "object" and toks[i + 1].text == "(":
        i = expect(toks, i + 2, "{")
        attributes = {}
        while toks[i].text != "}":
            name = toks[i].text.strip('"')
            i = i + 1
            if toks[i].text not in ("=", ":"):
                raise ValueError(f"object attribute {name}: expected = or :")
            optional, default = False, None
            if toks[i + 1].text == "optional" and toks[i + 2].text == "(":
                optional = True
                inner, i = parse_type(toks, i + 3)
                if toks[i].text == ",":
                    value, i = expression(toks, i + 1, {")"})
                    default = source(value)
                i = expect(toks, i, ")")
            else:
                inner, i = parse_type(toks, i + 1)
            attributes[name] = {**inner, "optional": optional, "default": default}
            if toks[i].text == ",":
                i += 1
        return {"type": "object", "attributes": attributes}, expect(toks, expect(toks, i, "}"), ")")
    if text == "tuple" and toks[i + 1].text == "(":
        return {"type": "tuple"}, past(toks, i + 1)  # a tuple's elements are not inputs of their own
    if kind == "name":
        return {"type": text}, i + 1
    raise ValueError(f"cannot read the type at {text!r}")


def expect(toks: list[Token], i: int, text: str) -> int:
    if i >= len(toks) or toks[i].text != text:
        raise ValueError(f"expected {text!r}, found {toks[i].text if i < len(toks) else 'the end'!r}")
    return i + 1


def blocks(toks: list[Token]):
    """Each top-level `variable "name" { ... }`: its name and its body's attributes as token lists."""
    i = 0
    while i < len(toks):
        if toks[i].text == "variable" and toks[i + 1].kind == "string" and toks[i + 2].text == "{":
            name, i = toks[i + 1].text.strip('"'), i + 3
            attributes = {}
            while toks[i].text != "}":
                if toks[i].kind == "newline":
                    i += 1
                elif toks[i + 1].text == "=":
                    attribute = toks[i].text
                    attributes[attribute], i = expression(toks, i + 2, set())
                else:  # a nested block, such as validation: skipped whole
                    while toks[i].text != "{":
                        i += 1
                    i = past(toks, i)
            yield name, attributes
        i += 1


def past(toks: list[Token], i: int) -> int:
    """The index after the bracket that closes the one at i."""
    depth = 0
    for j in range(i, len(toks)):
        depth += 1 if toks[j].text in OPEN else -1 if toks[j].text in CLOSE else 0
        if depth == 0:
            return j + 1
    raise ValueError("a bracket is not closed")


def flatten(path: str, node: dict, required: bool, default: str | None, out: dict) -> None:
    """Every path of a variable, in declaration order: its type in short, whether it must be given (in its object),
    and its default as HCL. A collection of objects passes through to the objects' attributes."""
    inner, shape = node, []
    while inner["type"] in ("list", "set", "map"):
        shape.append(inner["type"])
        inner = inner["element"]
    out[path] = {"type": "".join(f"{s}(" for s in shape) + inner["type"] + ")" * len(shape), "required": required}
    if not required:
        out[path]["default"] = default
    for name, attribute in inner.get("attributes", {}).items():
        flatten(f"{path}.{name}", attribute, not attribute["optional"], attribute["default"], out)


def variables(folder: Path) -> dict[str, dict]:
    """Every input path of the module in a folder, from its variable blocks."""
    out: dict[str, dict] = {}
    # variables.tf first: a module's own inputs, before those it shares with others (variables.azapi.tf and the like).
    for tf in sorted(folder.glob("*.tf"), key=lambda tf: (tf.name != "variables.tf", tf.name)):
        for name, attributes in blocks(tokens(tf.read_text())):
            node = parse_type(attributes["type"])[0] if "type" in attributes else {"type": "any"}
            flatten(name, node, "default" not in attributes, source(attributes["default"]) if "default" in attributes else None, out)
    return out


# --- Which module calls each root makes, at which pins --------------------------------------------------------------

ROOTS = ("bootstrap/terraform", "governance/terraform", "platform/terraform", "platform/networks/terraform",
         "platform/rules/terraform", "vending/terraform")


def downloaded(root: Path) -> dict[str, dict]:
    """The module calls a root makes, from the modules terraform get installed: the root's own, and those of a module
    of mlz-core's own it calls, as module.network.module.peering is (key network.peering). Not the calls inside
    another module, which are that module's business."""
    listed = json.loads((root / ".terraform" / "modules" / "modules.json").read_text())["Modules"]
    own = {m["Key"] for m in listed if m["Key"] and not m["Dir"].startswith(".terraform")}
    calls = {}
    for m in sorted(listed, key=lambda m: m["Key"]):
        parent = m["Key"].rpartition(".")[0]
        if m["Key"] and (parent == "" or parent in own):
            calls[m["Key"]] = {"source": m["Source"], "version": m.get("Version", ""), "dir": root / m["Dir"]}
    return calls


def refresh() -> dict:
    """The snapshot: each root's module calls, their pins, and every input of each."""
    snapshot = {}
    for root in ROOTS:
        run = subprocess.run(["terraform", f"-chdir={CORE / root}", "get"], capture_output=True, text=True)
        if run.returncode != 0:
            raise SystemExit(f"mlz inputs: terraform get in {root} failed:\n{run.stderr or run.stdout}")
        snapshot[root] = {key: {"source": call["source"], "version": call["version"], "inputs": variables(call["dir"])}
                          for key, call in downloaded(CORE / root).items()}
    return snapshot


def pins(folder: Path, prefix: str = "") -> dict[str, tuple[str, str]]:
    """The module calls in a root's .tf files, and in those of a module of mlz-core's own it calls: key to (source,
    version), as the snapshot has them. What the tests hold the snapshot to, with no download."""
    found = {}
    for tf in sorted(folder.glob("*.tf")):
        for block in CALL.finditer(tf.read_text()):
            source = SOURCE.search(block["body"])["source"]
            version = VERSION.search(block["body"])
            key = prefix + block["name"]
            found[key] = (source, version["version"] if version else "")
            if source.startswith("."):
                found.update(pins((folder / source).resolve(), key + "."))
    return found


CALL = re.compile(r'^module\s+"(?P<name>[^"]+)"\s*\{(?P<body>.*?)^\}', re.MULTILINE | re.DOTALL)
SOURCE = re.compile(r'^\s*source\s*=\s*"(?P<source>[^"]+)"', re.MULTILINE)
VERSION = re.compile(r'^\s*version\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)
REGISTRY = "registry.terraform.io/"


def snapshot_pins(snapshot: dict, root: str) -> dict[str, tuple[str, str]]:
    return {key: (call["source"].removeprefix(REGISTRY), call["version"]) for key, call in snapshot.get(root, {}).items()}


def load_snapshot() -> dict:
    return json.loads(SNAPSHOT.read_text())


def snapshot_text(snapshot: dict) -> str:
    """One line per input path, so a moved pin shows as the lines it changes."""
    lines = ["{"]
    for r, (root, calls) in enumerate(snapshot.items()):
        lines.append(f" {json.dumps(root)}: {{")
        for c, (key, call) in enumerate(calls.items()):
            lines += [f"  {json.dumps(key)}: {{", f"   \"source\": {json.dumps(call['source'])},",
                      f"   \"version\": {json.dumps(call['version'])},", "   \"inputs\": {"]
            items = list(call["inputs"].items())
            lines += [f"    {json.dumps(path)}: {json.dumps(info)}{',' if i < len(items) - 1 else ''}" for i, (path, info) in enumerate(items)]
            lines += ["   }", "  }" + ("," if c < len(calls) - 1 else "")]
        lines.append(" }" + ("," if r < len(snapshot) - 1 else ""))
    return "\n".join(lines + ["}"]) + "\n"


# --- The maps ---------------------------------------------------------------------------------------------------

MAP = "inputs.yaml"
# The customer's files a setting can come from: how a page names each, by its schema (schemas/<kind>.schema.json).
FILES = {
    "customer": "`customer.yaml`",
    "bootstrap": "`bootstrap.yaml`",
    "connectivity": "`connectivity/<region>.yaml`",
    "firewall-rules": "a rules file in `firewall-rules/`",
    "landing-zone": "a landing zone's file or a layer above it",
}
# Where names: goes, where that is narrower than the file's settings (the landing zone schema's names).
NAMES_IN = {"landing-zone": "the landing zone's own file, `landing-zones/<workload>/<env>.yaml`"}
# Each class: the fields it needs, and those it may have.
CLASSES = {
    "setting": ({"key"}, {"file", "by"}),
    "derived": ({"by"}, {"names"}),
    "fixed": ({"why"}, {"value", "from"}),
    "open": (set(), {"why"}),
    "closed": ({"why"}, set()),
}
CALL_FIELDS = {"builds", "file", "notes", "inputs"}


def load_maps() -> dict[str, dict]:
    from .files import preload, read_yaml
    paths = [CORE / root / MAP for root in ROOTS if (CORE / root / MAP).is_file()]
    preload(paths)
    return {root: read_yaml(CORE / root / MAP) for root in ROOTS if (CORE / root / MAP).is_file()}


def entry(value) -> dict:
    """An entry as a mapping: `open` alone is short for {class: open}."""
    return {"class": "open"} if value == "open" else value if isinstance(value, dict) else {"class": value}


def leaves(paths) -> list[str]:
    paths = list(paths)
    return [p for p in paths if not any(q.startswith(p + ".") for q in paths)]


def covering(path: str, entries) -> list[str]:
    """The entries that classify a path: itself, or a path above it."""
    return [e for e in entries if path == e or path.startswith(e + ".")]


def accepts(schema: dict, key: str) -> bool:
    """Whether a file's schema takes a dotted key: properties by name, a map's entries by * or an allowed name, a
    list's items as the list itself."""
    node = schema
    for part in key.split("."):
        while node and "properties" not in node and "additionalProperties" not in node and ("items" in node or "anyOf" in node):
            node = node["items"] if "items" in node else next((n for n in node["anyOf"] if "properties" in n or "items" in n), None)
        if not node:
            return False
        if part in node.get("properties", {}):
            node = node["properties"][part]
        elif isinstance(node.get("additionalProperties"), dict) and (part == "*" or part in node.get("propertyNames", {}).get("enum", [part])):
            node = node["additionalProperties"]
        else:
            return False
    return True


def schemas() -> dict[str, dict]:
    from . import schema
    return {kind: schema.SCHEMAS[f"{kind}.schema.json"]() for kind in FILES}


def problems(snapshot: dict, maps: dict[str, dict]) -> list[str]:
    """Everything wrong with the maps: an input without exactly one entry, an entry for no input, a setting or a
    names: override no loader accepts, a field a class does not take."""
    found, accepted = [], schemas()
    for root in ROOTS:
        calls, mapped = snapshot.get(root, {}), maps.get(root, {})
        where = f"{root}/{MAP}"
        found += [f"{where}: no section for module.{key.replace('.', '.module.')}" for key in calls if key not in mapped]
        found += [f"{where}: {key} is no module call of this root" for key in mapped if key not in calls]
        for key, call in calls.items():
            section = mapped.get(key)
            if not isinstance(section, dict):
                continue
            at = f"{where}: {key}"
            found += [f"{at}: {field} is not a field; use {', '.join(sorted(CALL_FIELDS))}" for field in section if field not in CALL_FIELDS]
            if not section.get("builds"):
                found.append(f"{at}: builds is required: what the call makes, in a line")
            if section.get("file") is not None and section["file"] not in FILES:
                found.append(f"{at}: file {section['file']!r} is not one of {', '.join(FILES)}")
            paths = call["inputs"]
            if not isinstance(section.get("inputs") or {}, dict):
                found.append(f"{at}: inputs maps each input's path to its entry")
                continue
            entries = {path: entry(value) for path, value in (section.get("inputs") or {}).items()}
            found += [f"{at}: notes.{path} is no input of the module" for path in (section.get("notes") or {}) if path not in paths]
            for path, item in entries.items():
                found += [f"{at}: {path}: {text}" for text in entry_problems(path, item, paths, section.get("file"), accepted)]
            for path in leaves(paths):
                count = len(covering(path, entries))
                if count != 1:
                    found.append(f"{at}: {path} has {'no entry' if count == 0 else f'{count} entries'}; it needs exactly one")
    return found


def entry_problems(path: str, item: dict, paths: dict, default_file: str | None, accepted: dict) -> list[str]:
    if path not in paths:
        return ["no such input in the module"]
    kind = item.get("class")
    if kind not in CLASSES:
        return [f"class {kind!r} is not one of {', '.join(CLASSES)}"]
    needed, allowed = CLASSES[kind]
    found = [f"{kind} needs {field}" for field in sorted(needed - set(item))]
    found += [f"{kind} takes no {field}" for field in sorted(set(item) - needed - allowed - {"class"})]
    found += [f"{field} must be text" for field in ("key", "by", "why", "file", "from") if field in item and not (isinstance(item[field], str) and item[field].strip())]
    names = item.get("names")
    if "names" in item and not (isinstance(names, str) or isinstance(names, list) and names and all(isinstance(n, str) for n in names)):
        found.append("names must be a key under names:, or a list of them")
    if found:
        return found
    file = item.get("file", default_file)
    if kind == "setting" or "names" in item:
        if file not in FILES:
            return found + [f"no file to find {item.get('key') or 'names'} in: give the call or the entry a file ({', '.join(FILES)})"]
        if kind == "setting" and isinstance(item.get("key"), str) and not accepts(accepted[file], item["key"]):
            found.append(f"{FILES[file]} takes no {item['key']}")
        for name in [item["names"]] if isinstance(item.get("names"), str) else item.get("names") or []:
            if not accepts(accepted[file], f"names.{name}"):
                found.append(f"{FILES[file]} takes no names.{name}")
    if kind == "fixed" and "value" in item and "from" in item:
        found.append("fixed takes value or from, not both")
    if kind == "fixed" and "from" in item and not (CORE / item["from"]).is_file():
        found.append(f"from: {item['from']} is not a file in mlz-core")
    return found


# --- The pages ----------------------------------------------------------------------------------------------------

LEGEND = """Each input is in one class:

| Class | Meaning | Can a customer set it? |
|---|---|---|
| setting | from a key in the customer's YAML | yes, with that key |
| derived | worked out by mlz-core: from the layout, the IP allocator, a contract or the naming | no; a name, under `names:` where the entry says so |
| fixed | Fortytwo's decision for every customer | no |
| open | mlz-core does not set it, so the module's default applies | not yet |
| closed | not supported yet | no |

An entry covers its input and everything in it: `bastion` closed means every attribute of `bastion`. A map or list of
objects passes through to the objects' attributes (`hub_virtual_networks.firewall.sku_tier` is each hub's)."""


def page_name(root: str) -> str:
    return root.removesuffix("/terraform").replace("/", "-") + ".md"


def module_label(call: dict) -> str:
    source, version = call["source"].removeprefix(REGISTRY), call["version"]
    git = re.match(r"^git::https://github\.com/[^/]+/(?P<repo>[^/.]+)\.git(?://(?P<path>[^?]+))?\?ref=(?P<ref>.+)$", source)
    if git:
        return f"{git['repo']}{'//' + git['path'] if git['path'] else ''} {git['ref']}"
    if source.startswith("."):
        return f"{(Path(source)).as_posix().lstrip('./')} (mlz-core)"
    return f"{source} {version}"


def cell(text: str) -> str:
    """Text for a table cell: | escaped, and < and > outside `code`, where markdown would read <region> as a tag."""
    parts = text.replace("\n", " ").split("`")
    return "`".join(part if i % 2 else part.replace("<", "&lt;").replace(">", "&gt;") for i, part in enumerate(parts)).replace("|", "\\|")


def short(value: str | None, limit: int = 60) -> str:
    """A default as a page shows it: whole when short."""
    text = "null" if value is None else value
    return f"`{text}`" if len(text) <= limit else "see the module"


def describe(item: dict, info: dict, file: str | None) -> str:
    kind = item["class"]
    if kind == "setting":
        text = f"`{item['key']}` in {FILES[item.get('file', file)]}"
        return text + (f": {item['by']}" if item.get("by") else "")
    if kind == "derived":
        names = [item["names"]] if isinstance(item.get("names"), str) else item.get("names") or []
        override = " or ".join(f"`names.{n}`" for n in names)
        return item["by"] + (f"; set it with {override} in {NAMES_IN.get(file, FILES[file])}" if names else "")
    if kind == "fixed":
        if "value" in item:
            value = f"`{json.dumps(item['value'])}`"
        elif "from" in item:
            value = f"the list in `{item['from']}`"
        else:
            value = f"the module's default ({short(info.get('default'))})" if not info["required"] else "the module's default"
        return f"{value}: {item['why']}"
    if kind == "open":
        default = "required" if info["required"] else f"the module's default: {short(info.get('default'))}"
        return default + (f"; {item['why']}" if item.get("why") else "")
    return item["why"]


def page(root: str, calls: dict, mapped: dict) -> str:
    from .stacks import STACKS
    stacks = [s.name for s in STACKS if s.root == root]
    lines = [f"# {root}: who owns each input", "",
             f"<!-- Written by python3 -m mlz reference from {root}/{MAP} and tools/mlz/module-variables.json; do not edit. -->", "",
             f"The Terraform root of the stack{'s' if len(stacks) > 1 else ''} {', '.join(f'`{s}`' for s in stacks)}: every input of every "
             f"module call it makes, and who owns it. The map is `{root}/{MAP}`; `tools/mlz/inputs.py` says how it is checked.", "",
             LEGEND]
    for key, call in calls.items():
        section = mapped[key]
        entries = {path: entry(value) for path, value in (section.get("inputs") or {}).items()}
        notes = section.get("notes") or {}
        counts = {kind: sum(1 for e in entries.values() if e["class"] == kind) for kind in CLASSES}
        builds = section["builds"].rstrip(".")
        lines += ["", f"## module.{key.replace('.', '.module.')}: {module_label(call)}", "", cell(builds[0].upper() + builds[1:]) + ".", ""]
        if section.get("file"):
            lines += [f"Settings from {cell(FILES[section['file']])}.", ""]
        lines += [f"{sum(counts.values())} entries: " + ", ".join(f"{n} {kind}" for kind, n in counts.items() if n) + ".", "",
                  "| Input | Type | Class | Source, value or reason |", "|---|---|---|---|"]
        for path, info in call["inputs"].items():
            if path in entries:
                item = entries[path]
                text = describe(item, info, section.get("file"))
                if path in notes:
                    text = f"{notes[path]}. {text}"
                lines.append(f"| `{path}` | {info['type']} | {item['class']} | {cell(text)} |")
            elif path in notes:
                lines.append(f"| `{path}` | {info['type']} | | {cell(notes[path])} |")
    return "\n".join(lines) + "\n"


def index(snapshot: dict, maps: dict) -> str:
    from .stacks import STACKS
    lines = ["# Who owns each module input", "",
             "<!-- Written by python3 -m mlz reference; do not edit. -->", "",
             "One page per Terraform root in mlz-core: each module call the root makes, and for every input of the module, "
             "who owns it. Made from the input map beside each root (`inputs.yaml`), so it cannot fall behind a root or a pin:",
             "`tools/mlz/inputs.py` says how.", "",
             "| Root | Stacks | Module calls | Entries: setting / derived / fixed / open / closed |", "|---|---|---|---|"]
    for root, calls in snapshot.items():
        counts = dict.fromkeys(CLASSES, 0)
        for key in calls:
            for value in (maps[root][key].get("inputs") or {}).values():
                counts[entry(value)["class"]] += 1
        stacks = ", ".join(f"`{s.name}`" for s in STACKS if s.root == root)
        modules = "<br>".join(f"`{key}`: {module_label(call)}" for key, call in calls.items())
        lines.append(f"| [{root}]({page_name(root)}) | {stacks} | {modules} | {' / '.join(str(n) for n in counts.values())} |")
    return "\n".join(lines) + "\n"


def render(snapshot: dict | None = None, maps: dict | None = None) -> dict[str, str]:
    """Every page's file name and text; SystemExit listing every problem when the maps are not right."""
    snapshot = load_snapshot() if snapshot is None else snapshot
    maps = load_maps() if maps is None else maps
    found = problems(snapshot, maps)
    if found:
        raise SystemExit("mlz reference: the input maps are not right:\n  " + "\n  ".join(found))
    pages = {page_name(root): page(root, calls, maps[root]) for root, calls in snapshot.items()}
    return {"README.md": index(snapshot, maps), **pages}


def write(out: Path) -> list[str]:
    out.mkdir(parents=True, exist_ok=True)
    pages = render()
    for name, text in pages.items():
        (out / name).write_text(text)
    return sorted(pages)


def main(check: bool) -> int:
    """--refresh writes the snapshot; --check says what differs from it and fails."""
    text = snapshot_text(refresh())
    if not check:
        SNAPSHOT.write_text(text)
        print(f"mlz inputs: wrote {SNAPSHOT.relative_to(CORE)}")
        return 0
    if SNAPSHOT.read_text() == text:
        print(f"mlz inputs: {SNAPSHOT.relative_to(CORE)} is what the pinned modules declare")
        return 0
    import difflib
    sys.stdout.writelines(difflib.unified_diff(SNAPSHOT.read_text().splitlines(True), text.splitlines(True), "committed", "pinned modules"))
    print(f"mlz inputs: {SNAPSHOT.relative_to(CORE)} is not what the pinned modules declare; run python3 -m mlz inputs --refresh, "
          "then classify every new input in its root's inputs.yaml and run python3 -m mlz reference --out docs/reference.", file=sys.stderr)
    return 1
