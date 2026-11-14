# This landing zone's own resources, the team's to write. Name each from the customer's template, through mlz-core's
# naming helper, so it reads like the landing zone's own (rg-acme-pay-app-pd-nwe-01):
#
# module "app_group_name" {
#   source  = "git::https://github.com/fortytwoservices/mlz-core.git//modules/naming?ref=v0.8.0" # x-release-please-version
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
