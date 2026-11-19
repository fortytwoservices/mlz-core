# This landing zone's own resources, the team's to write. Name each from the customer's template, through mlz-core's
# naming helper, so it reads like the landing zone's own (rg-acme-pay-app-pd-nwe-01):
#
# module "app_group_name" {
#   source  = "git::https://github.com/fortytwoservices/mlz-core.git//modules/naming?ref=v0.13.0" # x-release-please-version
#   mlz     = var.mlz
#   type    = "resource_group"
#   purpose = "app"
# }
#
# resource "azapi_resource" "app_group" {
#   type      = "Microsoft.Resources/resourceGroups@2024-11-01"
#   name      = module.app_group_name.name
#   parent_id = "/subscriptions/${data.azapi_client_config.current.subscription_id}"
#   location  = var.mlz.location
# }
#
# The landing zone's subscription is the one the pipeline signs in to (the repo's AZURE_SUBSCRIPTION_ID):
# data "azapi_client_config" "current" {}
#
# A virtual machine in the landing zone's app subnet, with the landing zone's tags: what vending wrote is the easy path.
# The module's inputs: https://github.com/fortytwoservices/az-tf-mlz-virtual-machine (v0.1.0).
#
# locals {
#   subscription = "/subscriptions/${data.azapi_client_config.current.subscription_id}"
#   network_id   = "${local.subscription}/resourceGroups/${var.mlz.network.resource_group}/providers/Microsoft.Network/virtualNetworks/${var.mlz.network.virtual_network}"
#   subnet_ids   = { for key, subnet in var.mlz.network.subnets : key => "${local.network_id}/subnets/${subnet.name}" }
# }
#
# module "app_vm" {
#   source         = "git::https://github.com/fortytwoservices/az-tf-mlz-virtual-machine.git?ref=v0.1.0"
#   name           = "vm-${var.mlz.org}-${var.mlz.app}-app-${var.mlz.env}-${var.mlz.region}-01" # the naming helper has no VM type yet
#   parent_id      = azapi_resource.app_group.id
#   location       = var.mlz.location
#   tags           = var.mlz.tags
#   os_type        = "Linux"
#   size           = "Standard_D2s_v5"
#   admin_username = "azureops"
#   ssh_public_keys = ["ssh-ed25519 AAAA... ops@acme.example"] # the team's own key
#   image = { publisher = "Canonical", offer = "ubuntu-24_04-lts", sku = "server" }
#   os_disk = { name = "osdisk-${var.mlz.app}-app-01" }
#   network_interfaces = {
#     primary = { primary = true, ip_configurations = { ipv4 = { primary = true, subnet_resource_id = local.subnet_ids["app"] } } }
#   }
# }
