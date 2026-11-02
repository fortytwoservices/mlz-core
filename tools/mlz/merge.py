"""How mlz-core combines YAML files, the same way for every role.

Two kinds of combining, with different rules on purpose:

- Chunks: one setting split over several files in a folder (hubs/norwayeast/network.yaml,
  hubs/norwayeast/firewall.yaml, ...). The result is as if it were one file. Mappings are merged at any
  depth, and two chunks may not set the same value: that is an error naming both files, so splitting a
  file can never silently override something.
- Layers: a later file refining an earlier one (defaults, then workload, then environment). Mappings are
  merged at any depth; a list or a single value in the later layer replaces the earlier one.
"""

from __future__ import annotations

from pathlib import Path


class ConfigError(Exception):
    pass


def layer(base: dict, override: dict) -> dict:
    """The later layer over the earlier one: mappings merge, everything else is replaced."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = layer(merged[key], value)
        else:
            merged[key] = value
    return merged


def combine_chunks(chunks: list[tuple[Path, dict]]) -> dict:
    """Sibling files as one: mappings merge, and a value set by two of them is an error."""
    merged: dict = {}
    origin: dict[tuple, Path] = {}

    def add(target: dict, source: dict, path: tuple, file: Path) -> None:
        for key, value in source.items():
            here = (*path, key)
            if key not in target:
                target[key] = _copy(value)
                _record(origin, here, value, file)
            elif isinstance(target[key], dict) and isinstance(value, dict):
                add(target[key], value, here, file)
            else:
                first = _first_origin(origin, here)
                raise ConfigError(
                    f"{'.'.join(map(str, here))} is set in both {first} and {file}; set it in one of them."
                )

    for file, data in chunks:
        add(merged, data, (), file)
    return merged


def _copy(value):
    return {k: _copy(v) for k, v in value.items()} if isinstance(value, dict) else value


def _record(origin: dict, path: tuple, value, file: Path) -> None:
    origin[path] = file
    if isinstance(value, dict):
        for key, child in value.items():
            _record(origin, (*path, key), child, file)


def _first_origin(origin: dict, path: tuple) -> Path:
    while path and path not in origin:
        path = path[:-1]
    return origin.get(path, Path("?"))
