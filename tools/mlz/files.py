"""Reading the customer's YAML: one file, or a folder of chunks that stand for one file.

YAML is parsed by terraform's yamldecode, so mlz-core needs no YAML package. Starting terraform is the
slow part, so `preload` parses many files in one run and `read_yaml` keeps what it has parsed, keyed by
the file's content.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .merge import ConfigError, combine_chunks

SUFFIXES = (".yaml", ".yml")
UNREADABLE = "mlz:unreadable"
_parsed: dict[bytes, object] = {}


def _empty(raw: bytes) -> bool:
    """Only comments: "no settings yet". yamldecode refuses an empty document."""
    return not any(line.strip() and not line.lstrip().startswith("#") for line in raw.decode().splitlines())


def _decode(contents: list[bytes]) -> tuple[subprocess.CompletedProcess, list]:
    """Parses the files in one terraform run; a file that does not parse comes back as UNREADABLE."""
    if shutil.which("terraform") is None:
        raise ConfigError("terraform is not on the PATH. mlz-core reads YAML with it.")
    # Copies under fixed names, so no path (quotes, ${...}) ever goes into the expression.
    with tempfile.TemporaryDirectory() as folder:
        for index, raw in enumerate(contents):
            (Path(folder) / f"{index}.yaml").write_bytes(raw)
        names = ", ".join(f'"{index}.yaml"' for index in range(len(contents)))
        run = subprocess.run(
            ["terraform", f"-chdir={folder}", "console"],
            input=f'jsonencode([for f in [{names}] : try(yamldecode(file(f)), "{UNREADABLE}")])',
            capture_output=True, text=True,
        )
    # console prints the JSON as an HCL string literal, whose escapes are JSON's.
    return run, json.loads(json.loads(run.stdout.strip())) if run.returncode == 0 else []


def preload(paths) -> None:
    """Parses every given file not parsed yet in one terraform run, for read_yaml to find."""
    contents = [raw for raw in dict.fromkeys(p.read_bytes() for p in paths) if raw not in _parsed and not _empty(raw)]
    if len(contents) > 1:
        _, results = _decode(contents)
        _parsed.update((raw, data) for raw, data in zip(contents, results) if data != UNREADABLE)


def read_yaml(path: Path) -> dict:
    """The settings in one YAML file. Empty is {}."""
    raw = path.read_bytes()
    if _empty(raw):
        return {}
    if raw not in _parsed:
        run, results = _decode([raw])
        if not results or results[0] == UNREADABLE:
            # Once more on its own, without try(), for terraform's own words on what is wrong.
            with tempfile.TemporaryDirectory() as folder:
                (Path(folder) / "input.yaml").write_bytes(raw)
                run = subprocess.run(["terraform", f"-chdir={folder}", "console"], input='yamldecode(file("input.yaml"))', capture_output=True, text=True)
            raise ConfigError(f"{path}: not valid YAML:\n{run.stderr or run.stdout}")
        _parsed[raw] = results[0]
    data = copy.deepcopy(_parsed[raw])
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected settings (key: value) at the top level.")
    return data


def yaml_files(folder: Path) -> list[Path]:
    """Every YAML file under a folder, for preload."""
    return [p for p in sorted(folder.rglob("*")) if p.suffix in SUFFIXES and p.is_file()] if folder.is_dir() else []


def read_setting(base: Path) -> dict | None:
    """`<base>.yaml`, or the chunks in folder `<base>/`, or None when neither exists. Both is an error."""
    files = [base.with_suffix(s) for s in SUFFIXES if base.with_suffix(s).is_file()]
    folder = base if base.is_dir() else None
    if files and folder:
        raise ConfigError(f"{files[0]} and the folder {folder} both define {base.name}; keep one.")
    if len(files) > 1:
        raise ConfigError(f"{base.name} is defined twice: {files[0]} and {files[1]}.")
    if files:
        return read_yaml(files[0])
    if folder:
        chunks = sorted(p for p in folder.iterdir() if p.suffix in SUFFIXES and not p.name.startswith("_"))
        if not chunks:
            raise ConfigError(f"{folder} has no .yaml files.")
        return combine_chunks([(p, read_yaml(p)) for p in chunks])
    return None


def setting_names(folder: Path) -> list[str]:
    """The settings in a folder: `x.yaml` files and `x/` chunk folders, by name, skipping `_` ones."""
    if not folder.is_dir():
        return []
    names = {p.stem for p in folder.iterdir() if p.is_file() and p.suffix in SUFFIXES}
    names |= {p.name for p in folder.iterdir() if p.is_dir()}
    return sorted(n for n in names if not n.startswith("_") and not n.startswith("."))
