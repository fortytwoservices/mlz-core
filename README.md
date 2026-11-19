# mlz-core

The versioned code behind Fortytwo's managed landing zones. Customer repos, made from the `mlz-governance`,
`mlz-platform`, `mlz-vending` and `mlz-workload` templates, hold only YAML and pin a release of this repo.

Each customer gets three pipelines, one per role: governance (the management group tree, policies and
central management), platform (the hubs) and vending (the landing zones). A one-time bootstrap sets up
what they run on. One page per part in `docs/`: start with `docs/bootstrap.md`, whose `mlz install` sets a
new customer up.

## Layout

```
bootstrap/terraform/   one-time setup per customer: top group, state, pipeline identities, GitHub settings
governance/
  defaults.yaml        Fortytwo's governance defaults; customer.yaml overrides them
  lib/                 Fortytwo's additions to the Azure Landing Zones library
  terraform/           the governance root
platform/terraform/    the platform's connectivity root: hubs, firewalls, DNS; networks/ (management, identity)
                       and rules/ (the firewall rules) beside it
vending/terraform/     the vending root: one landing zone per state
tools/mlz/             the loader: customer YAML in, checked config out (mlz governance renders customer.yaml
                       into the customer's library); mlz check, every check a repo's pipeline makes before it
                       plans; the IP allocator; the contracts;
                       mlz upgrade, which opens the pull request moving a customer to another release;
                       mlz install, which sets a new customer up step by step; mlz schema (below);
                       mlz explain, which shows where each of a stack's values comes from (a landing zone, a hub,
                       management's or identity's network, the rules, governance), and with --inputs every
                       input of its module calls;
                       mlz new landing-zone, which starts a landing zone's files;
                       mlz map, which says what each settings file feeds: its stack, root and modules;
                       mlz reference (below)
tools/ci/              the pipeline steps, run by .github/workflows/role.yml in each customer repo
modules/
  naming/              the names of everything mlz-core creates, shared by every root
starter/
  mlz-<role>/          what each template repo starts with: its pipeline, ./mlz (below), its editor settings,
                       and example settings, commented out or in examples/, which the loader does not read
schemas/               JSON Schemas of the customer YAML, for editors (below)
docs/                  one page per part; reference/, who owns each module input (below); advanced.md, the module
                       inputs a customer may set where mlz-core sets none
```

## Who owns each module input

`docs/reference/` has a page per Terraform root: every module call the root makes, every input of each module
at its pin, and who owns it. An input is a `setting` (a key in the customer's YAML, named), `derived` (worked out
by mlz-core: the layout, the IP allocator, a contract, the naming), `fixed` (Fortytwo's decision, with its value
and reason), `open` (mlz-core leaves it at the module's default) or `closed` (not supported yet, with the reason).
Start at [docs/reference/README.md](docs/reference/README.md).

The pages are written from the input map beside each root (`<root>/inputs.yaml`) and a snapshot of what each
pinned module declares (`tools/mlz/module-variables.json`). A test fails when an input of a module has no entry
or two, when a setting names a key no loader takes, and when the committed pages are out of date; CI fails when
the snapshot is not what the pinned modules declare. After moving a pin or editing a map:

```sh
PYTHONPATH=tools python3 -m mlz inputs --refresh            # the snapshot, from the modules at their new pins
PYTHONPATH=tools python3 -m mlz reference --out docs/reference
```

## Editor help in customer repos

Each customer repo's `.vscode/settings.json` points its YAML files at the schemas in `schemas/` of the
release the repo runs, and `.vscode/extensions.json` recommends Red Hat's YAML extension, which reads them.
The editor then completes keys, explains them on hover, and marks a mistyped key or value as it is typed.
Both files belong to mlz-core: they come with the starter, and `mlz upgrade` replaces them with the
workflow. JetBrains IDEs don't read them; map the schemas once in Languages & Frameworks › Schemas and
DTDs › JSON Schema Mappings.

The schemas are generated from the loaders' own key lists, and a test fails when the committed ones are
out of date:

```sh
PYTHONPATH=tools python3 -m mlz schema --out schemas
```

A schema checks one file on its own. What needs several files, such as a setting required in some layer
or two landing zones on one subscription, stays with the pipeline's check, which `./mlz check` runs too.

## ./mlz in customer repos

Each customer repo has `./mlz`: mlz-core's command line at the release the repo's workflow calls. It reads the copy
of mlz-core and the release from `.github/workflows/mlz.yml`, fetches that release once into `~/.cache/mlz-core`
with the person's own `gh` sign-in (the organization's copy is private), and runs `python3 -m mlz` in the repo:

```sh
./mlz check                         # every check the pipeline makes before it plans, and the stacks it would plan
./mlz map                           # each settings file's stack, that stack's Terraform root and its modules
./mlz explain --repo . <key>        # where each of a stack's values comes from (vending; a region in the platform)
./mlz new landing-zone --repo . <workload> <env>
```

It needs `python3`, `terraform` (the loader reads YAML with it) and `gh`, signed in to an account that can read the
copy; without `gh` or a sign-in it says what to do. Like the workflow and the editor settings, it belongs to
mlz-core: it comes with the starter, and `mlz upgrade` replaces it. The API commits `mlz install` and `mlz upgrade`
make cannot set a file's mode, so each adds one more commit that makes `./mlz` executable
(`GitHub.make_executable`): on an upgrade's pull request the squash merge folds it into one verified commit; a repo
`mlz install repos` makes keeps it as the one unverified commit on its `main`.

## Tests

Needs Terraform (version in `.terraform-version`) and Python 3.10 or later; the loader reads YAML with
Terraform, so it needs no Python packages.

```sh
python3 -m unittest discover -s tools -t tools
```
