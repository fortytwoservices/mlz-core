output "contract" {
  description = <<-EOT
    What governance needs from the bootstrap: the pipeline identities, to grant vending its rights on the
    groups governance creates. `python3 -m mlz contract bootstrap` writes it to `_generated/bootstrap.yaml`
    in the governance repo. No secrets.
  EOT
  value = {
    top_management_group = local.root
    state = {
      subscription    = trimprefix(local.management, "/subscriptions/")
      resource_group  = local.names.resource_group
      storage_account = local.names.storage_account
    }
    identities = {
      for role in local.roles : role => {
        for kind in ["plan", "apply"] : kind => {
          name         = local.names.identity["${role}-${kind}"]
          client_id    = azapi_resource.identity["${role}-${kind}"].output.properties.clientId
          principal_id = azapi_resource.identity["${role}-${kind}"].output.properties.principalId
        }
      }
    }
  }
}

output "handover" {
  description = "The commands that hand subscriptions to the pipelines, by moving them into the tree. Run as someone who may move them (Owner on the subscription, and rights on the group it is in now)."
  value       = <<-EOT
    # Once, after the bootstrap: the platform subscriptions into the top group. Governance moves them on.
    %{for name, scope in local.subscriptions~}
    az account management-group subscription add --name ${local.root} --subscription ${trimprefix(scope, "/subscriptions/")}   # ${name}
    %{endfor~}

    # For each new landing zone subscription, before its YAML is merged. Vending moves it on to Corp,
    # Online or Sandboxes, as its YAML says.
    az account management-group subscription add --name ${local.root}-landing-zones --subscription <new subscription ID>
  EOT
}

output "oidc_subjects" {
  description = "The GitHub subjects each identity trusts. They must match what GitHub issues: gh api repos/<organization>/<repository>/actions/oidc/customization/sub prints the prefix."
  value = merge(
    { for key, credential in azapi_resource.credential : key => credential.body.properties.subject },
    { for key, credential in azapi_resource.credential_main : "${key} (main)" => credential.body.properties.subject },
  )
}

output "backend_config" {
  description = "The -backend-config for this root's own state, once the account exists: terraform output -raw backend_config > backend.hcl."
  value       = <<-EOT
    storage_account_name = "${local.names.storage_account}"
    container_name       = "bootstrap"
    key                  = "bootstrap.tfstate"
  EOT
}

output "state_private_endpoint" {
  description = "Whether the state account has its private endpoint in management's network. It comes with the bootstrap's first run after the platform has built that network and the hub's blob zone."
  value       = local.state_endpoint
}
