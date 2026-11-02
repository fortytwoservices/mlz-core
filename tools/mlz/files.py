"""Reading the customer's YAML: one file, or a folder of chunks that stand for one file."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from .merge import ConfigError, combine_chunks

SUFFIXES = (".yaml", ".yml")


def read_yaml(path: Path) -> dict:
    """Parses YAML with terraform's yamldecode, so mlz-core needs no YAML package. Empty is {}."""
    # A file with only comments is "no settings yet"; yamldecode refuses an empty document.
    if not any(line.strip() and not line.lstrip().startswith("#") for line in path.read_text().splitlines()):
        return {}
    if shutil.which("terraform") is None:
        raise ConfigError("terraform is not on the PATH. mlz-core reads YAML with it.")
    # A copy under a fixed name, so no path (quotes, ${...}) ever goes into the expression.
    with tempfile.TemporaryDirectory() as folder:
        shutil.copyfile(path, Path(folder) / "input.yaml")
        run = subprocess.run(
            ["terraform", f"-chdir={folder}", "console"],
            input='jsonencode(yamldecode(file("input.yaml")))', capture_output=True, text=True,
        )
    if run.returncode != 0:
        raise ConfigError(f"{path}: not valid YAML:\n{run.stderr or run.stdout}")
    # console prints the JSON as an HCL string literal, whose escapes are JSON's.
    data = json.loads(json.loads(run.stdout.strip()))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected settings (key: value) at the top level.")
    return data


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
