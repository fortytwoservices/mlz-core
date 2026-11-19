# The landing zone, as vending writes it to mlz.auto.tfvars.json and keeps it up to date. Do not edit that file.
variable "mlz" {
  type = object({
    landing_zone = string # payments-prod
    location     = string # norwayeast
    # Naming, for mlz-core's helper (modules/naming): the customer's code, the workload's short name, the codes of
    # the environment and the region, and the template.
    org       = string
    app       = string
    env       = string
    region    = string
    template  = string
    templates = map(string)
    # The landing zone's network, which vending owns: put network interfaces and private endpoints in its subnets,
    # change nothing else in it. By names: build an ID from the subscription the pipeline signs in to (main.tf).
    network = object({
      resource_group  = string  # rg-acme-pay-net-pd-nwe-01
      virtual_network = string  # vnet-acme-pay-pd-nwe-01
      address_space   = string  # 10.100.4.0/24
      subnets = map(object({    # by the name in the landing zone's file
        name           = string # snet-app
        address_prefix = string # 10.100.4.0/26
      }))
      dns_servers = list(string) # the hub's, which resolve Private Link names
    })
    tags            = map(string)  # the tags vending gives what it makes; give the team's resources the same
    resource_groups = map(string)  # vending's groups in the subscription, by what they hold: network, identity, state
    platform_owns   = list(string) # the groups vending owns: anything changed in them, its next apply changes back
  })
}
