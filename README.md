# mlz-core

The versioned code behind Fortytwo's managed landing zones. Customer repos, made from the `mlz-governance`,
`mlz-platform`, `mlz-vending` and `mlz-workload` templates, hold only YAML and pin a release of this repo.

Each customer gets three pipelines, one per role: governance (the management group tree, policies and
central management), platform (the hubs) and vending (the landing zones). A one-time bootstrap sets up
what they run on. One page per part in `docs/`: start with `docs/bootstrap.md`.

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
tools/mlz/             the loader: customer YAML in, checked config out; the IP allocator; the contracts
tools/ci/              the pipeline steps, run by .github/workflows/role.yml in each customer repo
modules/
  naming/              the names of everything mlz-core creates, shared by every root
starter/
  mlz-<role>/          what each template repo starts with: its pipeline, and the demo settings
docs/                  one page per part
```

## Tests

Needs Terraform (version in `.terraform-version`) and Python 3.10 or later; the loader reads YAML with
Terraform, so it needs no Python packages.

```sh
python3 -m unittest discover -s governance/scripts
python3 -m unittest discover -s tools -t tools
```
