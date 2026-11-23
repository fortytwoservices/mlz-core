"""mlz-core's loader from the command line.

    python3 -m mlz check    [--repo <a customer repo>] [--out <folder>] [--json]   every check before a plan, and the stacks
    python3 -m mlz governance --customer <customer.yaml> --out <folder> [--defaults <file>]
    python3 -m mlz platform --repo <mlz-platform> --out <config.json>
    python3 -m mlz allocate --repo <mlz-vending> [--check]
    python3 -m mlz names    --repo <mlz-vending> --renew <key> [<key> ...]
    python3 -m mlz release  --repo <mlz-vending> <key>
    python3 -m mlz vending  --repo <mlz-vending> --out <folder>
    python3 -m mlz explain  --repo <mlz-vending> <key> | --repo <mlz-platform> <region> | --repo <mlz-governance> [--inputs]
    python3 -m mlz new landing-zone --repo <mlz-vending> <workload> <env> [--subscription <id>] [--owner <email>] ...
    terraform output -json contract | python3 -m mlz contract governance --out <repo>/_generated/governance.yaml
    python3 -m mlz upgrade  <owner>/<repo> [<owner>/<repo> ...] [--to vX.Y.Z] [--core <owner>/<repo>] [--dry-run]
    python3 -m mlz install  repos|check|bootstrap|handover|publish|runners|subscriptions --settings <dir> [--tenant <id>]
                            [--billing-scope <scope>] [--app-key <file>] [--yes]
    python3 -m mlz schema   --out schemas
    python3 -m mlz map      --repo <a customer repo>                 each settings file's stack, root and modules
    python3 -m mlz reference --out docs/reference                    who owns each module input, a page per root
    python3 -m mlz inputs   --refresh | --check                      the modules' inputs at their pins (tools/mlz/inputs.py)
    python3 -m mlz stacks   --role <role> --stacks <json> --base <checked copy of the base>   in tools/ci/check.py
    python3 -m mlz batches  --stacks <json> [--daily]               the plan jobs for a run, as JSON (role.yml)
    python3 -m mlz diff     --role <role> --stack <stack>            a stack's settings against the base branch's (tools/ci/comment.py)

A role reads the contracts it needs from its repo's `_generated/` (governance.yaml, platform.yaml);
--governance and --platform point elsewhere. The configs a Terraform root reads are JSON, written for
one run and never committed.

Run with tools/ on the Python path (PYTHONPATH=tools). Exits 1 with every problem listed when the
customer's settings are not valid.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path

from . import contract, decommission, explain, firewall, install, new, owners, platform, schema, stacks, upgrade, vending, workload
from . import inputs as input_maps  # main() has a local of the module's name
from . import governance as renderer  # and of this one's
from .github import GitHub
from .files import preload, read_yaml, yaml_files
from .merge import ConfigError

MARKER = ".written-by-mlz"

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mlz", description="Customer YAML in, one checked JSON file per role out.")
    roles = parser.add_subparsers(dest="role", required=True)
    ck = roles.add_parser("check", help="every check the repo's pipeline makes before it plans, and the stacks it would plan")
    ck.add_argument("--repo", type=Path, default=Path("."), help="a governance, platform or vending repo (default: here)")
    ck.add_argument("--out", type=Path, help="where to write the configs the roots read (default: a temporary folder; CI: .mlz)")
    ck.add_argument("--json", action="store_true", help="print only the stacks, as JSON (tools/ci/check.py)")
    ck.add_argument("--github-meta", type=Path, help="platform: a saved copy of api.github.com/meta for the firewall step, read instead of fetching it")
    gv = roles.add_parser("governance", help="customer.yaml, merged with Fortytwo's defaults and checked, into the governance root's library and config")
    gv.add_argument("--customer", type=Path, required=True, help="the customer's customer.yaml")
    gv.add_argument("--defaults", type=Path, default=renderer.DEFAULTS, help="Fortytwo's defaults (default: governance/defaults.yaml in mlz-core)")
    gv.add_argument("--out", type=Path, required=True, help="the folder to write the library and config.json to, replaced on every run")
    gv.add_argument("--bootstrap", type=Path, help="the governance repo's bootstrap.yaml: its github.bypass_team goes into the contract, for the workload repos' rules")
    p = roles.add_parser("platform", help="connectivity/, management/ and identity/ into the config of the platform's stacks")
    p.add_argument("--repo", type=Path, required=True, help="the customer's mlz-platform checkout")
    p.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    p.add_argument("--out", type=Path, required=True, help="where to write the root's config (JSON)")
    a = roles.add_parser("allocate", help="give new landing zones and subnets their blocks in _generated/ip-plan.yaml, and their names in _generated/names.yaml")
    a.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    a.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    a.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml)")
    a.add_argument("--check", action="store_true", help="write nothing; fail if the IP plan or the names are not what allocate would write (for CI)")
    a.add_argument("--base-names", type=Path, help="the names on a pull request's base branch: only those are kept, the rest given again (for CI)")
    nm = roles.add_parser("names", help="give landing zones the naming template's names again (docs/naming.md)")
    nm.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    nm.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    nm.add_argument("--renew", nargs="+", required=True, metavar="key", help="the landing zones to rename: their next plan replaces what is renamed")
    r = roles.add_parser("release", help="free what a landing zone no longer uses, once it is destroyed")
    r.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    r.add_argument("key", help="the landing zone's key, <workload>-<env>")
    v = roles.add_parser("vending", help="landing-zones/ into one config per landing zone")
    v.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    v.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml)")
    v.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml)")
    v.add_argument("--out", type=Path, required=True, help="the folder for <key>.json, one per landing zone; mlz owns it")
    e = roles.add_parser("explain", help="a stack's values, and where each one comes from: a landing zone, a hub, management's or identity's network, the rules, governance")
    e.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    e.add_argument("--governance", type=Path, help="the governance contract (default: <repo>/_generated/governance.yaml, if there)")
    e.add_argument("--platform", type=Path, help="the platform contract (default: <repo>/_generated/platform.yaml, if there)")
    e.add_argument("key", nargs="?", help="vending: a landing zone's key, <workload>-<env>; platform: a region (its connectivity file), "
                   "management, identity/<region> or firewall-rules; governance: nothing")
    e.add_argument("--inputs", action="store_true", help="every input of the stack's module calls: its class, value and source (docs/reference/)")
    n = roles.add_parser("new", help="start the files of something new (see tools/mlz/new.py)")
    kinds = n.add_subparsers(dest="kind", required=True)
    nl = kinds.add_parser("landing-zone", help="a landing zone's files, with only what the layers above don't set")
    nl.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    nl.add_argument("workload", help="the workload: its folder in landing-zones/")
    nl.add_argument("env", help="the environment: its file in the workload's folder")
    for flag, what in (("subscription", "the landing zone's subscription ID"), ("owner", "the owning team's email address"),
                       ("cost-center", "the cost center"), ("archetype", "corp, online or sandbox"), ("location", "the region"),
                       ("short-name", "a new workload's short name in names, 2 to 5 (docs/naming.md)")):
        nl.add_argument(f"--{flag}", help=what)
    f = roles.add_parser("firewall", help="firewall-rules/ into the rules root's config")
    f.add_argument("--repo", type=Path, required=True, help="the customer's mlz-platform checkout")
    f.add_argument("--platform-config", type=Path, required=True, help="the platform root's config, from mlz platform")
    f.add_argument("--out", type=Path, required=True, help="where to write the rules root's config (JSON)")
    f.add_argument("--github-meta", type=Path, help="a saved copy of api.github.com/meta, read instead of fetching it")
    w = roles.add_parser("vending-contract", help="each landing zone's region and range, for the platform's firewall rules")
    w.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout")
    w.add_argument("--out", type=Path, required=True, help="the YAML file to write")
    wr = roles.add_parser("workload-repos", help="after vending's apply, the workload repos' starter (once) and values (docs/vending.md)")
    wr.add_argument("--configs", type=Path, required=True, help="the folder of <key>.json that python3 -m mlz vending wrote")
    d = roles.add_parser("decommission", help="after a landing zone's decommission is applied, offer the pull requests that delete its files")
    d.add_argument("--repo", type=Path, required=True, help="the customer's mlz-vending checkout, at main")
    c = roles.add_parser("contract", help="a root's `contract` output (JSON on stdin) as the commented YAML file")
    c.add_argument("kind", choices=sorted(contract.CONTRACTS))
    c.add_argument("--out", type=Path, required=True, help="the YAML file to write, in a repo's _generated/")
    u = roles.add_parser("upgrade", help="open a pull request moving a customer repo to another mlz-core release")
    u.add_argument("repositories", nargs="+", metavar="owner/repo", help="the customer's GitHub repos to move")
    u.add_argument("--to", metavar="vX.Y.Z", help="the release to move to (default: the latest)")
    u.add_argument("--dry-run", action="store_true", help="show the change and the pull request's text; open nothing")
    u.add_argument("--core", metavar="owner/repo", help="move the repos onto this copy of mlz-core, such as the organization's (default: the one each calls)")
    sc = roles.add_parser("schema", help="write the JSON Schemas of the customer's YAML, for editors (see tools/mlz/schema.py)")
    sc.add_argument("--out", type=Path, required=True, help="the folder to write them to: schemas/ in mlz-core")
    mp = roles.add_parser("map", help="each settings file's stack, that stack's Terraform root and its modules (see tools/mlz/stacks.py)")
    mp.add_argument("--repo", type=Path, default=Path("."), help="a governance, platform or vending repo (default: here)")
    rf = roles.add_parser("reference", help="who owns each input of every module call, a page per root, from the input maps (see tools/mlz/inputs.py)")
    rf.add_argument("--out", type=Path, required=True, help="the folder to write them to: docs/reference in mlz-core")
    ip = roles.add_parser("inputs", help="the snapshot of every pinned module's inputs, tools/mlz/module-variables.json (see tools/mlz/inputs.py)")
    mode = ip.add_mutually_exclusive_group(required=True)
    mode.add_argument("--refresh", action="store_true", help="download each root's modules (terraform get) and write the snapshot")
    mode.add_argument("--check", action="store_true", help="fail when the snapshot is not what --refresh would write (CI)")
    st = roles.add_parser("stacks", help="of the stacks given, those whose rendered config differs from the base's (tools/ci/check.py)")
    st.add_argument("--role", dest="stack_role", required=True, choices=["governance", "platform", "vending"])
    st.add_argument("--stacks", required=True, help="every stack, as JSON, as check.py found them")
    st.add_argument("--base", type=Path, required=True, help="the base commit's copy of the repo, checked by check.py")
    st.add_argument("--repo", type=Path, default=Path("."), help="this commit's repo, checked (default: here)")
    bt = roles.add_parser("batches", help="the plan jobs for a run: one stack each, or groups of stacks (tools/mlz/stacks.py)")
    bt.add_argument("--stacks", required=True, help="the stacks the run plans, as JSON")
    bt.add_argument("--daily", action="store_true", help="the daily check, which plans in groups")
    df = roles.add_parser("diff", help="a stack's settings that differ from the base branch's, for its plan comment (tools/ci/comment.py)")
    df.add_argument("--role", dest="stack_role", required=True, choices=["governance", "platform", "vending"])
    df.add_argument("--stack", required=True, help="the stack, as the pipeline names it")
    df.add_argument("--repo", type=Path, default=Path("."), help="the repo, after check.py (default: here)")
    i = roles.add_parser("install", help="set a new customer up, one step at a time (see tools/mlz/install.py)")
    i.add_argument("step", choices=["repos", "check", "bootstrap", "handover", "publish", "runners", "subscriptions"])
    i.add_argument("--settings", type=Path, required=True, help="the folder with the customer's customer.yaml and bootstrap.yaml")
    i.add_argument("--tenant", help="the customer's Entra tenant ID (bootstrap, handover, publish, runners, subscriptions)")
    i.add_argument("--billing-scope", help="subscriptions: the billing scope vending makes subscriptions in, as billing_scope says")
    i.add_argument("--work", type=Path, help="where the bootstrap keeps its state and plan (default: ~/.mlz/install/<tenant>/<root_id>)")
    i.add_argument("--yes", action="store_true", help="apply the bootstrap's plan, move the subscriptions, or give the billing role, without asking")
    i.add_argument("--app-key", type=Path, help="runners: the runner App's private key (.pem), put into the runners' vault; delete the file afterwards")
    args = parser.parse_args(argv)

    if args.role == "check":
        return check(args.repo, args.out, args.json, args.github_meta)

    if args.role == "governance":
        try:
            config = renderer.render(args.customer, args.defaults, args.out, args.bootstrap)
        except (renderer.RenderError, ConfigError) as error:
            print(f"mlz governance: {error}", file=sys.stderr)
            return 1
        print(f"mlz governance: wrote the library for {config['root_id']} to {args.out}")
        return 0

    if args.role == "schema":
        print(f"mlz schema: wrote {', '.join(schema.write(args.out))} to {args.out}")
        return 0

    if args.role == "reference":
        print(f"mlz reference: wrote {', '.join(input_maps.write(args.out))} to {args.out}")
        return 0

    if args.role == "inputs":
        return input_maps.main(check=args.check)

    if args.role == "map":
        print(stacks.render(args.repo), end="")
        return 0

    if args.role == "diff":
        print(stacks.difference(args.stack_role, args.stack, args.repo), end="")
        return 0

    if args.role == "batches":
        print(json.dumps(stacks.batches(json.loads(args.stacks), args.daily)))
        return 0

    if args.role == "stacks":
        every = json.loads(args.stacks)
        touched = stacks.changed(args.stack_role, every, args.repo, args.base)
        print(f"::notice::Planning {len(touched)} of {len(every)} stacks, those whose config this change touches: {', '.join(touched)}. "
              "The others are as on the base, so they are not planned." if touched else
              f"::notice::Nothing to plan: no stack's config differs from the base's ({len(every)} stacks).", file=sys.stderr)
        print(json.dumps(touched))
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
                else:
                    customer, _ = install.settings(args.settings)
                    # A root ID is unique only within its tenant: two customers may both be "mlz".
                    work = args.work or Path.home() / ".mlz" / "install" / args.tenant / customer["root_id"]
                    terraform = install.Terraform(work, args.tenant)
                    if args.step == "runners":
                        install.step_runners(GitHub(), install.Azure(args.tenant), args.settings, terraform=terraform, app_key=args.app_key, yes=args.yes)
                    elif args.step == "bootstrap":
                        install.step_bootstrap(GitHub(), terraform, args.settings, args.yes)
                    elif args.step == "subscriptions":
                        if not args.billing_scope:
                            raise ConfigError("install subscriptions needs --billing-scope <the billing scope, as billing_scope in the vending repo>.")
                        install.step_subscriptions(install.Azure(args.tenant), terraform, args.settings, args.billing_scope, args.tenant, args.yes)
                    else:
                        install.step_publish(GitHub(), terraform, args.settings)
        except ConfigError as error:
            print(f"mlz install {args.step}: {error}", file=sys.stderr)
            return 1
        return 0

    if args.role == "decommission":
        try:
            repositories = json.loads(os.environ["MLZ_REPOSITORIES"])
            github = GitHub()
            # Only the run of main's current commit offers anything: an older one would offer files main has left.
            sha, head = os.environ.get("GITHUB_SHA"), github.head(repositories["vending"], "main")
            if sha and head != sha:
                print(f"mlz decommission: main is at {head} now, not at this run's {sha}: the newer run does it.")
                return 0
            run = os.environ.get("GITHUB_RUN_ID")
            run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{os.environ.get('GITHUB_REPOSITORY')}/actions/runs/{run}" if run else "run by hand"
            # Signed in as vending's read-only identity, which reads the hubs' firewall policies.
            decommission.cleanup(github, install.Azure(os.environ["AZURE_TENANT_ID"]), args.repo, repositories, run_url, sha=head)
        except KeyError as error:
            print(f"mlz decommission: {error} is not set; MLZ_REPOSITORIES and AZURE_TENANT_ID come from the bootstrap.", file=sys.stderr)
            return 1
        except ConfigError as error:
            print(f"mlz decommission: {error}", file=sys.stderr)
            return 1
        return 0

    if args.role == "workload-repos":
        try:
            github = GitHub()
            # Only the run of main's current commit writes: an older one, a daily run among them, has older values.
            sha, vending_repo = os.environ.get("GITHUB_SHA"), os.environ.get("GITHUB_REPOSITORY")
            if sha and vending_repo and github.head(vending_repo, "main") != sha:
                print(f"mlz workload-repos: main has moved on from this run's {sha}: the newer run does it.")
                return 0
            workload.sync(github, args.configs, app_slug=os.environ.get("MLZ_APP_SLUG") or None)
        except ConfigError as error:
            print(f"mlz workload-repos: {error}", file=sys.stderr)
            return 1
        return 0

    if args.role == "upgrade":
        # Repos on GitHub, not a checkout: nothing to preload. One failing repo does not stop the others.
        github, failed, told = GitHub(), 0, set()
        for repository in args.repositories:
            try:
                upgrade.upgrade(github, repository, args.to, args.dry_run, core=args.core, told=told)
            except ConfigError as error:
                print(f"mlz upgrade: {repository}: {error}", file=sys.stderr)
                failed += 1
        return 1 if failed else 0

    def published(name: str) -> dict:
        path = getattr(args, name, None) or args.repo / "_generated" / f"{name}.yaml"
        if not path.is_file():
            raise ConfigError(f"{path} is not there: {name}'s contract has not arrived yet. {name.capitalize()} offers it by pull request "
                              f"after each run on its main in which nothing failed (docs/pipelines.md); once it is merged here, bring "
                              f"this branch up to date with main.")
        return read_yaml(path)

    # Every file the command reads, parsed in one terraform run before anything is read.
    inputs = {
        "platform": ["connectivity", "management", "identity", "_generated"], "firewall": ["firewall-rules", "_generated"],
        **{role: ["defaults", "landing-zones", "_generated"] for role in ("allocate", "names", "vending", "release", "vending-contract", "new")},
        "explain": ["defaults", "landing-zones", "connectivity", "management", "identity", "_generated"],
    }
    try:
        if args.role != "contract":
            contracts = [getattr(args, name) for name in ("governance", "platform", "base_names") if getattr(args, name, None)]
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
        elif args.role == "explain" and explain.role_of(args.repo) != "vending":
            governance, _ = optional_contracts(args)
            path = args.repo / "_generated" / "vending.yaml"
            if explain.role_of(args.repo) == "platform":
                print(explain.platform_explain(args.repo, args.key, governance, read_yaml(path) if path.is_file() else None, args.inputs), end="")
            else:
                print(explain.governance_explain(args.repo, args.inputs), end="")
        elif args.role == "explain":
            if args.key is None:
                raise ConfigError("explain in a vending repo takes a landing zone's key, <workload>-<env>.")
            zones = vending.landing_zones(args.repo)
            if args.key not in zones:
                raise ConfigError(f"no landing zone {args.key}; the landing zones are {', '.join(sorted(zones)) or 'none yet'}.")
            governance, platform_contract = optional_contracts(args)
            lz, plan = zones[args.key], vending.ip_plan(args.repo)
            if args.inputs:
                if None in (governance, platform_contract):
                    raise ConfigError("explain --inputs needs governance's and the platform's contracts in _generated/: the config it shows is made from them.")
                config = vending.build(args.repo, governance, platform_contract)[args.key]
                print(explain.render_parts(f"{args.key} ({explain.file_of(args.repo, lz['where'])})", explain.module_inputs("vending/terraform", config, {}), None), end="")
                return 0
            parts = explain.explain(args.repo, args.key, lz, plan, governance, platform_contract)
            found = explain.problems(args.key, lz, plan, governance, platform_contract, vending.recorded_names(args.repo))
            print(explain.render(args.repo, args.key, lz, parts, found, None not in (governance, platform_contract)), end="")
        elif args.role == "new":
            given = {k: getattr(args, k) for k in ("owner", "cost_center", "archetype", "location", "subscription", "short_name")}
            for path in new.landing_zone(args.repo, args.workload, args.env, given):
                print(f"mlz new: wrote {path}")
            key = f"{args.workload}-{args.env}"
            governance, platform_contract = optional_contracts(args)
            found = explain.problems(key, vending.landing_zones(args.repo)[key], vending.ip_plan(args.repo), governance, platform_contract,
                                     vending.recorded_names(args.repo))
            subscription = ("vending makes its subscription where billing_scope says" if vending.makes_subscription(vending.landing_zones(args.repo)[key])
                            else "hand the subscription over to vending (docs/bootstrap.md)")
            print(f"mlz new: next, fill in the settings commented out in those files (the check below names them), {subscription}, "
                  f"and open a pull request: its run gives {key} its addresses.")
            print("".join(f"  {problem}\n" for problem in found) or f"  {explain.clean(None not in (governance, platform_contract))}\n", end="")
        elif args.role == "vending-contract":
            data = firewall.vending_contract(vending.landing_zones(args.repo), vending.ip_plan(args.repo))
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(contract.render("vending", data))
            print(f"mlz vending-contract: wrote {args.out}")
        elif args.role == "platform":
            if platform.unset(args.repo):
                # An older config at --out goes too, so that nothing plans with it.
                args.out.unlink(missing_ok=True)
                print(f"mlz platform: no file in {args.repo / platform.CONNECTIVITY} has a setting yet; nothing written until one does")
                return 0
            path = args.repo / "_generated" / "vending.yaml"
            config = platform.build(args.repo, published("governance"), read_yaml(path) if path.exists() else None)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(config, indent=2) + "\n")
            print(f"mlz platform: wrote {args.out}")
        elif args.role == "allocate":
            plan, changes, retired = vending.allocate(args.repo, published("platform"))
            base = vending.recorded_names(args.repo, args.base_names) if args.base_names else None
            record, named = vending.give_names(args.repo, plan, published("governance"), base=base)
            changes += named
            texts = {vending.IP_PLAN: vending.render_plan(plan, retired), vending.NAMES: vending.render_names(record)}
            if args.check:
                stale = [name for name, text in texts.items() if not (args.repo / name).exists() or (args.repo / name).read_text() != text]
                if stale:
                    raise ConfigError(
                        f"{' and '.join(stale)} {'is' if len(stale) == 1 else 'are'} not up to date: a pull request's check brings "
                        f"{'it' if len(stale) == 1 else 'them'} up to date itself; on main, or to do it here, run ./mlz allocate --repo . "
                        f"and commit {'it' if len(stale) == 1 else 'them'}."
                        + "".join(f"\n  {c}" for c in changes)
                    )
                print(f"mlz allocate: {vending.IP_PLAN} and {vending.NAMES} are up to date")
                return 0
            for name, text in texts.items():
                write_file(args.repo / name, text)
            print("mlz allocate: " + (f"{len(changes)} change(s)" if changes else "nothing new") + "".join(f"\n  {c}" for c in changes))
            for key in retired:
                print(f"mlz allocate: {key} was removed but holds its range; once it is destroyed, run: ./mlz release --repo . {key}")
        elif args.role == "names":
            record, changes = vending.give_names(args.repo, vending.ip_plan(args.repo), published("governance"), set(args.renew))
            write_file(args.repo / vending.NAMES, vending.render_names(record))
            print("mlz names: " + (f"{len(changes)} renamed" if changes else "the names were the template's already") + "".join(f"\n  {c}" for c in changes))
            if changes:
                print(f"mlz names: commit {vending.NAMES} in a pull request; its plan replaces each renamed resource, for its reviewer to approve.")
        elif args.role == "release":
            plan, retired, freed = vending.release(args.repo, args.key)
            write_file(args.repo / vending.IP_PLAN, vending.render_plan(plan, retired))
            if (args.repo / vending.NAMES).exists():  # what is released takes its names with it
                write_file(args.repo / vending.NAMES, vending.render_names(vending.held_names(vending.recorded_names(args.repo), plan)))
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


def check(repo: Path, out: Path | None, as_json: bool, github_meta: Path | None = None) -> int:
    """Every check the repo's pipeline makes before it plans (tools/ci/check.py calls this), with the configs the roots
    read written to out, and the stacks a run would plan: all of them, as on the daily check (check.py then keeps those
    a change touches). The role is the one the repo's workflow names."""
    with tempfile.TemporaryDirectory() as scratch:
        found = every_stack(repo, out or Path(scratch), github_meta)
    if found is None:
        return 1
    if as_json:
        print(json.dumps(found))
    else:
        print(f"mlz check: {repo.resolve().name} checks; its stacks: {', '.join(found) if found else 'none yet'}")
    return 0


def every_stack(repo: Path, out: Path, github_meta: Path | None = None) -> list[str] | None:
    """The stacks the repo's settings make, in the order they apply, with their configs written to out; None when a
    check fails (the command that failed has said why). Each step is the command a person can run on its own."""
    def run(*argv: object) -> bool:
        with contextlib.redirect_stdout(sys.stderr):  # stdout is for the stacks
            return main([str(a) for a in argv]) == 0

    role = stacks.role(repo)
    # The code owners first, offline: exactly the installer's two rules (tools/mlz/owners.py, decision D8). The
    # governance repo has bootstrap.yaml, which names the platform team.
    found, bootstrap = [], {}
    if (repo / "bootstrap.yaml").is_file():
        try:
            settings = read_yaml(repo / "bootstrap.yaml")
            bootstrap = settings.get("github") if isinstance(settings, dict) else None
        except ConfigError as error:
            found.append(f"bootstrap.yaml: {error}")
        if not isinstance(bootstrap, dict):
            found += [] if found else ["bootstrap.yaml: github is not a mapping of the organization's settings (docs/bootstrap.md)."]
            bootstrap = {}
    # Either form of _generated/ (Enterprise's or Team's): the plan's form is mlz install check's to enforce, since a
    # switch changes bootstrap.yaml and this file in pull requests of their own.
    found += owners.offline_problems(repo, bootstrap.get("organization"), bootstrap.get("platform_team"))
    for problem in found:
        print(f"::error::{problem}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"mlz check: {problem}", file=sys.stderr)
    if found:
        return None
    if role == "governance":
        given = ["--bootstrap", repo / "bootstrap.yaml"] if (repo / "bootstrap.yaml").is_file() else []
        return ["governance"] if run("governance", "--customer", repo / "customer.yaml", *given, "--out", out / "rendered") else None
    if role == "platform":
        if not run("platform", "--repo", repo, "--out", out / "platform.json"):
            return None
        # As the starter comes, no connectivity file has a setting: the platform waits and plans nothing. On a first
        # deployment governance's contract then merges itself, and the pull request that writes the hub is checked
        # against it.
        if not (out / "platform.json").is_file():
            notice(f"No connectivity file has a setting yet ({platform.CONNECTIVITY}/): the platform waits for one; nothing is planned or changed.")
            return []
        # The rules stack runs with or without firewall-rules/: every hub's platform group holds the rules mlz-core
        # requires, and deleting the last rules file must take its rules out of Azure, not leave them behind.
        meta = ["--github-meta", github_meta] if github_meta else []
        if not run("firewall", "--repo", repo, "--platform-config", out / "platform.json", "--out", out / "firewall.json", *meta):
            return None
        # Management's and identity's networks run every time, built or not, so turning one off takes it out of Azure.
        # They apply after the hub, which they peer with, and before the rules, which need the hub's policy: the
        # order of stacks.STACKS, which map shows too.
        every = [stack.name for stack in stacks.STACKS if stack.role == "platform"]
        if (repo / "_generated" / "vending.yaml").is_file():
            return every
        # Until vending publishes its first contract, the landing zones' rules have no sources, so the rules stack
        # stays out of the run: on a first deployment the hub goes first, and a contract gone missing from a platform
        # already deployed never plans their rules away. The files are still checked above.
        notice("No vending contract yet (_generated/vending.yaml): the firewall rules stack waits for it; nothing in it is planned or changed.")
        return [name for name in every if name != "firewall-rules"]
    # Vending works everything out from governance's and platform's contracts. Until both are here, it waits and plans
    # nothing: on a first deployment it goes last, and its contract pull requests merge themselves meanwhile; a
    # contract gone missing from a vending already deployed never plans its landing zones away.
    for contract_ in ("governance", "platform"):
        if not (repo / "_generated" / f"{contract_}.yaml").is_file():
            notice(f"No {contract_} contract yet (_generated/{contract_}.yaml): vending waits for it; nothing is planned or changed.")
            return []
    if not run("allocate", "--repo", repo, "--check") or not run("vending", "--repo", repo, "--out", out / "vending"):
        return None
    return sorted(p.stem for p in (out / "vending").glob("*.json"))


def notice(text: str) -> None:
    """A note on the run's page in GitHub Actions; a line on the terminal anywhere else."""
    print(f"::notice::{text}" if os.environ.get("GITHUB_ACTIONS") == "true" else f"mlz check: {text}", file=sys.stderr)


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


def write_file(path: Path, text: str) -> None:
    if not path.exists() or path.read_text() != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


if __name__ == "__main__":
    sys.exit(main())
