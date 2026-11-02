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
  description = "The commands that hand a new landing zone subscription to vending, for the subscription runbook."
  value       = <<-EOT
    # Hand a new landing zone subscription to vending. Run as someone who is Owner on it, once per subscription.
    SUBSCRIPTION=<new subscription ID>
    az role assignment create --assignee-object-id ${azapi_resource.identity["vending-apply"].output.properties.principalId} --assignee-principal-type ServicePrincipal --role Owner --scope /subscriptions/$SUBSCRIPTION
    az role assignment create --assignee-object-id ${azapi_resource.identity["vending-plan"].output.properties.principalId} --assignee-principal-type ServicePrincipal --role Reader --scope /subscriptions/$SUBSCRIPTION
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
