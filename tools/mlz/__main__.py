"""mlz-core's loader from the command line.

    python3 -m mlz platform --repo <mlz-platform> --out <config.json>
    python3 -m mlz allocate --repo <mlz-vending> [--check]
    python3 -m mlz release  --repo <mlz-vending> <key>
    python3 -m mlz vending  --repo <mlz-vending> --out <folder>
    terraform output -json contract | python3 -m mlz contract governance --out <repo>/_generated/governance.yaml
    python3 -m mlz upgrade  <owner>/<repo> [<owner>/<repo> ...] [--to vX.Y.Z] [--dry-run]

A role reads the contracts it needs from its repo's `_generated/` (governance.yaml, platform.yaml);
--governance and --platform point elsewhere. The configs a Terraform root reads are JSON, written for
one run and never committed.

Run with tools/ on the Python path (PYTHONPATH=tools). Exits 1 with every problem listed when the
customer's settings are not valid.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import contract, firewall, platform, upgrade, vending
from .files import preload, read_yaml, yaml_files
from .merge import ConfigError

MARKER = ".written-by-mlz"

def main() -> int:
    parser = argparse.ArgumentParser(prog="mlz", description="Customer YAML in, one checked JSON file per role out.")
    roles = parser.add_subparsers(dest="role", required=True)
    p = roles.add_parser("platform", help="hubs/<region>.yaml into the platform root's config")
    p.add_argument("--repo", type=Path, required=True, help="the customer's mlz-platform checkout")
    p.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    p.add_argument("--out", type=Path, required=True, help="where to write the root's config (JSON)")
    a = roles.add_parser("allocate", help="give new landing zones and subnets their blocks in _generated/ip-plan.yaml")
    a.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    a.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml)")
    a.add_argument("--check", action="store_true", help="write nothing; fail if the IP plan is not what allocate would write (for CI)")
    r = roles.add_parser("release", help="free what a landing zone no longer uses, once it is destroyed")
    r.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    r.add_argument("key", help="the landing zone's key, <workload>-<env>")
    v = roles.add_parser("vending", help="landing-zones/ into one config per landing zone")
    v.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    v.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    v.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml)")
    v.add_argument("--out", type=Path, required=True, help="the folder for <key>.json, one per landing zone; mlz owns it")
    f = roles.add_parser("firewall", help="firewall/ into the rules root's config")
    f.add_argument("--repo", type=Path, required=True, help="the customer's mlz-platform checkout")
    f.add_argument("--platform-config", type=Path, required=True, help="the platform root's config, from mlz platform")
    f.add_argument("--out", type=Path, required=True, help="where to write the rules root's config (JSON)")
    w = roles.add_parser("vending-contract", help="each landing zone's region and range, for the platform's firewall rules")
    w.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    w.add_argument("--out", type=Path, required=True, help="the YAML file to write")
    c = roles.add_parser("contract", help="a root's `contract` output (JSON on stdin) as the commented YAML file")
    c.add_argument("kind", choices=sorted(contract.CONTRACTS))
    c.add_argument("--out", type=Path, required=True, help="the YAML file to write, in a repo's _generated/")
    u = roles.add_parser("upgrade", help="open a pull request moving a customer repo to another mlz-core release")
    u.add_argument("repositories", nargs="+", metavar="owner/repo", help="the customer's GitHub repos to move")
    u.add_argument("--to", metavar="vX.Y.Z", help="the release to move to (default: the latest)")
    u.add_argument("--dry-run", action="store_true", help="show the change and the pull request's text; open nothing")
    args = parser.parse_args()

    if args.role == "upgrade":
        # Repos on GitHub, not a checkout: nothing to preload. One failing repo does not stop the others.
        github, failed = upgrade.GitHub(), 0
        for repository in args.repositories:
            try:
                upgrade.upgrade(github, repository, args.to, args.dry_run)
            except ConfigError as error:
                print(f"mlz upgrade: {repository}: {error}", file=sys.stderr)
                failed += 1
        return 1 if failed else 0

    def published(name: str) -> dict:
        path = getattr(args, name, None) or args.repo / "_generated" / f"{name}.yaml"
        return read_yaml(path)

    # Every file the command reads, parsed in one terraform run before anything is read.
    inputs = {
        "platform": ["hubs"], "firewall": ["firewall", "_generated"], "allocate": ["landing-zones", "_generated"],
        "vending": ["landing-zones", "_generated"], "release": ["landing-zones", "_generated"], "vending-contract": ["landing-zones", "_generated"],
    }
    try:
        if args.role != "contract":
            contracts = [getattr(args, name) for name in ("governance", "platform") if getattr(args, name, None)]
            preload([p for p in contracts if p.is_file()] + [f for folder in inputs[args.role] for f in yaml_files(args.repo / folder)])
        if args.role == "contract":
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(contract.render(args.kind, json.loads(sys.stdin.read())))
            print(f"mlz contract: wrote {args.out}")
        elif args.role == "firewall":
            path = args.repo / "_generated" / "vending.yaml"
            config = firewall.build(args.repo, json.loads(args.platform_config.read_text()), read_yaml(path) if path.exists() else None)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(config, indent=2) + "\n")
            print(f"mlz firewall: wrote {args.out}")
        elif args.role == "vending-contract":
            data = firewall.vending_contract(vending.landing_zones(args.repo), vending.ip_plan(args.repo))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(contract.render("vending", data))
            print(f"mlz vending-contract: wrote {args.out}")
        elif args.role == "platform":
            config = platform.build(args.repo, published("governance"))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(config, indent=2) + "\n")
            print(f"mlz platform: wrote {args.out}")
        elif args.role == "allocate":
            plan, changes, retired = vending.allocate(args.repo, published("platform"))
            text = vending.render_plan(plan, retired)
            if args.check:
                path = args.repo / vending.IP_PLAN
                if (path.read_text() if path.exists() else "") != text:
                    raise ConfigError(
                        f"{vending.IP_PLAN} is not up to date; run python3 -m mlz allocate and commit it."
                        + "".join(f"\n  {c}" for c in changes)
                    )
                print(f"mlz allocate: {vending.IP_PLAN} is up to date")
                return 0
            write_plan(args.repo, text)
            print("mlz allocate: " + (f"{len(changes)} change(s)" if changes else "nothing new") + "".join(f"\n  {c}" for c in changes))
            for key in retired:
                print(f"mlz allocate: {key} was removed but holds its range; once it is destroyed, run: python3 -m mlz release --repo {args.repo} {key}")
        elif args.role == "release":
            plan, retired, freed = vending.release(args.repo, args.key)
            write_plan(args.repo, vending.render_plan(plan, retired))
            print(f"mlz release: freed {freed}")
        else:
            configs = vending.build(args.repo, published("governance"), published("platform"))
            own_folder(args.out)
            for key, config in configs.items():
                (args.out / f"{key}.json").write_text(json.dumps(config, indent=2) + "\n")
            print(f"mlz vending: wrote {len(configs)} landing zone(s) to {args.out}")
    except (ConfigError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"mlz {args.role}: {error}", file=sys.stderr)
        return 1
    return 0


def own_folder(folder: Path) -> None:
    """An empty folder, or one mlz wrote before, emptied of its old configs: a landing zone removed
    since the last run must not leave a config behind for anything to apply."""
    if folder.is_dir() and any(folder.iterdir()) and not (folder / MARKER).is_file():
        raise ConfigError(f"{folder} holds files mlz did not write; use an empty folder.")
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.json"):
        old.unlink()
    (folder / MARKER).write_text("Written by python3 -m mlz vending, which empties this folder on every run.\n")


def write_plan(repo: Path, text: str) -> None:
    path = repo / vending.IP_PLAN
    if not path.exists() or path.read_text() != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


if __name__ == "__main__":
    sys.exit(main())
