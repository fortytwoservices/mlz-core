terraform {
  required_version = ">= 1.16, < 2.0"

  required_providers {
    alz = {
      source  = "Azure/alz"
      version = "~> 0.22.0"
    }
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13"
    }
  }

  # The pipeline passes the storage account, container and key with -backend-config.
  backend "azurerm" {
    use_azuread_auth = true
  }
}
