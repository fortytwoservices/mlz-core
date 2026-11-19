# bootstrap/terraform: who owns each input

<!-- Written by python3 -m mlz reference from bootstrap/terraform/inputs.yaml and tools/mlz/module-variables.json; do not edit. -->

The Terraform root of the stack `bootstrap`: every input of every module call it makes, and who owns it. The map is `bootstrap/terraform/inputs.yaml`; `tools/mlz/inputs.py` says how it is checked.

Each input is in one class:

| Class | Meaning | Can a customer set it? |
|---|---|---|
| setting | from a key in the customer's YAML | yes, with that key |
| derived | worked out by mlz-core: from the layout, the IP allocator, a contract or the naming | no; a name, under `names:` where the entry says so |
| fixed | Fortytwo's decision for every customer | no |
| open | mlz-core does not set it, so the module's default applies | under `advanced:` where the entry says so (docs/advanced.md); not yet elsewhere |
| closed | not supported yet | no |

An entry covers its input and everything in it: `bastion` closed means every attribute of `bastion`. A map or list of
objects passes through to the objects' attributes (`hub_virtual_networks.firewall.sku_tier` is each hub's).

## module.runners: az-tf-mlz-github-runners v0.1.0

The self-hosted runners, Fortytwo's runner image on a scale set in management's network, once their vault holds the runner App's key.

Settings from `bootstrap.yaml`.

24 entries: 6 setting, 6 derived, 5 fixed, 7 open.

| Input | Type | Class | Source, value or reason |
|---|---|---|---|
| `app_key_secret_resource_id` | string | derived | the vault's github-runner-app-key, which mlz install runners writes |
| `github.scope` | string | setting | `github.organization` in `bootstrap.yaml` |
| `github.app_client_id` | string | setting | `github.runners.app_client_id` in `bootstrap.yaml` |
| `github.labels` | list(string) | fixed | `["mlz"]`: the label the pipelines' MLZ_RUNS_ON names |
| `github.runner_group` | string | fixed | `"mlz"`: the runner group mlz install runners makes, open to the three repos only |
| `instances` | number | setting | `github.runners.instances` in `bootstrap.yaml` |
| `location` | string | setting | `primary_region` in `customer.yaml` |
| `name` | string | derived | the naming template (customer.yaml's naming) |
| `network_security_group_name` | string | derived | the naming template |
| `parent_id` | string | derived | the bootstrap's resource group |
| `sku_name` | string | setting | `github.runners.size` in `bootstrap.yaml` |
| `subnet_resource_id` | string | derived | management's runners subnet, from names |
| `user_assigned_identity_name` | string | derived | the naming template |
| `admin_username` | string | open | the module's default: `"runneradmin"` |
| `computer_name_prefix` | string | open | the module's default: `null` |
| `encryption_at_host_enabled` | bool | open | the module's default: `true` |
| `image` | object | setting | `github.runners.image` in `bootstrap.yaml`: fortytwo or dev, as the Marketplace image and plan (tools/mlz/install.py, RUNNER_IMAGES) |
| `os_disk` | object | open | the module's default: `{}` |
| `tags` | map(string) | fixed | `{"ManagedBy": "mlz-core"}`: what mlz-core built, at a glance |
| `zones` | list(string) | open | the module's default: `[]` |
| `ignore_body_changes` | object | fixed | the module's default (`{}`): every difference Azure shows is planned, and the daily check reports drift |
| `resource_types` | object | fixed | the module's default (`{}`): the API versions the module is tested with; new ones come with a module release |
| `retry` | object | open | the module's default: `null` |
| `timeouts` | object | open | the module's default: `null` |
