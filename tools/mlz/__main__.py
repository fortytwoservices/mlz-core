"""mlz-core's loader from the command line.

    python3 -m mlz platform --repo <mlz-platform> --out <config.json>
    python3 -m mlz allocate --repo <mlz-vending> [--check]
    python3 -m mlz release  --repo <mlz-vending> <key>
    python3 -m mlz vending  --repo <mlz-vending> --out <folder>
    python3 -m mlz explain  --repo <mlz-vending> <key>
    python3 -m mlz new landing-zone --repo <mlz-vending> <workload> <env> [--subscription <id>] [--owner <email>] ...
    terraform output -json contract | python3 -m mlz contract governance --out <repo>/_generated/governance.yaml
    python3 -m mlz upgrade  <owner>/<repo> [<owner>/<repo> ...] [--to vX.Y.Z] [--dry-run]
    python3 -m mlz install  repos|check|bootstrap|handover|publish --settings <dir> [--tenant <id>] [--yes]
    python3 -m mlz schema   --out schemas

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

from . import contract, explain, firewall, install, new, platform, schema, upgrade, vending
from .github import GitHub
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
    e = roles.add_parser("explain", help="a landing zone's values, and where each one comes from")
    e.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    e.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml, if there)")
    e.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml, if there)")
    e.add_argument("key", help="the landing zone's key, <workload>-<env>")
    n = roles.add_parser("new", help="start the files of something new (see tools/mlz/new.py)")
    kinds = n.add_subparsers(dest="kind", required=True)
    nl = kinds.add_parser("landing-zone", help="a landing zone's files, with only what the layers above don't set")
    nl.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    nl.add_argument("workload", help="the workload: its folder in landing-zones/")
    nl.add_argument("env", help="the environment: its file in the workload's folder")
    for flag, what in (("subscription", "the landing zone's subscription ID"), ("owner", "the owning team's email address"),
                       ("cost-center", "the cost center"), ("archetype", "corp, online or sandbox"), ("location", "the region")):
        nl.add_argument(f"--{flag}", help=what)
    f = roles.add_parser("firewall", help="firewall/ into the rules root's config")
    f.add_argument("--repo", type=Path, required=True, help="the customer's mlz-platform checkout")
    f.add_argument("--platform-config", type=Path, required=True, help="the platform root's config, from mlz platform")
    f.add_argument("--out", type=Path, required=True, help="where to write the rules root's config (JSON)")
    f.add_argument("--github-meta", type=Path, help="a saved copy of api.github.com/meta, read instead of fetching it")
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
    sc = roles.add_parser("schema", help="write the JSON Schemas of the customer's YAML, for editors (see tools/mlz/schema.py)")
    sc.add_argument("--out", type=Path, required=True, help="the folder to write them to: schemas/ in mlz-core")
    i = roles.add_parser("install", help="set a new customer up, one step at a time (see tools/mlz/install.py)")
    i.add_argument("step", choices=["repos", "check", "bootstrap", "handover", "publish", "runners"])
    i.add_argument("--settings", type=Path, required=True, help="the folder with the customer's customer.yaml and bootstrap.yaml")
    i.add_argument("--tenant", help="the customer's Entra tenant ID (bootstrap, handover, publish, runners)")
    i.add_argument("--work", type=Path, help="where the bootstrap keeps its state and plan (default: ~/.mlz/install/<tenant>/<root_id>)")
    i.add_argument("--yes", action="store_true", help="apply the bootstrap's plan, or move the subscriptions, without asking")
    args = parser.parse_args()

    if args.role == "schema":
        print(f"mlz schema: wrote {', '.join(schema.write(args.out))} to {args.out}")
        return 0

    if args.role == "install":
        try:
            if args.step == "repos":
                install.step_repos(GitHub(), args.settings)
            elif args.step == "check":
                install.step_check(GitHub(), args.settings)
            else:
                if not args.tenant:
                    raise ConfigError(f"install {args.step} needs --tenant <the customer's Entra tenant ID>.")
                if args.step == "handover":
                    install.step_handover(install.Azure(args.tenant), args.settings, args.yes)
                elif args.step == "runners":
                    install.step_runners(GitHub(), install.Azure(args.tenant), args.settings)
                else:
                    customer, _ = install.settings(args.settings)
                    # A root ID is unique only within its tenant: two customers may both be "mlz".
                    work = args.work or Path.home() / ".mlz" / "install" / args.tenant / customer["root_id"]
                    terraform = install.Terraform(work, args.tenant)
                    if args.step == "bootstrap":
                        install.step_bootstrap(GitHub(), terraform, args.settings, args.yes)
                    else:
                        install.step_publish(GitHub(), terraform, args.settings)
        except ConfigError as error:
            print(f"mlz install {args.step}: {error}", file=sys.stderr)
            return 1
        return 0

    if args.role == "upgrade":
        # Repos on GitHub, not a checkout: nothing to preload. One failing repo does not stop the others.
        github, failed = GitHub(), 0
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
        "platform": ["hubs", "_generated"], "firewall": ["firewall", "_generated"], "allocate": ["landing-zones", "_generated"],
        "vending": ["landing-zones", "_generated"], "release": ["landing-zones", "_generated"], "vending-contract": ["landing-zones", "_generated"],
        "explain": ["landing-zones", "_generated"], "new": ["landing-zones", "_generated"],
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
            if not path.exists():
                # Without vending's contract the files are checked, and nothing is written: a config without the
                # landing zones would plan their rules away. An older one at --out goes first, before the checks,
                # so that not even a failed check leaves one behind to plan with.
                args.out.unlink(missing_ok=True)
            github = firewall.github_actions_domains(args.github_meta)
            config = firewall.build(args.repo, json.loads(args.platform_config.read_text()), read_yaml(path) if path.exists() else None, github)
            if config is None:
                print(f"mlz firewall: checked; nothing written until vending's contract ({path}) exists")
            else:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(config, indent=2) + "\n")
                print(f"mlz firewall: wrote {args.out}")
        elif args.role == "explain":
            zones = vending.landing_zones(args.repo)
            if args.key not in zones:
                raise ConfigError(f"no landing zone {args.key}; the landing zones are {', '.join(sorted(zones)) or 'none yet'}.")
            governance, platform_contract = optional_contracts(args)
            lz, plan = zones[args.key], vending.ip_plan(args.repo)
            parts = explain.explain(args.repo, args.key, lz, plan, governance, platform_contract)
            found = explain.problems(args.key, lz, plan, governance, platform_contract)
            print(explain.render(args.repo, args.key, lz, parts, found, None not in (governance, platform_contract)), end="")
        elif args.role == "new":
            given = {k: getattr(args, k) for k in ("owner", "cost_center", "archetype", "location", "subscription")}
            for path in new.landing_zone(args.repo, args.workload, args.env, given):
                print(f"mlz new: wrote {path}")
            key = f"{args.workload}-{args.env}"
            governance, platform_contract = optional_contracts(args)
            found = explain.problems(key, vending.landing_zones(args.repo)[key], vending.ip_plan(args.repo), governance, platform_contract)
            print(f"mlz new: next, fill in the settings commented out in those files (the check below names them), hand "
                  f"the subscription over to vending (docs/bootstrap.md), and open a pull request: its run gives {key} its addresses.")
            print("".join(f"  {problem}\n" for problem in found) or f"  {explain.clean(None not in (governance, platform_contract))}\n", end="")
        elif args.role == "vending-contract":
            data = firewall.vending_contract(vending.landing_zones(args.repo), vending.ip_plan(args.repo))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(contract.render("vending", data))
            print(f"mlz vending-contract: wrote {args.out}")
        elif args.role == "platform":
            path = args.repo / "_generated" / "vending.yaml"
            config = platform.build(args.repo, published("governance"), read_yaml(path) if path.exists() else None)
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


def optional_contracts(args) -> tuple[dict | None, dict | None]:
    """The governance and platform contracts, each None until it is published: what they bring then says it waits."""
    paths = (getattr(args, name, None) or args.repo / "_generated" / f"{name}.yaml" for name in ("governance", "platform"))
    return tuple(read_yaml(path) if path.is_file() else None for path in paths)


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
