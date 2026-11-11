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
  scripts/             render_library.py (customer.yaml -> the customer's library) and its tests
  terraform/           the governance root
platform/terraform/    the platform root: hubs, firewalls, DNS
vending/terraform/     the vending root: one landing zone per state
tools/mlz/             the loader: customer YAML in, checked config out; the IP allocator; the contracts;
                       mlz upgrade, which opens the pull request moving a customer to another release;
                       mlz install, which sets a new customer up step by step; mlz schema (below)
tools/ci/              the pipeline steps, run by .github/workflows/role.yml in each customer repo
modules/
  naming/              the names of everything mlz-core creates, shared by every root
starter/
  mlz-<role>/          what each template repo starts with: its pipeline, its editor settings, and
                       example settings, commented out or in a folder the loader skips
schemas/               JSON Schemas of the customer YAML, for editors (below)
docs/                  one page per part
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
or two landing zones on one subscription, stays with the pipeline's check.

## Tests

Needs Terraform (version in `.terraform-version`) and Python 3.10 or later; the loader reads YAML with
Terraform, so it needs no Python packages.

```sh
python3 -m unittest discover -s governance/scripts
python3 -m unittest discover -s tools -t tools
```
