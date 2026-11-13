# The firewall rules, apart from the hub: rules change often and the hub rarely, so a rule change never
# plans the hub. Each region's hub policy holds a fixed set of rule collection groups (tools/mlz/firewall.py):
# `platform` for the shared rules and `lz-<nn>` partitions for the landing zones' own.
locals {
  config = jsondecode(file(var.config_file))

  groups = merge([
    for region, groups in local.config.policies : {
      for name, group in groups : "${region}/${name}" => merge(group, { region = region, name = name })
    }
  ]...)

  # Each hub's policy, by the names the platform root gives it (mlz firewall, from tools/mlz/names.py).
  policy_id = local.config.policy_ids
}

module "rule_collection_group" {
  for_each = local.groups
  source   = "git::https://github.com/fortytwoservices/az-tf-mlz-azure-firewall.git//modules/rule_collection_group?ref=v0.1.4"

  name                         = each.value.name
  parent_id                    = local.policy_id[each.value.region]
  priority                     = each.value.priority
  application_rule_collections = each.value.application
  network_rule_collections     = each.value.network
}
