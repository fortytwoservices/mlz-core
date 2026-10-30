# mlz-core

The versioned code behind Fortytwo's managed landing zones. Customer repos, made from the `mlz-governance`,
`mlz-platform`, `mlz-vending` and `mlz-workload` templates, hold only YAML and pin a release of this repo.

Status: local only, not yet on GitHub. Governance is the first part (`docs/governance.md`).

## Layout

```
governance/
  defaults.yaml        Fortytwo's governance defaults; customer.yaml overrides them
  lib/                 Fortytwo's additions to the Azure Landing Zones library
  scripts/             render_library.py (customer.yaml -> the customer's library) and its tests
  terraform/           the governance root
starter/
  mlz-governance/      the demo customer.yaml for the template repo
docs/                  one page per role
```

## Try governance locally

Needs Terraform (version in `.terraform-version`), Python 3.10 or later, and the Azure CLI signed in.

```sh
python3 -m unittest discover -s governance/scripts
python3 governance/scripts/render_library.py --customer starter/mlz-governance/customer.yaml --out /tmp/rendered
printf 'terraform {\n  backend "local" {}\n}\n' > governance/terraform/backend_override.tf   # gitignored
terraform -chdir=governance/terraform init
terraform -chdir=governance/terraform plan -var rendered_library_path=/tmp/rendered
```

Do not apply yet: see "Before the first apply" in `docs/governance.md`.
