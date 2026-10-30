# Both providers sign in with the Azure CLI locally and with OIDC (ARM_USE_OIDC) in CI.

provider "azapi" {}

# Microsoft's library comes in through governance/lib's dependency on platform/alz. The rendered
# customer library goes last, so it can use the archetypes and assignments of the other two.
provider "alz" {
  library_references = [
    { custom_url = "${path.root}/../lib" },
    { custom_url = var.rendered_library_path },
  ]
}
