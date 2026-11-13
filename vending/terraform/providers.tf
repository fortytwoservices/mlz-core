# Signs in with the Azure CLI locally and with OIDC (ARM_USE_OIDC) in CI. Every resource names its
# subscription through its parent ID, so the provider's default subscription does not matter.
provider "azapi" {}

# The landing zone's workload repo, in its GitHub organization, as the customer's MLZ App: the pipeline passes an
# installation token in GITHUB_TOKEN, read-only for plans. Unused by a landing zone without github.organization.
provider "github" {
  owner = try(local.lz.github.organization, null)
}
