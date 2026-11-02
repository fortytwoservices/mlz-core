"""mlz-core's loader from the command line.

    python3 -m mlz platform --repo <mlz-platform> --out <config.json>
    python3 -m mlz allocate --repo <mlz-vending>
    python3 -m mlz vending  --repo <mlz-vending> --out <folder>
    terraform output -json contract | python3 -m mlz contract governance --out <repo>/_generated/governance.yaml

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

from . import contract, platform, vending
from .files import read_yaml
from .merge import ConfigError

IP_PLAN_HEADER = "# Every landing zone's address range. Written by the IP allocator: do not edit.\n# A range never moves once given; removing a landing zone frees it.\n\n"


def main() -> int:
    parser = argparse.ArgumentParser(prog="mlz", description="Customer YAML in, one checked JSON file per role out.")
    roles = parser.add_subparsers(dest="role", required=True)
    p = roles.add_parser("platform", help="hubs/<region>.yaml into the platform root's config")
    p.add_argument("--repo", type=Path, required=True, help="the customer's mlz-platform checkout")
    p.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    p.add_argument("--out", type=Path, required=True, help="where to write the root's config (JSON)")
    a = roles.add_parser("allocate", help="give new landing zones an address range in _generated/ip-plan.yaml")
    a.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    a.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml)")
    v = roles.add_parser("vending", help="landing-zones/ into one config per landing zone")
    v.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    v.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    v.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml)")
    v.add_argument("--out", type=Path, required=True, help="the folder for <key>.json, one per landing zone")
    c = roles.add_parser("contract", help="a root's `contract` output (JSON on stdin) as the commented YAML file")
    c.add_argument("kind", choices=sorted(contract.CONTRACTS))
    c.add_argument("--out", type=Path, required=True, help="the YAML file to write, in a repo's _generated/")
    args = parser.parse_args()

    def published(name: str) -> dict:
        path = getattr(args, name, None) or args.repo / "_generated" / f"{name}.yaml"
        return read_yaml(path)

    try:
        if args.role == "contract":
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(contract.render(args.kind, json.loads(sys.stdin.read())))
            print(f"mlz contract: wrote {args.out}")
        elif args.role == "platform":
            config = platform.build(args.repo, published("governance"))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(config, indent=2) + "\n")
            print(f"mlz platform: wrote {args.out}")
        elif args.role == "allocate":
            new = vending.allocate(args.repo, published("platform"))
            plan_file = args.repo / vending.IP_PLAN
            if new:
                plan_file.parent.mkdir(parents=True, exist_ok=True)
                existing = plan_file.read_text() if plan_file.exists() else IP_PLAN_HEADER
                plan_file.write_text(existing + "".join(f"{key}: {space}\n" for key, space in new.items()))
            print(f"mlz allocate: {len(new)} new range(s)" + "".join(f"\n  {k}: {v}" for k, v in new.items()))
        else:
            configs = vending.build(args.repo, published("governance"), published("platform"))
            args.out.mkdir(parents=True, exist_ok=True)
            for key, config in configs.items():
                (args.out / f"{key}.json").write_text(json.dumps(config, indent=2) + "\n")
            print(f"mlz vending: wrote {len(configs)} landing zone(s) to {args.out}")
    except (ConfigError, OSError, KeyError, json.JSONDecodeError) as error:
        print(f"mlz {args.role}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
