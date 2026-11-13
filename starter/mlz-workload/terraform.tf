terraform {
  required_version = ">= 1.16, < 2.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13"
    }
  }

  # The landing zone's own state account: the pipeline passes it with -backend-config, from the repo's variables.
  backend "azurerm" {
    use_azuread_auth = true
  }
}

# Signs in with the Azure CLI locally and with OIDC (ARM_USE_OIDC) in the pipeline: as the plan identity for plans,
# and as the deploy identity in the apply environment.
provider "azapi" {}
