# Signs in with the Azure CLI locally and with OIDC (ARM_USE_OIDC) in CI. Every resource names its
# subscription through its parent ID, so the provider's default subscription does not matter.
provider "azapi" {}

# The landing zone's workload repo, in its GitHub organization, as the customer's MLZ App: the pipeline passes an
# installation token in GITHUB_TOKEN, read-only for plans, and the vending repo's organization in GITHUB_OWNER, the
# owner a landing zone without a repo falls back to. An installation token may not ask who it is (GET /user), which
# the provider would without an owner; a repo vending made and no longer makes is archived through that owner too.
provider "github" {
  owner = try(local.lz.github.organization, null)
}
