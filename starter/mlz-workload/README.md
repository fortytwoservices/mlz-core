# Workload repo

This landing zone's own Terraform. Its pipeline (`.github/workflows/mlz.yml`) plans every pull request and every
push to `main`; on `main`, it applies once the owning team approves the `apply` environment, and only the changes
that plan showed.

- **Write** the landing zone's resources at the top of the repo: `main.tf` has an example.
- **`mlz.auto.tfvars.json`** is the landing zone's key, region and naming values. mlz-core's vending writes it and
  keeps it up to date: do not edit it. The subscription is the one the pipeline signs in to
  (`data "azapi_client_config"`).
- **Names** come from the customer's template, through mlz-core's naming helper (`main.tf`'s example): a name Azure
  would not take stops the plan.
- **State** is in the landing zone's own storage account (the repo's `TFSTATE_*` variables). Pull requests plan from
  a read-only copy of it.
- **Signing in** needs no secret: the plan identity for pull requests, the deploy identity in `apply`, both by
  GitHub's OIDC.
