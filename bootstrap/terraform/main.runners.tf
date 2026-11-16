# Self-hosted runners for the three pipelines, in management's network, when bootstrap.yaml's github.runners asks
# for them (the default kind). The bootstrap deploys them, never a pipeline that runs on them, so a broken change
# cannot cut off its own run (docs/bootstrap.md, private runners).
#
# They come in two runs. Once the platform has built management's network and the hub's zones: a Key Vault for
# the runner App's private key, reachable only through its private endpoint there. Then, once mlz install runners
# has put the key in it: the runners (az-tf-mlz-github-runners), Fortytwo's runner image on a scale set, which
# register into the organization's runner group mlz and read the key with an identity of their own.
locals {
  runners     = jsondecode(file(var.runners_file)) # bootstrap.yaml's github.runners, checked by mlz install, with defaults
  self_hosted = local.runners.kind == "self-hosted"

  # Every ID from names, so that whether a part exists is known while planning, on the first run too.
  bootstrap_group = "${local.management}/resourceGroups/${local.names.resource_group}"
  runners_subnet  = "${local.management_network_id}/subnets/${local.names.runners_subnet}"
  vault_zone      = "${local.connectivity}/resourceGroups/${local.names.dns_resource_group}/providers/Microsoft.Network/privateDnsZones/privatelink.vaultcore.azure.net"
  # The vault's and the runners' names are null with GitHub-hosted runners (tools/mlz/names.py), and nothing reads them.
  vault_id       = local.self_hosted ? "${local.bootstrap_group}/providers/Microsoft.KeyVault/vaults/${local.names.key_vault}" : null
  app_key_secret = local.self_hosted ? "${local.vault_id}/secrets/github-runner-app-key" : null # mlz install runners writes it (install.APP_KEY_SECRET)

  vault       = local.self_hosted && local.state_endpoint && data.azapi_resource.runners_subnet.exists && data.azapi_resource.vault_zone.exists
  runner_pool = local.vault && local.runners.app_client_id != null && one(data.azapi_resource.app_key[*].exists) == true
}

data "azapi_resource" "runners_subnet" {
  type                   = "Microsoft.Network/virtualNetworks/subnets@2024-07-01"
  resource_id            = local.runners_subnet
  ignore_not_found       = true
  response_export_values = []
}

data "azapi_resource" "vault_zone" {
  type                   = "Microsoft.Network/privateDnsZones@2024-06-01"
  resource_id            = local.vault_zone
  ignore_not_found       = true
  response_export_values = []
}

# Its metadata only: reading a secret's value takes Key Vault's data plane, which nothing here touches.
data "azapi_resource" "app_key" {
  count = local.self_hosted ? 1 : 0

  type                   = "Microsoft.KeyVault/vaults/secrets@2026-02-01"
  resource_id            = local.app_key_secret
  ignore_not_found       = true
  response_export_values = []
}

# The runner App's key and nothing else. Roles only, and none for anyone but the runners' identity, which reads
# that one secret. Its public endpoint admits no address: mlz install runners lets the uploader's in for the
# upload alone, and this apply takes any address left behind out again. Purge protection, since the key is the
# runners' only way into GitHub: a deleted vault stays recoverable for 90 days, and its name taken.
resource "azapi_resource" "vault" {
  count = local.vault ? 1 : 0

  type      = "Microsoft.KeyVault/vaults@2026-02-01"
  name      = local.names.key_vault
  parent_id = azapi_resource.resource_group.id
  location  = local.region
  tags      = local.tags
  body = {
    properties = {
      tenantId                  = local.tenant_id
      sku                       = { family = "A", name = "standard" }
      enableRbacAuthorization   = true
      enableSoftDelete          = true
      softDeleteRetentionInDays = 90
      enablePurgeProtection     = true
      publicNetworkAccess       = "Enabled"
      networkAcls = {
        defaultAction       = "Deny"
        bypass              = "None"
        ipRules             = []
        virtualNetworkRules = []
      }
    }
  }
  response_export_values = []

  depends_on = [azapi_resource_action.register]
}

resource "azapi_resource" "vault_endpoint" {
  count = local.vault ? 1 : 0

  type      = "Microsoft.Network/privateEndpoints@2024-07-01"
  name      = local.names.vault_endpoint
  parent_id = azapi_resource.resource_group.id
  location  = local.region
  tags      = local.tags
  body = {
    properties = {
      subnet                     = { id = local.state_endpoint_subnet }
      customNetworkInterfaceName = local.names.vault_endpoint_nic
      privateLinkServiceConnections = [{
        name       = "vault"
        properties = { privateLinkServiceId = azapi_resource.vault[0].id, groupIds = ["vault"] }
      }]
    }
  }
  response_export_values = []
}

resource "azapi_resource" "vault_endpoint_dns" {
  count = local.vault ? 1 : 0

  type      = "Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01"
  name      = "default"
  parent_id = azapi_resource.vault_endpoint[0].id
  body = {
    properties = {
      privateDnsZoneConfigs = [{ name = "vault", properties = { privateDnsZoneId = local.vault_zone } }]
    }
  }
}

# As the state endpoint's blob zone link (main.tf): the vault's name resolves from management's own network too.
resource "azapi_resource" "vault_zone_link" {
  count = local.vault ? 1 : 0

  type      = "Microsoft.Network/privateDnsZones/virtualNetworkLinks@2024-06-01"
  name      = local.names.management_network
  parent_id = local.vault_zone
  location  = "global"
  tags      = local.tags
  body = {
    properties = {
      virtualNetwork      = { id = local.management_network_id }
      registrationEnabled = false
    }
  }
  response_export_values = []
}

module "runners" {
  source = "git::https://github.com/fortytwoservices/az-tf-mlz-github-runners.git?ref=v0.1.0"
  count  = local.runner_pool ? 1 : 0

  name                        = local.names.runners
  parent_id                   = azapi_resource.resource_group.id
  location                    = local.region
  subnet_resource_id          = local.runners_subnet
  sku_name                    = local.runners.size
  instances                   = local.runners.instances
  image                       = local.runners.image # Fortytwo's runner image, or its dev plan for Fortytwo's own tests
  user_assigned_identity_name = local.names.runners_identity
  network_security_group_name = local.names.runners_nsg
  app_key_secret_resource_id  = local.app_key_secret
  # Into the runner group mlz that mlz install runners makes, with the label the pipelines' MLZ_RUNS_ON names.
  github = {
    scope         = local.github.organization
    app_client_id = local.runners.app_client_id
    labels        = ["mlz"]
    runner_group  = "mlz"
  }
  tags = local.tags

  # Each instance reads the key on its first boot, through the vault's private endpoint and its name in the zone.
  depends_on = [azapi_resource.vault_endpoint_dns, azapi_resource.vault_zone_link, azapi_resource_action.register]
}
