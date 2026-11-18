"""What each settings file feeds: its stack, the stack's Terraform root and the modules that root calls.

    python3 -m mlz map --repo <a customer repo>

One table, STACKS, says it for every role; the pipeline's own list of stacks (tools/ci/check.sh) must agree with
it, which a test checks. A folder is named after its stack, so the table is short: the folder's files feed the stack
of the same name. The modules are read from the roots, so they are always the ones this release pins.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from . import contract, vending
from .files import SUFFIXES
from .upgrade import WORKFLOW, role_of

CORE = Path(__file__).resolve().parents[2]


class Stack(NamedTuple):
    role: str
    name: str  # as the pipeline names it: its state file, its plan comment, its apply
    reads: str  # the files its loader reads, as a pattern on the path in the repo
    root: str  # relative to mlz-core
    note: str = ""


# A setting is a file, x.yaml, or a folder of chunks, x/*.yaml, read as one file (tools/mlz/files.py).
SETTING = r"(\.ya?ml|/[^/]+\.ya?ml)"
STACKS = (
    Stack("governance", "bootstrap", r"bootstrap\.ya?ml", "bootstrap/terraform", "run by mlz install from a laptop, not by the pipeline"),
    Stack("governance", "governance", r"customer\.ya?ml", "governance/terraform"),
    Stack("platform", "connectivity", rf"connectivity/[^/]+{SETTING}", "platform/terraform"),
    Stack("platform", "management", rf"management/[^/]+{SETTING}", "platform/networks/terraform", "built in the primary region with or without a file"),
    Stack("platform", "identity", rf"identity/[^/]+{SETTING}", "platform/networks/terraform", "built in each region with a file"),
    Stack("platform", "firewall-rules", rf"firewall-rules/((platform|management|identity)|landing-zones/[^/]+){SETTING}", "platform/rules/terraform"),
    Stack("vending", "<workload>-<env>", rf"(defaults/[^/]+-defaults|landing-zones/[^/]+/[^/]+){SETTING}", "vending/terraform", "one stack per landing zone"),
)
# Files in a role's repo that no stack reads.
NOT_READ = {"vending": ("examples/",)}
# The files in _generated/ and who writes each; a contract says what it is in its own header (tools/mlz/contract.py).
WRITERS = {
    "bootstrap.yaml": "mlz install publish",
    "governance.yaml": "the governance repo's pipeline, after each apply",
    "platform.yaml": "the platform repo's pipeline, after each apply",
    "vending.yaml": "the vending repo's pipeline, after each apply",
    "ip-plan.yaml": "the IP allocator, on vending's pull requests: every landing zone's range and subnets",
    "names.yaml": "the IP allocator, on vending's pull requests: the names each landing zone was given",
}
# Built by another role than the one whose repo looks like its home.
ELSEWHERE = {
    "platform": ("management's Log Analytics workspace and its group: governance, from customer.yaml",
                 "the hubs' peerings with landing zones: vending, as each landing zone is made"),
    "vending": ("each landing zone's firewall rules: the platform repo's firewall-rules/landing-zones/<key>.yaml",),
}
MODULE = re.compile(r'^module\s+"[^"]+"\s*\{(?P<body>.*?)^\}', re.MULTILINE | re.DOTALL)
SOURCE = re.compile(r'^\s*source\s*=\s*"(?P<source>[^"]+)"', re.MULTILINE)
VERSION = re.compile(r'^\s*version\s*=\s*"(?P<version>[^"]+)"', re.MULTILINE)
GIT = re.compile(r"^git::https://github\.com/[^/]+/(?P<repo>[^/.]+)\.git(?://(?P<path>[^?]+))?\?ref=(?P<ref>.+)$")


def modules(root: Path) -> list[str]:
    """The modules a root calls, with their pins; a local module's own calls follow it, indented."""
    found = []
    for tf in sorted(root.glob("*.tf")):
        for block in MODULE.finditer(tf.read_text()):
            source = SOURCE.search(block["body"])["source"]
            version = VERSION.search(block["body"])
            git = GIT.match(source)
            if git:
                found.append(f"{git['repo']}{'/' + git['path'] if git['path'] else ''} {git['ref']}")
            elif source.startswith("."):
                local = (root / source).resolve()
                found.append(f"{local.relative_to(CORE)} (mlz-core)")
                found += [f"  {inner}" for inner in modules(local)]
            else:
                found.append(f"{source} {version['version'] if version else ''}".rstrip())
    return found


def role(repo: Path) -> str:
    """The repo's role, from its pipeline: the starter's workflow names it."""
    workflow = repo / WORKFLOW
    if not workflow.is_file():
        raise SystemExit(f"mlz map: {workflow} is not there; run it in a governance, platform or vending repo.")
    return role_of(workflow.read_text())


def settings_files(repo: Path) -> list[str]:
    """Every YAML file a person writes, outside _generated/ and hidden folders, sorted."""
    return sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if p.is_file() and p.suffix in SUFFIXES
                  and not any(part.startswith((".", "_")) for part in p.relative_to(repo).parts))


def stack_of(role_: str, path: str) -> Stack | None:
    """The stack whose loader reads the file; None for a file no loader reads, such as one a folder too deep."""
    return next((s for s in STACKS if s.role == role_ and re.fullmatch(s.reads, path)), None)


def feeds(stack: Stack, path: str) -> str:
    """The stacks a file feeds: in vending, the landing zones its layer reaches (tools/mlz/vending.py)."""
    if stack.role != "vending":
        return stack.name
    parts = Path(path).with_suffix("").parts
    if parts[0] == vending.DEFAULTS:
        return "every landing zone" if parts[1] == vending.ALL_DEFAULTS else f"every landing zone in {parts[1].removesuffix('-defaults')}"
    workload = parts[1]
    return f"every landing zone of {workload}" if len(parts) > 2 and parts[2] == vending.WORKLOAD_DEFAULTS else f"{workload}-{parts[2]}"


def generated_by(name: str) -> str:
    kind = name.removesuffix(".yaml")
    what = contract.CONTRACTS[kind]["header"].splitlines()[0] if kind in contract.CONTRACTS else ""
    return f"written by {WRITERS[name]}" + (f". {what}" if what else "") if name in WRITERS else "not mlz-core's"


def render(repo: Path) -> str:
    role_ = role(repo)
    stacks = [s for s in STACKS if s.role == role_]
    lines = [f"{repo.resolve().name}: the {role_} repo", "", "Settings files:"]
    files = settings_files(repo)
    width = max([len(f) for f in files] + [10])
    for path in files:
        stack = stack_of(role_, path)
        skipped = any(path.startswith(folder) for folder in NOT_READ.get(role_, ()))
        lines.append(f"  {path:<{width}}  -> {feeds(stack, path) if stack else 'example: not read' if skipped else 'NOT READ by any stack'}")
    lines += ["", "Stacks (Terraform root in mlz-core, then its modules):"]
    for stack in stacks:
        lines.append(f"  {stack.name}: {stack.root}" + (f" ({stack.note})" if stack.note else ""))
        lines += [f"    {module}" for module in modules(CORE / stack.root)]
    generated = sorted(p.name for p in (repo / "_generated").glob("*.yaml")) if (repo / "_generated").is_dir() else []
    lines += ["", "_generated/ (written by a pipeline, never edited):"]
    lines += [f"  {name}: {generated_by(name)}" for name in generated] or ["  nothing yet"]
    if role_ in ELSEWHERE:
        lines += ["", "Built by another role:"] + [f"  {item}" for item in ELSEWHERE[role_]]
    return "\n".join(lines) + "\n"
