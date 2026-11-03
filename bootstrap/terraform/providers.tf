# Azure: the Azure CLI sign-in of the person running the bootstrap. Every resource names its subscription
# through its parent ID, so the provider's default subscription does not matter.
provider "azapi" {}

# GitHub: a token with admin rights on the customer's three repos, in GITHUB_TOKEN
# (for example GITHUB_TOKEN=$(gh auth token)).
provider "github" {
  owner = local.github.organization
}
