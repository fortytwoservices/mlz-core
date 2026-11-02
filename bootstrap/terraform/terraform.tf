terraform {
  required_version = ">= 1.16, < 2.0"

  required_providers {
    azapi = {
      source  = "Azure/azapi"
      version = "~> 2.13"
    }
    github = {
      source  = "integrations/github"
      version = "~> 6.6"
    }
  }

  # The bootstrap's own state lives in the account it creates, in the `bootstrap` container: pass the
  # account with -backend-config (docs/bootstrap.md). The first run, before the account exists, uses a
  # local backend through a git-ignored backend_override.tf.
  backend "azurerm" {
    use_azuread_auth = true
  }
}
