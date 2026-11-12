# Signs in with the Azure CLI locally and with OIDC (ARM_USE_OIDC) in CI. Every resource names its
# subscription through its parent ID, so the provider's default subscription does not matter.
provider "azapi" {}
