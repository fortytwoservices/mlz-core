"""Writing YAML people read: block mappings, short lists of plain values on one line, quotes only where
YAML would read a value differently (numbers, true/false/null, special characters).

Standard library only. Every file it writes is read back by terraform's yamldecode in the tests.
"""

from __future__ import annotations

import json
import re

PLAIN = re.compile(r"^[A-Za-z0-9_./@][A-Za-z0-9_./@:+*()-]*$")
LOOKS_LIKE_OTHER_TYPE = re.compile(
    r"^(true|false|yes|no|on|off|null|~|[-+]?(\d[\d_]*)?\.?\d+([eE][-+]?\d+)?|0x[0-9a-fA-F]+|0o[0-7]+|\.inf|\.nan)$",
    re.IGNORECASE,
)
FLOW_WIDTH = 100


def scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    text = str(value)
    if PLAIN.match(text) and not LOOKS_LIKE_OTHER_TYPE.match(text) and ": " not in text and " #" not in text:
        return text
    return json.dumps(text)  # a double-quoted YAML scalar uses JSON's escapes


def dump(data: dict, header: str = "", comments: dict[str, str] | None = None, spaced: bool = False) -> str:
    """The mapping as YAML, after the header. `comments` puts a comment line above top-level keys;
    `spaced` puts a blank line between all of them."""
    lines = [f"# {line}" if line else "#" for line in header.splitlines()]
    if lines:
        lines.append("")
    for index, (key, value) in enumerate(data.items()):
        note = (comments or {}).get(key)
        if (note or spaced) and index > 0:
            lines.append("")
        if note:
            lines += [f"# {line}" for line in note.splitlines()]
        lines += _entry(key, value, 0)
    return "\n".join(lines) + "\n"


def _entry(key, value, indent: int) -> list[str]:
    pad = "  " * indent
    key = scalar(str(key))  # keys too: YAML reads a key "on" or "no" as a boolean
    if isinstance(value, dict):
        if not value:
            return [f"{pad}{key}: {{}}"]
        out = [f"{pad}{key}:"]
        for k, v in value.items():
            out += _entry(k, v, indent + 1)
        return out
    if isinstance(value, list):
        return [f"{pad}{key}:{_list(value, indent)}"] if value else [f"{pad}{key}: []"]
    return [f"{pad}{key}: {scalar(value)}"]


def _list(items: list, indent: int) -> str:
    if all(not isinstance(i, (dict, list)) for i in items):
        flow = "[" + ", ".join(scalar(i) for i in items) + "]"
        if len(flow) <= FLOW_WIDTH:
            return " " + flow
        return "".join(f"\n{'  ' * (indent + 1)}- {scalar(i)}" for i in items)
    out = ""
    for item in items:
        if isinstance(item, dict):
            first = True
            for k, v in item.items():
                lines = _entry(k, v, indent + 2)
                lines[0] = ("  " * (indent + 1)) + "- " + lines[0].lstrip() if first else lines[0]
                out += "\n" + "\n".join(lines)
                first = False
        else:
            out += f"\n{'  ' * (indent + 1)}- {scalar(item)}"
    return out
