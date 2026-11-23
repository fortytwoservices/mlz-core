terraform {
  required_version = ">= 1.16, < 2.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.13"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.6"
    }
  }

  # The pipeline passes the storage account, container and key with -backend-config.
  backend "azurerm" {
    use_azuread_auth = true
  }
}
